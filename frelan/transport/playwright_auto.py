"""Transport Layer — Automated Playwright CDP Transport.

This transport automates interactions with the browser versions of ChatGPT, Gemini,
and Claude by connecting to an already running Google Chrome instance via CDP (Chrome
DevTools Protocol) on port 9223.

It bypasses bot detection and utilizes active, premium sessions completely for free.
It also includes an automatic fallback to manual CLI entry if automated extraction
fails or times out.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright

from frelan.enums import CheckpointDecision
from frelan.mission_contract import Participant
from frelan.transport.browser import BrowserTransport
from frelan.transport.adapters import get_adapter

_RULE = "=" * 70
_DASH = "-" * 70

# Placeholder embedded in prompt text for artifacts that can't be inlined;
# the real file still travels as a browser attachment.
_BINARY_PLACEHOLDER = "[Binary file attachment — automatically uploaded to browser]"
# Cap for inlining downloaded text artifacts into prompts.
_MAX_EMBED_CHARS = 20000

# Anchor texts that are UI labels, not filenames.
_GENERIC_LINK_LABELS = {"download", "download file", "save", "save file"}

# Extra ~seconds to hold a turn open for image/file generation after the text
# has stabilised, before giving up on the busy signal.
_ARTIFACT_GRACE_POLLS = 180
# NOTE: busy heuristics rot (Gemini kept aria-busy="true" forever once).
# With ZERO artifact evidence in the response, a lying busy signal should cost
# seconds, not minutes — the long grace is reserved for real image/file renders.
_NO_ARTIFACT_GRACE_POLLS = 15

# Never shrink the inline cap below this — a stub plus headers always fits.
_MIN_INLINE_CHARS = 3_000
# Roll over before the conversation budget is fully spent.
_ROLLOVER_MARGIN = 0.10
# Bound the limit-error recovery ladder so a mis-detected banner can't loop.
_MAX_LIMIT_RECOVERIES = 2
# Refresh-without-resend: a manual F5 reliably un-wedges a tab whose DOM has
# gone stale (response rendered but unharvestable, extraction erroring). After
# this many no-progress polls (~seconds), reload the tab WITHOUT re-delivering.
_STALLED_REFRESH_POLLS = 30
_MAX_STALLED_REFRESHES = 2
# Send verification: a force-click on a still-disabled send button silently
# sends NOTHING (observed live 2026-07-12: ChatGPT attachment processing
# outlasted the enable wait three turns in a row; each unsent turn was then
# misread as web lag and burned ~2 minutes of refreshes + re-delivery).
# After delivery, confirm the text actually left the composer and re-submit
# until it does.
_SEND_VERIFY_SECONDS = 90
# How long to wait for the send button to enable while uploads process.
_SEND_ENABLE_WAIT_SECONDS = 45


def _effective_inline_limit(base: int, cumulative: int, budget: int | None) -> int:
    """Shrink the inline cap as the conversation consumes its char budget.

    Platforms without a conversation budget (ChatGPT/Gemini — their composer
    limit is per-message) keep the static cap. Claude resends the whole history
    every turn, so the safe inline size shrinks proportionally as the chat grows.
    """
    if not budget:
        return base
    remaining_ratio = max(0.0, 1.0 - cumulative / budget)
    return max(_MIN_INLINE_CHARS, int(base * remaining_ratio))


def _needs_rollover(cumulative: int, next_size: int, budget: int | None) -> bool:
    """True when delivering ``next_size`` more chars would exhaust the budget."""
    if not budget:
        return False
    return cumulative + next_size > budget * (1 - _ROLLOVER_MARGIN)


def _degrade_to_inline(prompt: str, cap: int) -> str:
    """Head+tail slice for the attachment-failed last resort.

    Keeps the mission header/instructions (top) and the 'Your turn' directives
    (bottom); the middle — usually the embedded discussion — is dropped with an
    explicit marker. Honest degradation beats typing 100k+ chars into a
    composer and wedging the run.
    """
    marker = (
        "\n\n[...CONTEXT OMITTED: attachment delivery failed; the middle of "
        "this prompt was dropped to fit the composer. Respond using the "
        "instructions and the context that remains...]\n\n"
    )
    head = int(cap * 0.6)
    tail = max(0, cap - head - len(marker))
    return prompt[:head] + marker + prompt[len(prompt) - tail:]


# A stub delivery only works if the attachment actually arrived. When it did
# not, models answer with a refusal naming the file instead of doing the turn —
# and that refusal used to be recorded as the turn's response (observed live
# 2026-08-19: three ChatGPT turns lost this way, including the final synthesis,
# which wrote the refusal into recommendation.md). Detect it and re-deliver.
_ATTACHMENT_REFUSAL_NEEDLES = (
    "don't have access",
    "do not have access",
    "not available to me",
    "isn't available",
    "is not available",
    "not accessible",
    "cannot read",
    "can't read",
    "unable to read",
    "unable to access",
    "cannot access",
    "can't access",
    "please attach",
    "re-attach",
    "reattach",
)
# One re-delivery per turn. A model that refuses twice is not going to be
# argued into reading the file; the truncated inline prompt is the answer.
_MAX_SPILL_REDELIVERIES = 1
# A refusal is short. Past this the response is doing real work, even if it
# happens to mention the file, so it is a response and not a delivery failure.
_MAX_REFUSAL_CHARS = 3_000


def _looks_like_attachment_refusal(text: str, filename: str) -> bool:
    """True when ``text`` is the model saying it cannot read ``filename``.

    Deliberately narrow: the response must NAME the overflow file *and* express
    inability *and* be short. A genuine turn does not quote the transport's
    scratch filename, so this cannot swallow real work.
    """
    if not text or not filename:
        return False
    body = text.strip()
    if len(body) > _MAX_REFUSAL_CHARS:
        return False
    lowered = body.lower()
    if filename.lower() not in lowered:
        return False
    return any(n in lowered for n in _ATTACHMENT_REFUSAL_NEEDLES)


# Chunked inline delivery: the overflow path that needs no upload to work.
# Truncation loses context permanently; an attachment can fail silently. Sending
# the prompt as N sequential composer messages loses nothing and depends on
# nothing but the composer itself.
_MAX_PROMPT_CHUNKS = 6
_CHUNK_HEADER = (
    "[PROMPT PART {i} OF {n} — DO NOT RESPOND YET. This prompt is being "
    "delivered in {n} messages because it exceeds the composer limit. Read this "
    "part, reply with nothing, and wait for the part marked FINAL.]\n\n"
)
_CHUNK_FINAL_HEADER = (
    "[PROMPT PART {n} OF {n} — FINAL. The whole prompt has now been delivered "
    "across these {n} messages. Treat all {n} parts as one single message and "
    "respond to it now.]\n\n"
)


def _chunk_prompt(prompt: str, limit: int, max_chunks: int = _MAX_PROMPT_CHUNKS) -> list[str]:
    """Split ``prompt`` into ≤``max_chunks`` composer-sized messages, or [].

    Returns an empty list when the prompt cannot be delivered within the chunk
    budget — the caller then degrades to a truncated inline prompt. Splits on
    paragraph boundaries where possible so a part never ends mid-sentence.
    """
    if limit <= 0:
        return []
    # Every part carries a header; size the body against the worst-case header.
    overhead = max(
        len(_CHUNK_HEADER.format(i=max_chunks, n=max_chunks)),
        len(_CHUNK_FINAL_HEADER.format(n=max_chunks)),
    )
    body_cap = limit - overhead
    if body_cap <= 0:
        return []
    if -(-len(prompt) // body_cap) > max_chunks:  # ceil division
        return []

    bodies: list[str] = []
    rest = prompt
    while rest:
        if len(rest) <= body_cap:
            bodies.append(rest)
            break
        window = rest[:body_cap]
        cut = window.rfind("\n\n")
        if cut < body_cap // 2:  # no useful paragraph break — take a hard slice
            cut = body_cap
        bodies.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")

    if len(bodies) > max_chunks:
        return []
    total = len(bodies)
    chunks = []
    for i, body in enumerate(bodies, start=1):
        header = (
            _CHUNK_FINAL_HEADER.format(n=total)
            if i == total
            else _CHUNK_HEADER.format(i=i, n=total)
        )
        chunks.append(header + body)
    return chunks


def _render_overflow_stub(filename: str, participant_name: str) -> str:
    """The short inline message that replaces an over-limit prompt."""
    return (
        "Your full turn prompt could not be typed inline (composer length "
        f"limit). It is in the attached file `{filename}`.\n\n"
        f"{participant_name}: read the attached `{filename}` completely and "
        "respond according to its instructions, exactly as if its contents "
        "were this message."
    )


def _safe_artifact_name(name: str, url: str) -> str:
    """Derive a safe outputs/ filename for a browser-generated file artifact."""
    candidate = (name or "").strip()
    if candidate.lower() in _GENERIC_LINK_LABELS or len(candidate) > 80:
        candidate = ""
    if not candidate:
        candidate = Path(unquote(urlparse(url).path)).name
    candidate = re.sub(r"[^\w.\- ]", "_", candidate).strip("._ ")
    # data: URIs have no meaningful path — keep the tail so a real extension survives.
    candidate = candidate[-80:]
    if not candidate:
        candidate = f"artifact_{int(time.time())}"
    if "." not in candidate:
        candidate += ".bin"
    return candidate


def _is_web_lag(has_started: bool, busy: bool, elapsed: float, threshold_seconds: float) -> bool:
    """True when a turn is genuinely stuck and worth a reload.

    Requires BOTH no response text (``not has_started``) AND no active-generation
    signal (``not busy``) past the threshold. A busy page — Stop button visible or
    ``data-is-streaming`` present — is working, not lagging; models like Claude sit
    in thinking/queue for well over the threshold before their first token renders.
    Reloading a busy tab interrupts a real response and re-fires the prompt (duplicate
    submissions). NOTE: text-plus-busy heuristic; revisit only if a transport
    reports busy while truly hung.
    """
    return not has_started and not busy and elapsed > threshold_seconds


def _chip_needles(files: list[str]) -> list[str]:
    """Lowercased filename fragments expected to appear on attachment chips.

    Chips truncate long names, so match on the first 12 chars of the stem.
    """
    needles = []
    for f in files:
        stem = Path(f).stem.lower()
        needles.append(stem[:12] if len(stem) > 12 else stem)
    return needles


def _split_upload_state(desired: set[str], tracked: set[str]) -> tuple[list[str], set[str]]:
    """Reconcile the reference set against what was already uploaded.

    Returns ``(to_upload, stale)``: new paths to attach, and tracked paths no
    longer referenced (removed via the P menu / Clear all) that must be
    unloaded so a later re-add uploads them again.
    """
    return sorted(desired - tracked), tracked - desired


# innerText of the composer region (where attachment chips render), used to
# verify that uploads actually produced visible chips before trusting them.
_COMPOSER_TEXT_JS = '''() => {
    const anchors = [
        document.querySelector('#prompt-textarea'),
        document.querySelector('rich-textarea'),
        document.querySelector('div[contenteditable="true"]'),
    ];
    for (const el of anchors) {
        if (!el) continue;
        let scope = el.closest('form');
        if (!scope) {
            scope = el;
            for (let i = 0; i < 3 && scope.parentElement; i++) scope = scope.parentElement;
        }
        if (scope) return scope.innerText || '';
    }
    return '';
}'''

# The composer INPUT element itself (not the chip region): returns its current
# text, optionally focusing it first so a keyboard Enter lands there. Same
# anchors as _COMPOSER_TEXT_JS, most-specific first.
_COMPOSER_INPUT_JS = '''(focus) => {
    const el = document.querySelector('#prompt-textarea')
        || document.querySelector('rich-textarea div[contenteditable="true"]')
        || document.querySelector('rich-textarea')
        || document.querySelector('div[contenteditable="true"]');
    if (!el) return null;
    if (focus) el.focus();
    return (el.innerText || el.value || '').trim();
}'''


class PlaywrightAutomatedTransport:
    """Automates browser interactions via Playwright over an active Chrome CDP session."""

    def __init__(
        self,
        cdp_url: str = "http://localhost:9223",
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        timeout_seconds: int = 400,
        auto: bool = False,
        topic_override: str | None = None,
        prompt_inject: str | None = None,
        injected_files: dict[str, str] | None = None,
        injected_images: list[str] | None = None,
        limit_overrides: dict[str, dict[str, int]] | None = None,
        refresh_policy: dict[str, int] | None = None,
        artifact_dir: Path | str = Path("outputs"),
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self.cdp_url = cdp_url
        # Where overflow spills and downloaded artifacts are written. Supplied by
        # main.py so `-o` governs every file a run produces, not just the five
        # canonical ones.
        self._artifact_dir = Path(artifact_dir)
        self.timeout_seconds = timeout_seconds
        self.auto = auto
        # Per-engine {"max_inline_chars": ..., "chat_budget_chars": ...} from
        # mission metadata; consulted before the adapter defaults.
        self._limit_overrides = limit_overrides or {}
        # Approx. chars sent+received per participant conversation (rollover budget).
        self._conversation_chars: dict[str, int] = {}
        # Assistant-message count at delivery time; a later increase proves a
        # new reply exists even when its text collides with the baseline.
        self._baseline_msg_counts: dict[str, int] = {}
        # Refresh schema — ONE policy for every engine (equitable by
        # construction). A tab refresh fires only when nothing is progressing
        # (no new reply, not busy) and never re-delivers; the re-delivery
        # reload is reserved for messages that appear never to have been sent.
        # Mission metadata may override any field (see MISSION-CONTRACT.md §2).
        self._refresh_policy: dict[str, int] = {
            "stalled_refresh_seconds": _STALLED_REFRESH_POLLS,
            "max_refreshes_per_turn": _MAX_STALLED_REFRESHES,
            "lag_seconds": 35,
            "max_redeliveries_per_turn": 2,
            **(refresh_policy or {}),
        }

        # Fallback to manual transport if automation fails or is disconnected
        self._fallback_transport = BrowserTransport(
            input_fn=input_fn,
            output_fn=output_fn,
            auto=auto,
            topic_override=topic_override,
            prompt_inject=prompt_inject,
            injected_files=injected_files,
            injected_images=injected_images,
        )

        self._playwright = None
        self._browser = None
        self._connected = False
        self._last_responses: dict[str, str] = {}
        self._last_prompts: dict[str, str] = {}
        self._uploaded_attachments: dict[str, set[str]] = {}
        self._downloaded_urls: set[str] = set()
        # participant.id -> (full prompt, overflow filename) for a turn that was
        # delivered as a stub + attachment, and how many times it was re-sent.
        self._pending_spill: dict[str, tuple[str, str]] = {}
        self._spill_redeliveries: dict[str, int] = {}
        # Participants whose browser conversation lost its history (rollover).
        # Synced into the run context by the interpreter (_TRANSPORT_OVERRIDES)
        # so the renderer sends a full re-brief instead of a delta into a chat
        # that no longer remembers the discussion.
        self.context_reset: set[str] = set()

        self._output("Initializing Playwright CDP Automated Transport...")
        self._try_connect()

    def _try_connect(self) -> bool:
        """Attempt to connect to the Chrome instance via CDP."""
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
            self._connected = True
            self._output(f"Successfully connected to Chrome via CDP at {self.cdp_url}!")
            return True
        except Exception as exc:
            self._output(_RULE)
            self._output("WARNING: Could not connect to Chrome via CDP.")
            self._output(f"Error: {exc}")
            self._output("Make sure Chrome is running with remote debugging enabled:")
            self._output("  Start-Process \"chrome.exe\" -ArgumentList \"--remote-debugging-port=9223\"")
            self._output("The transport will run in HYBRID mode (falling back to CLI prompt copy/paste).")
            self._output(_RULE)
            self._connected = False
            return False

    def _get_page_for_participant(self, participant: Participant) -> any:
        """Find or open the page corresponding to the participant's execution engine."""
        if not self._connected:
            return None

        engine = participant.assigned_engine.execution_engine.lower()
        adapter = get_adapter(engine)

        if not adapter:
            self._output(f"Unknown engine '{engine}'. Falling back to manual entry.")
            return None

        domain = adapter.domain
        default_url = adapter.default_url

        try:
            # Search existing tabs across ALL contexts
            self._output(f"Searching {len(self._browser.contexts)} context(s) for '{domain}'...")

            for c_idx, context in enumerate(self._browser.contexts):
                self._output(f"Checking context {c_idx} (has {len(context.pages)} page(s)):")
                for p_idx, page in enumerate(context.pages):
                    self._output(f"  [{p_idx}] URL: {page.url}")
                    if domain in page.url:
                        self._output(f"Found active tab for {domain}! Bringing it to front.")
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        return page

            # If not found, see if we can reuse an empty/blank page in any context
            self._output(f"Tab for {domain} not found in any context. Opening/reusing a tab...")

            if self._browser.contexts:
                context = self._browser.contexts[0]
                # Find a blank/default page to reuse if possible
                for page in context.pages:
                    if "about:blank" in page.url or "chrome://" in page.url:
                        self._output("Reusing an existing blank tab.")
                        page.goto(default_url)
                        page.wait_for_load_state("domcontentloaded")
                        return page

                # Try to open a new page
                try:
                    self._output("Opening a new browser tab.")
                    page = context.new_page()
                    page.goto(default_url)
                    page.wait_for_load_state("domcontentloaded")
                    return page
                except Exception as e:
                    # If new_page fails, fall back to navigating the first page of the first context
                    if context.pages:
                        self._output("new_page failed; falling back to navigating the first available tab.")
                        page = context.pages[0]
                        page.goto(default_url)
                        page.wait_for_load_state("domcontentloaded")
                        return page
                    raise e
            else:
                raise Exception("No active browser contexts found.")
        except Exception as exc:
            self._output(f"Error finding/opening browser tab: {exc}")
            return None

    def deliver_prompt(self, participant: Participant, prompt: str) -> None:
        """Deliver the prompt to the participant's browser tab automatically."""
        self._last_prompts[participant.id] = prompt
        # The re-brief has been rendered and is about to be delivered, so the
        # reset is spent. Discarded here rather than in the renderer, which is
        # a pure function and must not mutate run state.
        self.context_reset.discard(participant.id)
        # A new turn starts with a clean spill record: the re-delivery budget is
        # per turn, not per mission. (_retry_failed_spill re-sends through the
        # adapter directly, so it never resets its own counter.)
        self._pending_spill.pop(participant.id, None)
        self._spill_redeliveries.pop(participant.id, None)
        engine = participant.assigned_engine.execution_engine.lower()
        adapter = get_adapter(engine)

        self._output(_RULE)
        self._output(f"AUTOMATED PROMPT FOR: {participant.display_name}")
        self._output(_RULE)

        page = self._get_page_for_participant(participant)
        if page is None or not adapter:
            self._output("Could not locate active browser tab or adapter. Falling back to manual delivery.")
            self._fallback_transport.deliver_prompt(participant, prompt)
            return

        try:
            self._output(f"Injecting prompt into {participant.display_name}...")

            max_inline, budget = self._limits_for(adapter)
            cumulative = self._conversation_chars.get(participant.id, 0)

            # A full conversation (Claude resends its history every turn) must
            # roll over BEFORE delivery, or the send is rejected outright.
            if _needs_rollover(cumulative, len(prompt), budget):
                self._rollover_conversation(page, participant, adapter)
                cumulative = 0

            # Baseline: remember the tab's current last assistant message so
            # collect_response never harvests a stale reply (e.g. old chat
            # history) and relays it to the other participant as if new. The
            # message COUNT is the stronger identity signal: after a lag
            # re-delivery the text baseline can equal the already-finished
            # response, but the count still proves whether a new reply landed.
            try:
                baseline = page.evaluate(adapter.extract_js)
                if isinstance(baseline, dict):
                    self._last_responses[participant.id] = (baseline.get("text") or "").strip()
                    count = baseline.get("msgCount")
                    if isinstance(count, int):
                        self._baseline_msg_counts[participant.id] = count
            except Exception:
                pass

            # Sync attachments (upload new reference files, unload removed ones)
            self._upload_attachments(page, participant, adapter)

            # Deliver — spilling to an .md attachment when the prompt exceeds
            # the platform's safe composer size.
            limit = _effective_inline_limit(max_inline, cumulative, budget)
            delivered = self._deliver_with_spill(page, participant, adapter, prompt, limit)

            page.wait_for_timeout(2000)  # Wait 2 seconds for submission to process and page state to transition
            self._ensure_sent(page, adapter, delivered)
            self._output("Prompt delivered successfully!")

            # A rejected send shows a length-limit banner instead of streaming;
            # recover by re-delivering as attachment (and rolling over if full).
            self._recover_from_limit_error(page, participant, adapter, prompt, delivered)

            # Count the full prompt (attachments consume context too) toward the
            # conversation budget.
            self._conversation_chars[participant.id] = (
                self._conversation_chars.get(participant.id, 0) + len(prompt)
            )

        except Exception as exc:
            self._output(f"Failed to deliver prompt automatically: {exc}")
            self._output("Falling back to manual clipboard/CLI delivery.")
            self._fallback_transport.deliver_prompt(participant, prompt)

    def collect_response(self, participant: Participant) -> str:
        """Wait for generation to complete and harvest the response automatically."""
        engine = participant.assigned_engine.execution_engine.lower()
        adapter = get_adapter(engine)
        page = self._get_page_for_participant(participant)

        if page is None or not adapter:
            return self._fallback_transport.collect_response(participant)

        try:
            self._output(f"Waiting for {participant.display_name} to finish responding (timeout: {self.timeout_seconds}s)...")

            previous_text = self._last_responses.get(participant.id, "").strip()

            start_time = time.time()
            last_length = -1
            stable_count = 0
            required_stable_counts = 4  # 4 seconds of no length change = generation complete

            lag_threshold_seconds = self._refresh_policy["lag_seconds"]
            reload_count = 0
            max_reloads = self._refresh_policy["max_redeliveries_per_turn"]
            stalled_polls = 0
            refresh_count = 0
            stalled_limit = self._refresh_policy["stalled_refresh_seconds"]
            max_refreshes = self._refresh_policy["max_refreshes_per_turn"]

            image_urls = []
            file_links = []

            while time.time() - start_time < self.timeout_seconds:
                page.wait_for_timeout(1000)

                busy = False
                try:
                    res = page.evaluate(adapter.extract_js)
                    if isinstance(res, dict):
                        current_text = res.get("text", "")
                        image_urls = res.get("imageUrls", [])
                        file_links = res.get("fileLinks", [])
                        busy = bool(res.get("busy", False))
                    else:
                        current_text = res
                        image_urls = []
                        file_links = []
                except Exception:
                    current_text = None
                    image_urls = []
                    file_links = []

                has_started = False
                current_text_stripped = ""
                if current_text:
                    current_text_stripped = current_text.strip()
                    if previous_text and current_text_stripped == previous_text:
                        has_started = False
                    elif current_text_stripped:
                        has_started = True

                # Text collision escape: after a lag re-delivery the baseline
                # can equal the finished response (observed live 2026-07-11 —
                # ChatGPT sat "waiting" on a complete reply). If the message
                # count grew past the delivery baseline, a new reply exists.
                if not has_started and current_text_stripped:
                    baseline_count = self._baseline_msg_counts.get(participant.id)
                    current_count = res.get("msgCount") if isinstance(res, dict) else None
                    if (
                        isinstance(baseline_count, int)
                        and isinstance(current_count, int)
                        and current_count > baseline_count
                    ):
                        has_started = True

                # Refresh-without-resend: the automated equivalent of the
                # manual F5 that reliably un-wedges a stale tab. Fires only
                # when nothing is progressing (no new reply, not busy) and,
                # unlike the lag path below, NEVER re-delivers the prompt —
                # so an already-sent message can't be duplicated.
                if has_started or busy:
                    stalled_polls = 0
                else:
                    stalled_polls += 1
                    if stalled_polls >= stalled_limit and refresh_count < max_refreshes:
                        # Human-only blockers (quota wall, login wall,
                        # verification challenge) are NOT fixable by
                        # reload/refresh — surface the reason and hand the turn
                        # to the human instead of burning recovery attempts.
                        # Verification challenges are never auto-solved.
                        try:
                            blocker = page.evaluate(adapter.blocker_js)
                        except Exception:
                            blocker = None
                        if blocker:
                            self._output(
                                f"\n[BLOCKED] {participant.display_name} shows a "
                                f"human-only blocker: {blocker!r}. Resolve it in the "
                                "browser (log in / wait out the quota / complete the "
                                "check), then paste the response manually."
                            )
                            return self._fallback_transport.collect_response(participant)
                        refresh_count += 1
                        self._output(
                            f"[REFRESH] No harvest progress on {participant.display_name} "
                            f"for ~{stalled_polls}s; refreshing the tab without re-delivering "
                            f"(attempt {refresh_count}/{max_refreshes})..."
                        )
                        try:
                            # A reload wipes composer chips; clear the tracker so
                            # the next sync re-uploads reference files if needed.
                            if participant.id in self._uploaded_attachments:
                                self._uploaded_attachments[participant.id].clear()
                            page.reload()
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(3000)
                        except Exception as refresh_exc:
                            self._output(f"Refresh failed: {refresh_exc}")
                        # Fresh lag window for the refreshed page; totals stay
                        # bounded by the refresh cap.
                        start_time = time.time()
                        stalled_polls = 0
                        continue

                # Web lag / response-stuck detection. A busy page (Stop button /
                # streaming node) is generating — don't reload it just because no
                # text has rendered yet, or we interrupt it and re-fire the prompt.
                elapsed = time.time() - start_time
                if _is_web_lag(has_started, busy, elapsed, lag_threshold_seconds):
                    # A rejected send looks exactly like lag (no text, not busy).
                    # If a length-limit banner is up, re-deliver as an attachment
                    # instead of reloading — a reload would just re-fail the send.
                    if reload_count < max_reloads and self._check_limit_error(page, adapter):
                        reload_count += 1
                        prompt = self._last_prompts.get(participant.id, "")
                        if prompt:
                            self._output(
                                f"\n[LIMIT ERROR] {participant.display_name} rejected the prompt "
                                "while we waited; re-delivering as an .md attachment "
                                f"(attempt {reload_count}/{max_reloads})..."
                            )
                            try:
                                self._clear_composer(page)
                                self._deliver_with_spill(page, participant, adapter, prompt, limit=0)
                            except Exception as spill_exc:
                                self._output(f"Re-delivery as attachment failed: {spill_exc}")
                        start_time = time.time()
                        last_length = -1
                        stable_count = 0
                        continue
                    if reload_count < max_reloads:
                        reload_count += 1
                        self._output(
                            f"\n[WARNING] Web lag detected on {participant.display_name} "
                            f"(no response started after {lag_threshold_seconds}s)."
                            f" Reloading tab and re-delivering prompt (attempt {reload_count}/{max_reloads})..."
                        )
                        try:
                            # Clear the attachment tracker for this participant because reloading the page wipes the input files!
                            if participant.id in self._uploaded_attachments:
                                self._uploaded_attachments[participant.id].clear()

                            # Reload and wait for initialization
                            page.reload()
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(3000)

                            # Re-deliver the prompt
                            prompt = self._last_prompts.get(participant.id, "")
                            if prompt:
                                self.deliver_prompt(participant, prompt)
                        except Exception as reload_exc:
                            self._output(f"Failed to reload or re-deliver prompt: {reload_exc}")

                        # Reset wait state and timers for this turn
                        start_time = time.time()
                        last_length = -1
                        stable_count = 0
                        continue
                    else:
                        self._output(f"\n[WARNING] Maximum reload attempts ({max_reloads}) reached for {participant.display_name}. Continuing wait...")

                # If we have text and it has actually started
                if has_started:
                    curr_len = len(current_text_stripped)
                    if curr_len == last_length:
                        stable_count += 1
                        if stable_count >= required_stable_counts:
                            # Text is stable — but do NOT hand the turn over while
                            # the model is still generating (stop button visible /
                            # image rendering / skeleton placeholder). The next
                            # participant must receive the reply WITH its artifacts.
                            # Grace-capped so a stuck busy signal can't wedge the run
                            # (polls are ~1s, so counts approximate seconds). Without
                            # any artifact evidence in the response, the grace is
                            # short — a lying busy probe costs seconds, not minutes.
                            grace = (
                                _ARTIFACT_GRACE_POLLS
                                if (image_urls or file_links)
                                else _NO_ARTIFACT_GRACE_POLLS
                            )
                            if busy and stable_count < required_stable_counts + grace:
                                if stable_count == required_stable_counts or stable_count % 15 == 0:
                                    self._output(
                                        f"Text is stable but {participant.display_name} is still "
                                        "generating (image/file rendering in progress). Waiting for "
                                        "all artifacts before ending the turn..."
                                    )
                                continue
                            if busy:
                                self._output(
                                    "Artifact wait exceeded the grace period; proceeding with "
                                    "whatever artifacts are currently available."
                                )
                            final_text = current_text_stripped

                            # The turn was delivered as a stub + attachment. If
                            # the model answers that it cannot read the file,
                            # the delivery failed — banking this as the response
                            # relays a refusal to the peers and, on a synthesis
                            # turn, writes it into the deliverable.
                            retry = self._retry_failed_spill(
                                page, participant, adapter, final_text
                            )
                            if retry is not None:
                                return retry
                            self._pending_spill.pop(participant.id, None)

                            self._last_responses[participant.id] = final_text
                            # Responses count toward the conversation budget too
                            # (the whole history is resent on budgeted platforms).
                            self._conversation_chars[participant.id] = (
                                self._conversation_chars.get(participant.id, 0) + curr_len
                            )
                            self._output(f"Harvested {curr_len} characters automatically.")

                            # Automatically harvest generated images and file artifacts
                            if image_urls:
                                self._download_and_register_images(page, participant, image_urls)
                            if file_links:
                                self._download_and_register_files(page, participant, file_links)

                            return final_text
                    else:
                        # Text is still changing (generating)
                        stable_count = 0
                        last_length = curr_len

            self._output("Response generation wait timed out or state could not be determined.")
            return self._fallback_transport.collect_response(participant)

        except Exception as exc:
            self._output(f"Failed to harvest response automatically: {exc}")
            self._output("Falling back to manual clipboard/CLI response entry.")
            return self._fallback_transport.collect_response(participant)

    @property
    def topic_override(self) -> str | None:
        return self._fallback_transport.topic_override

    @topic_override.setter
    def topic_override(self, value: str | None) -> None:
        self._fallback_transport.topic_override = value

    @property
    def prompt_inject(self) -> str | None:
        return self._fallback_transport.prompt_inject

    @prompt_inject.setter
    def prompt_inject(self, value: str | None) -> None:
        self._fallback_transport.prompt_inject = value

    @property
    def injected_files(self) -> dict[str, str] | None:
        return self._fallback_transport.injected_files

    @injected_files.setter
    def injected_files(self, value: dict[str, str] | None) -> None:
        self._fallback_transport.injected_files = value

    @property
    def injected_images(self) -> list[str] | None:
        return self._fallback_transport.injected_images

    @injected_images.setter
    def injected_images(self, value: list[str] | None) -> None:
        self._fallback_transport.injected_images = value

    def ask_checkpoint(self, summary: str) -> CheckpointDecision:
        """Checkpoints are governance interactions, so we always ask the user via CLI, or automate if configured."""
        decision = self._fallback_transport.ask_checkpoint(summary)
        if self._fallback_transport.auto:
            self.auto = True
        return decision

    def _limits_for(self, adapter) -> tuple[int, int | None]:
        """Resolve (max_inline_chars, chat_budget_chars) — mission-metadata
        overrides win over the adapter defaults."""
        overrides = self._limit_overrides.get(adapter.engine_key, {})
        return (
            overrides.get("max_inline_chars", adapter.max_inline_chars),
            overrides.get("chat_budget_chars", adapter.chat_budget_chars),
        )

    def _write_overflow_file(self, participant: Participant, prompt: str) -> Path:
        """Persist an over-limit prompt as an .md file under the run directory."""
        path = self._artifact_dir / f"prompt_overflow_{participant.id}_{int(time.time())}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
        return path

    def _attach_one_shot(self, page, adapter, path: str) -> bool:
        """Attach a single file for THIS message only (never via injected_files —
        the renderer inlines injected_files into every future prompt, which
        would compound each oversized prompt into the next).

        Verification is STRICT here, unlike the reference-file sync: an
        unverifiable chip counts as failure, so delivery degrades to a truncated
        inline prompt instead of a stub naming a file the model may not have.
        A truncated prompt loses context; a dead stub loses the whole turn.
        """
        try:
            before = self._composer_text(page)
            if self._set_files_on_hidden_input(page, adapter, [path]):
                if self._attachments_visible(page, [path], baseline=before) is True:
                    return True
                if getattr(adapter, "trust_unverified_uploads", False):
                    # Chip selectors can't see this engine's attachment UI but
                    # the upload path is proven to deliver (observed live on
                    # Claude 2026-07-11) — trust it instead of double-sending.
                    self._output(
                        "  Chip not visible, but this engine's uploads are "
                        "trusted-by-evidence; proceeding with the attachment."
                    )
                    return True
                self._output("  Hidden-input upload produced no visible chip; trying the attach menu...")
            if self._upload_via_attach_menu(page, adapter, [path]):
                return self._attachments_visible(page, [path], baseline=before) is True
        except Exception as e:
            self._output(f"Notice: overflow attachment upload failed: {e}")
        return False

    def _deliver_in_chunks(self, page, adapter, prompt: str, limit: int) -> str | None:
        """Send ``prompt`` as sequential composer messages; None if not possible.

        Returns the FINAL chunk, which is what the caller verifies as sent — the
        earlier parts are instructed to draw no response, so the model's reply
        belongs to the last message either way.
        """
        chunks = _chunk_prompt(prompt, limit)
        if not chunks:
            return None
        self._output(
            f"Attachment unavailable; delivering the prompt inline in "
            f"{len(chunks)} parts ({len(prompt)} chars, composer limit {limit})."
        )
        for i, chunk in enumerate(chunks, start=1):
            adapter.deliver_prompt(page, chunk, self._output, self._wait_until_enabled)
            # Let the send land (and any acknowledgement render) before the next
            # part: two composer writes in the same instant merge into one send.
            page.wait_for_timeout(2000)
            if i < len(chunks):
                self._ensure_sent(page, adapter, chunk)
                self._output(f"  Part {i}/{len(chunks)} delivered.")
        return chunks[-1]

    def _retry_failed_spill(
        self, page, participant: Participant, adapter, final_text: str
    ) -> str | None:
        """Re-deliver a stub turn the model could not read; None if not needed.

        Returns the replacement response when a re-delivery happened, so the
        caller hands back real work instead of the refusal. Bounded by
        ``_MAX_SPILL_REDELIVERIES`` — a model that refuses twice gets the
        truncated inline prompt and the turn stands on that.
        """
        pending = self._pending_spill.get(participant.id)
        if not pending:
            return None
        full_prompt, filename = pending
        if not _looks_like_attachment_refusal(final_text, filename):
            return None

        attempts = self._spill_redeliveries.get(participant.id, 0)
        if attempts >= _MAX_SPILL_REDELIVERIES:
            self._output(
                f"{participant.display_name} still reports it cannot read "
                f"'{filename}'. Keeping the response as-is; the full prompt "
                "remains on disk."
            )
            self._pending_spill.pop(participant.id, None)
            self._spill_redeliveries.pop(participant.id, None)
            return None

        self._spill_redeliveries[participant.id] = attempts + 1
        self._pending_spill.pop(participant.id, None)
        inline_cap, _ = self._limits_for(adapter)
        degraded = _degrade_to_inline(full_prompt, inline_cap)
        self._output(
            f"{participant.display_name} answered that it cannot read "
            f"'{filename}' — the attachment did not arrive. Re-delivering a "
            f"TRUNCATED inline prompt ({len(degraded)} of {len(full_prompt)} chars)."
        )

        # Re-baseline first: without this the refusal we just read counts as
        # the "new" reply and the re-delivery is harvested as already-complete.
        self._last_responses[participant.id] = final_text
        try:
            probe = page.evaluate(adapter.extract_js)
            if isinstance(probe, dict) and isinstance(probe.get("msgCount"), int):
                self._baseline_msg_counts[participant.id] = probe["msgCount"]
        except Exception:
            pass

        self._clear_composer(page)
        adapter.deliver_prompt(page, degraded, self._output, self._wait_until_enabled)
        page.wait_for_timeout(2000)
        self._ensure_sent(page, adapter, degraded)
        return self.collect_response(participant)

    def _composer_text(self, page) -> str:
        """Current composer-region text, or '' when it cannot be read."""
        try:
            return (page.evaluate(_COMPOSER_TEXT_JS) or "").lower()
        except Exception:
            return ""

    def _deliver_with_spill(self, page, participant: Participant, adapter, prompt: str, limit: int) -> str:
        """Deliver ``prompt``, spilling it to an .md attachment when over ``limit``.

        Returns the text actually typed into the composer (the full prompt, or
        the stub when spilled).
        """
        if len(prompt) <= limit:
            adapter.deliver_prompt(page, prompt, self._output, self._wait_until_enabled)
            return prompt

        overflow_path = self._write_overflow_file(participant, prompt)
        self._output(
            f"Prompt is {len(prompt)} chars (safe inline limit {limit}); "
            f"spilling to attachment '{overflow_path.name}'."
        )
        if not self._attach_one_shot(page, adapter, str(overflow_path.resolve())):
            # Nothing was uploaded, so no stub can work. Deliver the prompt as
            # several composer-sized messages instead: unlike truncation it
            # loses nothing, and unlike an attachment it cannot fail silently.
            chunked = self._deliver_in_chunks(page, adapter, prompt, limit)
            if chunked is not None:
                self._output(
                    f"Full prompt preserved at '{overflow_path.name}' "
                    "(also delivered inline, in parts)."
                )
                return chunked

            # A possibly-rejected inline send beats a silently dropped turn —
            # but only up to the composer's real capacity. Typing a 100k+ char
            # prompt inline wedges the run, so degrade to head+tail instead.
            inline_cap, _ = self._limits_for(adapter)
            if len(prompt) > inline_cap:
                degraded = _degrade_to_inline(prompt, inline_cap)
                self._output(
                    "Could not verify the overflow attachment; sending a TRUNCATED "
                    f"inline prompt ({len(degraded)} of {len(prompt)} chars) as a last resort. "
                    f"Full prompt preserved at '{overflow_path.name}'."
                )
                adapter.deliver_prompt(page, degraded, self._output, self._wait_until_enabled)
                return degraded
            self._output("Could not verify the overflow attachment; sending the full prompt inline as a last resort.")
            adapter.deliver_prompt(page, prompt, self._output, self._wait_until_enabled)
            return prompt

        # Record the spill so collect_response can tell a refusal-to-read from a
        # real answer and re-deliver instead of banking the refusal as the turn.
        self._pending_spill[participant.id] = (prompt, overflow_path.name)
        stub = _render_overflow_stub(overflow_path.name, participant.display_name)
        adapter.deliver_prompt(page, stub, self._output, self._wait_until_enabled)
        return stub

    def _check_limit_error(self, page, adapter) -> bool:
        """True when the page shows a visible length-limit rejection banner."""
        try:
            return bool(page.evaluate(adapter.limit_error_js))
        except Exception:
            return False

    def _clear_composer(self, page) -> None:
        """Best-effort wipe of composer text left behind by a rejected send, so
        a re-delivery doesn't append to the failed message."""
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(300)
        except Exception:
            pass

    def _recover_from_limit_error(self, page, participant: Participant, adapter, prompt: str, delivered: str) -> None:
        """Recovery ladder for a rejected send: inline -> attachment spill ->
        (budgeted platforms) fresh-chat rollover -> raise (manual fallback)."""
        attempts = 0
        while self._check_limit_error(page, adapter) and attempts < _MAX_LIMIT_RECOVERIES:
            attempts += 1
            _, budget = self._limits_for(adapter)
            self._clear_composer(page)
            if delivered == prompt:
                self._output(
                    f"[LIMIT ERROR] {participant.display_name} rejected the inline "
                    "prompt; re-delivering as an .md attachment..."
                )
            elif budget:
                self._output(
                    f"[LIMIT ERROR] {participant.display_name}'s conversation is "
                    "full; rolling over to a fresh chat..."
                )
                self._rollover_conversation(page, participant, adapter)
                self._upload_attachments(page, participant, adapter)
            else:
                break
            delivered = self._deliver_with_spill(page, participant, adapter, prompt, limit=0)
            page.wait_for_timeout(2000)
            self._ensure_sent(page, adapter, delivered)

        if self._check_limit_error(page, adapter):
            raise RuntimeError(
                "prompt rejected by a length-limit error after "
                f"{attempts} recovery attempt(s)"
            )

    def _rollover_conversation(self, page, participant: Participant, adapter) -> None:
        """Open a fresh chat and reset per-conversation state.

        Continuity is carried by the prompt itself (it embeds the recent
        discussion) plus the reference files, which re-upload automatically on
        the next _upload_attachments sync because the tracker is cleared.
        """
        self._output(
            f"[ROLLOVER] Opening a fresh {adapter.engine_key} chat for "
            f"{participant.display_name} (conversation length budget reached)..."
        )
        page.goto(adapter.new_chat_url)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)
        # The fresh chat remembers nothing: the next prompt for this
        # participant must re-brief in full rather than send a delta.
        self.context_reset.add(participant.id)
        self._uploaded_attachments.pop(participant.id, None)
        self._last_responses.pop(participant.id, None)
        self._conversation_chars[participant.id] = 0

    def _upload_attachments(self, page, participant: Participant, adapter) -> None:
        """Sync browser attachments with the current reference set.

        Uploads newly added reference files/images and unloads removed ones:
        stale paths leave the tracking set (so re-adding re-uploads them) and
        any pending composer chips for them are cleared best-effort.
        """
        tracked = self._uploaded_attachments.setdefault(participant.id, set())

        desired: set[str] = set()
        for path_str in [*(self.injected_files or {}), *(self.injected_images or [])]:
            p = Path(path_str)
            if p.is_file():
                desired.add(str(p.resolve()))

        files_to_upload, stale = _split_upload_state(desired, tracked)

        if stale:
            tracked.difference_update(stale)
            self._remove_composer_attachments(page, adapter, sorted(Path(s).name for s in stale))

        if not files_to_upload:
            return

        self._output(f"Attempting to automatically upload {len(files_to_upload)} new attachment(s) to {participant.display_name}...")
        success = False
        try:
            # Strategy A: set files directly on a hidden file input.
            if self._set_files_on_hidden_input(page, adapter, files_to_upload):
                # A non-throwing set_input_files is NOT proof: verify chips rendered.
                success = self._attachments_visible(page, files_to_upload) is not False
                if not success:
                    self._output("  Hidden-input upload produced no visible attachment chips; trying the attach menu...")

            # Strategy B: open the composer's "+" attach menu and feed the file chooser.
            if not success and adapter:
                if self._upload_via_attach_menu(page, adapter, files_to_upload):
                    success = self._attachments_visible(page, files_to_upload) is not False
        except Exception as e:
            self._output(f"Notice: Automated file uploading encountered an issue: {e}")

        if success:
            tracked.update(files_to_upload)
            self._output(f"Successfully attached {len(files_to_upload)} file(s) to {participant.display_name}!")
            page.wait_for_timeout(2000)  # Wait for upload previews to settle
        else:
            self._output(
                "Could not verify automated attachment upload. Files remain referenced "
                "in the prompt text and the upload will be retried next turn."
            )

    def _set_files_on_hidden_input(self, page, adapter, files: list[str]) -> bool:
        """Try to set the files on a chat file input directly; return True if one accepted them."""
        for selector in adapter.file_input_selectors:
            try:
                loc = page.locator(selector)
                count = loc.count()
                self._output(f"  Checking selector '{selector}' (found {count} match(es))...")
                if count > 0:
                    loc.first.set_input_files(files)
                    self._output(f"  Set {len(files)} file(s) via '{selector}'.")
                    return True
            except Exception as selector_exc:
                self._output(f"  Selector '{selector}' attempt encountered issue: {selector_exc}")
                continue
        return False

    def _dismiss_consent_dialog(self, page) -> bool:
        """Click through one-time acknowledgment dialogs that block uploads.

        Observed live 2026-07-11: Gemini's 'Creating content from images and
        files' dialog (Cancel/Agree) appears on first upload use and swallows
        the menu-item click, so the file chooser never opens. Scoped to
        visible dialogs only; returns True if something was dismissed.
        """
        for sel in (
            '[role="dialog"] button:has-text("Agree")',
            'mat-dialog-container button:has-text("Agree")',
            '[role="dialog"] button:has-text("Got it")',
        ):
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    self._output(f"  Dismissing blocking consent dialog via '{sel}'...")
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    def _upload_via_attach_menu(self, page, adapter, files: list[str]) -> bool:
        """Open the '+' attach menu and satisfy the OS file chooser it triggers.

        ChatGPT mounts its file input lazily behind this menu, so the direct
        hidden-input route can find nothing to fill; this path mirrors what a
        human does: + button -> 'Add photos & files' -> pick files.
        """
        menu = adapter.attach_menu
        if not menu:
            return False

        def _open_menu() -> bool:
            for open_sel in menu.get("open", []):
                try:
                    btn = page.locator(open_sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        self._output(f"  Opening attach menu via '{open_sel}'...")
                        btn.first.click(timeout=2000)
                        page.wait_for_timeout(1200)  # menu items render lazily
                        return True
                except Exception:
                    continue
            return False

        self._dismiss_consent_dialog(page)  # clear anything already blocking
        if not _open_menu():
            self._output("  Could not find an attach-menu button.")
            return False

        try:
            # Opening the menu may have mounted the hidden input — try it first.
            if self._set_files_on_hidden_input(page, adapter, files):
                page.keyboard.press("Escape")  # close the menu; files are queued
                return True

            for item_sel in menu.get("item", []):
                # Two attempts: a first-time consent dialog can swallow the
                # click; dismiss it, re-open the menu if needed, and retry.
                for attempt in (1, 2):
                    try:
                        item = page.locator(item_sel)
                        if item.count() == 0 or not item.first.is_visible():
                            break  # not this selector; try the next one
                        with page.expect_file_chooser(timeout=3000) as fc_info:
                            item.first.click(timeout=2000)
                        fc_info.value.set_files(files)
                        self._output(
                            f"  Attached {len(files)} file(s) via the attach menu ('{item_sel}')."
                        )
                        return True
                    except Exception as item_exc:
                        self._output(
                            f"  Attach item '{item_sel}' attempt {attempt} failed: {item_exc}"
                        )
                        if attempt == 2 or not self._dismiss_consent_dialog(page):
                            break  # nothing was blocking; move to the next selector
                        # The dismissal may have closed the menu — restore it.
                        try:
                            if page.locator(item_sel).count() == 0:
                                _open_menu()
                        except Exception:
                            pass
            # Silent no-match was invisible in real runs (a whole mission fell
            # back to truncated inline with no clue why) — say it loudly.
            self._output(
                "  Attach menu opened but no upload item matched any selector; "
                "the menu layout may have changed — re-probe recommended."
            )
        finally:
            # Never leave a dangling menu blocking the composer.
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    def _attachments_visible(
        self,
        page,
        files: list[str],
        wait_seconds: int = 10,
        baseline: str | None = None,
    ) -> bool | None:
        """Poll the composer for attachment chips naming the files.

        Returns True when every filename fragment shows up, False when the
        composer is readable but chips never appear, and None when the composer
        region cannot be located (unverifiable — treated as success upstream).

        ``baseline`` is the composer text captured BEFORE the upload. When
        supplied, a needle must appear *more often* than it already did, which
        is what distinguishes this file's chip from a leftover one. Overflow
        filenames all share the truncated head ``prompt_overf``
        (``_chip_needles`` cuts at 12 chars), so without this a stale chip from
        the previous turn satisfied verification for a file that never
        uploaded — and the stub went out pointing at nothing.
        """
        needles = _chip_needles(files)
        before = (baseline or "").lower()
        deadline = time.time() + wait_seconds
        scope_found = False
        while time.time() < deadline:
            try:
                text = (page.evaluate(_COMPOSER_TEXT_JS) or "").lower()
            except Exception:
                return None
            if text.strip():
                scope_found = True
                if baseline is None:
                    landed = all(n in text for n in needles)
                else:
                    landed = all(text.count(n) > before.count(n) for n in needles)
                if landed:
                    self._output("  Verified attachment chips are visible in the composer.")
                    return True
            page.wait_for_timeout(500)
        if not scope_found:
            self._output("  Composer region not found for chip verification; assuming upload succeeded.")
            return None
        self._output("  Attachment chips did not appear in the composer.")
        return False

    def _wait_until_enabled(self, page, locator, seconds: int = _SEND_ENABLE_WAIT_SECONDS) -> None:
        """Poll until the locator is enabled — send buttons stay disabled while
        attachment uploads are still processing, so sending early drops files."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if locator.is_enabled():
                    return
            except Exception:
                return
            page.wait_for_timeout(500)
        self._output(f"  Warning: send button did not enable within {seconds}s. Proceeding...")

    def _ensure_sent(self, page, adapter, delivered: str) -> None:
        """Confirm the delivered text actually left the composer.

        A force-click on a still-disabled send button (attachment upload
        still processing) reports success but sends nothing; the unsent turn
        then looks exactly like web lag and costs minutes of refresh +
        re-delivery. While the text is still sitting in the composer, keep
        re-submitting until the platform accepts it.
        """
        fingerprint = delivered.strip()[:60].lower()
        if not fingerprint:
            return
        deadline = time.time() + _SEND_VERIFY_SECONDS
        warned = False
        while time.time() < deadline:
            try:
                composer = page.evaluate(_COMPOSER_INPUT_JS, False)
            except Exception:
                return  # unverifiable — assume sent rather than double-send
            if composer is None or fingerprint not in composer.lower():
                return  # text left the composer: the message went out
            if self._check_limit_error(page, adapter):
                return  # rejected send; the limit-recovery ladder owns this
            if not warned:
                warned = True
                self._output(
                    "  Send did not go through (text still in composer — "
                    "upload likely still processing). Retrying until it sends..."
                )
            page.wait_for_timeout(2000)
            try:
                page.evaluate(_COMPOSER_INPUT_JS, True)  # refocus the input
                page.keyboard.press("Enter")
            except Exception:
                return
        self._output(
            f"  Warning: message never left the composer after {_SEND_VERIFY_SECONDS}s; "
            "the turn may not have been sent."
        )

    def _remove_composer_attachments(self, page, adapter, filenames: list[str]) -> None:
        """Best-effort unload: clear pending composer chips for removed reference files.

        Attachments already sent with earlier messages are part of the
        conversation history and cannot be unloaded; this only stops removed
        files from travelling with the upcoming prompt.
        """
        self._output(f"Unloading {len(filenames)} removed reference file(s) from the composer...")
        try:
            clicked = page.evaluate(adapter.remove_chips_js, filenames)
            for name in clicked:
                self._output(f"  [UNLOADED] Removed pending attachment chip: {name}")
            for name in filenames:
                if name not in clicked:
                    self._output(f"  [UNLOADED] '{name}' had no pending chip (already sent or never attached); it will no longer be attached to future prompts.")
            if clicked:
                page.wait_for_timeout(1000)  # let the composer settle after chip removal
        except Exception as e:
            self._output(f"Notice: composer attachment removal encountered an issue: {e}")

    def _download_file_from_page(self, page, url: str, target_path: any) -> bool:
        """Download a URL from the browser page using base64 fetch to bypass CORS and auth restrictions."""
        if not url:
            return False

        # Inline data: URIs (common for Gemini-generated images) already carry
        # the bytes — decode directly, no fetch needed.
        if url.startswith("data:"):
            if ";base64," not in url:
                return False
            import base64
            try:
                artifact_bytes = base64.b64decode(url.split(";base64,", 1)[1])
                p = Path(target_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(artifact_bytes)
                self._output(f"  [DOWNLOADED] Decoded inline data-URI artifact to: {p}")
                return True
            except Exception as e:
                self._output(f"Notice: data-URI decode failed: {e}")
                return False

        self._output(f"Downloading generated artifact from browser: {url}...")
        try:
            # JS to fetch image and convert to base64 data URL
            fetch_js = """async (url) => {
                const res = await fetch(url);
                const blob = await res.blob();
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }"""

            data_url = page.evaluate(fetch_js, url)
            if data_url and ";base64," in data_url:
                import base64
                header, base64_data = data_url.split(";base64,", 1)
                artifact_bytes = base64.b64decode(base64_data)

                p = Path(target_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(artifact_bytes)
                self._output(f"  [DOWNLOADED] Saved browser-generated artifact to: {p}")
                return True
        except Exception as e:
            self._output(f"  In-page fetch failed ({e}); trying browser-context request...")

        # Fallback: Playwright's request API shares the browser's cookies but is
        # not a page-context fetch, so cross-origin hosts (e.g. Gemini images on
        # googleusercontent.com) can't refuse it via CORS. blob: URLs excluded —
        # they only exist inside the page.
        if url.startswith("http"):
            try:
                resp = page.context.request.get(url)
                if resp.ok:
                    p = Path(target_path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(resp.body())
                    self._output(f"  [DOWNLOADED] Saved artifact via browser-context request: {p}")
                    return True
                self._output(f"Notice: artifact download got HTTP {resp.status} for {url}")
            except Exception as e:
                self._output(f"Notice: artifact download failed: {e}")
        return False

    def _download_and_register_images(self, page, participant: Participant, urls: list[str]) -> None:
        """Download multiple generated images from the page and register them into injected_images."""
        for idx, url in enumerate(urls, 1):
            if not url or url in self._downloaded_urls:
                continue

            timestamp = int(time.time())
            filename = f"generated_{participant.id}_turn_{timestamp}_{idx}.png"
            target_path = self._artifact_dir / filename

            success = self._download_file_from_page(page, url, target_path)
            if success:
                self._downloaded_urls.add(url)
                # Add to injected_images for immediate propagation to subsequent turns
                if self.injected_images is None:
                    self.injected_images = []
                abs_path_str = str(target_path.resolve())
                if abs_path_str not in self.injected_images:
                    self.injected_images.append(abs_path_str)
                    # The creator already has this image in its conversation —
                    # only the other participant(s) need the upload.
                    self._uploaded_attachments.setdefault(participant.id, set()).add(abs_path_str)
                    self._output(f"  [IMAGE PROPAGATION] Registered '{target_path.name}' to Reference Images for upcoming turns!")

    def _download_and_register_files(self, page, participant: Participant, links: list[dict]) -> None:
        """Download file artifacts a participant generated and share them with the others.

        Each downloaded file is saved under outputs/, registered as a reference
        file (so it appears in every subsequent prompt), and pre-marked as
        uploaded for its creator — so the next deliver_prompt attaches it only
        to the *other* participant(s).
        """
        for link in links:
            url = (link.get("url") or "").strip()
            if not url or url in self._downloaded_urls:
                continue

            filename = _safe_artifact_name(link.get("name") or "", url)
            target_path = self._artifact_dir / filename
            if target_path.exists():
                target_path = target_path.with_name(f"{int(time.time())}_{filename}")

            if not self._download_file_from_page(page, url, target_path):
                continue
            self._downloaded_urls.add(url)

            # Inline small text artifacts into prompts; binaries ride as attachments only.
            content = _BINARY_PLACEHOLDER
            try:
                data = target_path.read_bytes()
                if b"\x00" not in data[:1024]:
                    text = data.decode("utf-8")
                    if len(text) <= _MAX_EMBED_CHARS:
                        content = text
            except (UnicodeDecodeError, OSError):
                pass

            if self.injected_files is None:
                self.injected_files = {}
            rel_path = str(target_path)
            self.injected_files[rel_path] = content

            # The creator already has this file in its conversation — only the
            # other participant(s) need the upload.
            abs_path = str(target_path.resolve())
            self._uploaded_attachments.setdefault(participant.id, set()).add(abs_path)
            self._output(
                f"  [FILE PROPAGATION] {participant.display_name} generated '{target_path.name}'; "
                "registered as a reference file for the other participant's upcoming turns!"
            )

    def close(self) -> None:
        """Clean up the Playwright resources."""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
