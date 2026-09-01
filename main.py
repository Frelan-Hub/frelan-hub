"""Entry point — wire the four layers together and run one mission.

Responsibilities (and nothing more):
  1. load + validate the Mission Contract,
  2. create the Mission Instance and ledger,
  3. run the interpreter over the Browser transport,
  4. save the outputs as Markdown (+ runtime metadata as JSON).

This is the *only* module that names a concrete transport. Swapping to a future
API/MCP transport is a one-line change here; nothing else moves.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import replace
from frelan.deliverables import split_outputs
from frelan.discovery import build_profile, count_items, write_profile
from frelan.enums import LedgerEntryType, RuntimeStatus
from frelan.evidence import collect_evidence
from frelan.ledger import Ledger
from frelan.mission_contract import Mission, Participant, AssignedEngine
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import MissionInterpreter
from frelan.mission_loader import MissionValidationError, load_mission
from frelan.report import load_log, render_report
from frelan.transport.browser import (
    BrowserTransport,
    looks_like_local_path,
    split_path_list,
)

DEFAULT_MISSION = Path("missions/frelan_debate.yaml")
# The parent of the per-run directories, and the home of the resume pointer.
# Not itself a run directory: a run writes to outputs/run-<UTC-timestamp>/ so
# history survives for evidence comparison instead of being overwritten
# (MISSION-LIBRARY-RESOLUTION.md §7 change #3).
DEFAULT_OUTPUT_DIR = Path("outputs")
RUN_DIR_PREFIX = "run-"
# Stable pointer to the most recent run, so `--resume` with no `-o` can still
# find it once the default directory stopped being a fixed path.
LAST_RUN_POINTER = ".last-run"
# Transport scratch: over-limit prompts spilled to attachments. Pruned only on
# explicit request (--prune-spills) — a run directory is evidence, never
# garbage-collected behind the Founder's back.
SPILL_GLOB = "prompt_overflow_*.md"

# Capability Discovery — the optional, read-only pre-planning stage (--discover).
# The contract lives in the "pre-planning" SUBdirectory on purpose: that folder
# is in _EXCLUDED_MISSION_DIRS, so discovery never appears in the meeting-type
# menu. It is a stage, not a meeting type.
DISCOVERY_MISSION = Path("missions/pre-planning/capability_discovery.yaml")
# NOTE: one profile at the project root, overwritten per discovery run —
# root because outputs/ is wiped every run and this artifact is meant to outlive
# it. Add --profile-out / per-project naming when more than one project is
# discovered from this checkout.
PROFILE_PATH = Path("capability-profile.yaml")
# Cumulative evidence lives at the project root, NOT in outputs/ (which is
# overwritten every run) — one JSON line per mission, so model performance per
# meeting type accumulates across runs.
EVIDENCE_LOG = Path("evidence-log.jsonl")

MISSIONS_DIR = Path("missions")
# Where a Founder-authored meeting type goes. Nothing in the runtime is special
# about this folder — it is discovered exactly like the shipped library, and is
# named only so the menu can tag its entries and the docs can point somewhere.
CUSTOM_MISSIONS_DIR = MISSIONS_DIR / "custom"
# Dev fixtures — runnable, but not library meeting types
# (MISSION-LIBRARY-RESOLUTION.md §6). Excluded from the numbered menu.
_FIXTURE_FILES = frozenset({"frelan_debate.yaml", "frelan_mission_contract_v2.yaml"})
# Subdirectories of missions/ that hold stages or reference material, not
# meeting types. Everything else one level down is a category folder and its
# contracts DO appear in the menu, tagged with the folder name.
_EXCLUDED_MISSION_DIRS = frozenset({"pre-planning"})


def _discover_meeting_types(
    missions_dir: Path = MISSIONS_DIR,
) -> list[tuple[str, Path, str]]:
    """Scan missions/ for runnable templates -> ``[(label, path, group)]``.

    The menu is built from the filesystem, not a hard-coded table
    (MISSION-LIBRARY-RESOLUTION.md §7 change #1): adding a meeting type is
    dropping a ``.yaml`` in ``missions/`` (or in ``missions/custom/`` for one of
    your own) — config over code, with no contract generator anywhere in the
    runtime (FORMATS.md §B.10). Each contract is read through the real loader,
    so a malformed file is skipped rather than crashing the menu.

    Scanning goes one level deep so category folders (``custom/``,
    ``candidates/``, ``shape/`` …) are listed; the folder name becomes the group
    tag unless the contract declares ``metadata.group`` itself. Folders in
    ``_EXCLUDED_MISSION_DIRS`` hold stages, not meeting types, and are skipped.
    Dev fixtures are excluded from the library listing (§6).
    """
    found: list[tuple[str, Path, str]] = []
    for path in sorted(missions_dir.glob("*.y*ml")):
        if path.name in _FIXTURE_FILES:
            continue
        entry = _meeting_type_entry(path, folder_group="")
        if entry:
            found.append(entry)
    for folder in sorted(p for p in missions_dir.iterdir() if p.is_dir()):
        if folder.name in _EXCLUDED_MISSION_DIRS:
            continue
        for path in sorted(folder.glob("*.y*ml")):
            entry = _meeting_type_entry(path, folder_group=folder.name)
            if entry:
                found.append(entry)
    # Ungrouped library entries first, then each folder's, alphabetically.
    found.sort(key=lambda t: (t[2], t[0]))
    return found


def _meeting_type_entry(
    path: Path, *, folder_group: str
) -> tuple[str, Path, str] | None:
    """``(label, path, group)`` for one contract file, or None if unrunnable."""
    try:
        mission = load_mission(path)
    except MissionValidationError:
        return None  # not a runnable contract; leave it out, don't halt
    return (mission.name, path, mission.metadata.get("group") or folder_group)


def _meeting_type_brief(path: Path) -> dict[str, str]:
    """What one meeting type is *for*, for display beside the menu.

    ``metadata.summary`` says when to reach for it; ``objective`` states the
    goal it drives at; ``metadata.format`` names its phase skeleton. All three
    are read from the contract, so a new template documents itself and there is
    no description table anywhere in the runtime to keep in sync
    (MISSION-LIBRARY-RESOLUTION.md §8.4). Returns ``{}`` for a contract that
    does not load — the same silence the menu scan gives it.
    """
    try:
        mission = load_mission(path)
    except MissionValidationError:
        return {}
    # Folded YAML scalars keep a trailing newline; collapse the whitespace.
    return {
        "summary": " ".join(mission.metadata.get("summary", "").split()),
        "objective": " ".join(mission.objective.split()),
        "format": " ".join(mission.metadata.get("format", "").split()),
    }


def _prompt_meeting_type(
    input_fn=input, *, ask_claude: bool = True, missions_dir: Path = MISSIONS_DIR
) -> tuple[Path | None, bool]:
    """Interactive startup menu: pick a meeting type and optionally Claude.

    Returns (mission_path or None to keep the default, include_claude).
    A number picks a discovered meeting type; anything else is treated as a
    path to a mission contract, so a one-off custom meeting type can be run
    without restarting. Any EOF/interrupt keeps the defaults — same graceful
    pattern as the topic-injection prompts.
    """
    try:
        templates = _discover_meeting_types(missions_dir)
        print("\n" + "=" * 70)
        print("                AI-CONDUCTOR B RUNTIME — MEETING TYPE")
        print("=" * 70)
        for i, (label, _path, group) in enumerate(templates, start=1):
            tag = f"[{group}] " if group else ""
            print(f" {i}) {tag}{label}")
        print("(Press Enter to keep the default legacy debate)")
        print(
            f"(Custom meeting type: type its path, or drop the .yaml in "
            f"{CUSTOM_MISSIONS_DIR.as_posix()}/ to list it above)\n"
        )
        choice = input_fn(f"Meeting Type [1-{len(templates)} or path]:\n> ").strip()
        path: Path | None = None
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(templates):
                path = templates[index][1]
                print(f"[SUCCESS] Meeting type set to: {templates[index][0]}")
                # Listing the summaries above would bury the menu; showing the
                # chosen one here confirms the pick was the intended one.
                summary = _meeting_type_brief(path).get("summary", "")
                if summary:
                    print(textwrap.fill(summary, width=70, initial_indent="          ",
                                        subsequent_indent="          "))
        elif choice:
            path = _custom_meeting_path(choice)

        include_claude = False
        # Asking about a peer the chosen contract forbids would invite an answer
        # the runtime then has to refuse. Say so instead of offering it.
        if ask_claude and not _claude_peer_supported_at(path or DEFAULT_MISSION):
            print(
                "\n[NOTE] This meeting type does not support a third peer: the "
                "injection\n       would add Claude to every phase and collapse "
                "the roles it separates."
            )
            ask_claude = False
        if ask_claude:
            answer = input_fn("\nInclude Claude as a third peer? [y/N]\n> ").strip().lower()
            include_claude = answer in ("y", "yes")
            if include_claude:
                print("[SUCCESS] Claude will join every phase after ChatGPT and Gemini.")
        return path, include_claude
    except (EOFError, KeyboardInterrupt):
        return None, False


def _custom_meeting_path(raw: str) -> Path | None:
    """Validate a Founder-typed mission path -> the path, or None to keep default.

    The file is loaded through the real loader here rather than deferred to the
    launch path, so a typo or a malformed contract is reported at the menu (with
    the loader's own error list) instead of aborting the run several prompts
    later. Bare names are also looked up under ``missions/custom/``, which is
    where a Founder-authored meeting type is meant to live.
    """
    candidates = [Path(raw.strip().strip('"').strip("'"))]
    if not candidates[0].suffix:
        candidates = [candidates[0].with_suffix(ext) for ext in (".yaml", ".yml")]
    candidates += [
        CUSTOM_MISSIONS_DIR / c for c in list(candidates) if not c.is_absolute()
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            mission = load_mission(candidate)
        except MissionValidationError as exc:
            print(f"[WARNING] {candidate} is not a runnable contract:\n{exc}")
            print("[WARNING] Keeping the default meeting type.")
            return None
        print(f"[SUCCESS] Custom meeting type set to: {mission.name} ({candidate})")
        return candidate
    print(f"[WARNING] No mission contract found at {raw!r} — keeping the default.")
    return None


def is_binary_file(filepath: Path) -> bool:
    """Detect if a file is binary by checking for null bytes in its first kilobyte."""
    try:
        with open(filepath, "rb") as f:
            return b"\x00" in f.read(1024)
    except Exception:
        return True


def _limit_overrides(mission: Mission) -> dict[str, dict[str, int]]:
    """Parse per-engine composer-limit overrides from mission metadata.

    Recognised keys: ``<engine>_max_inline_chars`` and
    ``<engine>_chat_budget_chars`` (values are integer strings). Invalid values
    warn and fall back to the adapter defaults — never halt the mission.
    """
    overrides: dict[str, dict[str, int]] = {}
    for key, raw in mission.metadata.items():
        for suffix in ("max_inline_chars", "chat_budget_chars"):
            if key.endswith("_" + suffix):
                engine = key[: -(len(suffix) + 1)]
                try:
                    overrides.setdefault(engine, {})[suffix] = int(raw)
                except (TypeError, ValueError):
                    print(f"[WARNING] Ignoring invalid limit override {key}={raw!r} (not an integer).")
    return overrides


def _new_run_dir(root: Path, now: datetime | None = None) -> Path:
    """An unused ``run-<UTC-timestamp>`` directory under ``root``."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / f"{RUN_DIR_PREFIX}{stamp}"
    # Two runs started inside the same second must not share a directory.
    suffix = 2
    while candidate.exists():
        candidate = root / f"{RUN_DIR_PREFIX}{stamp}-{suffix}"
        suffix += 1
    return candidate


def _resolve_run_dir(args: argparse.Namespace, root: Path | None = None) -> Path:
    """Decide which directory this run reads from and writes to.

    Three cases, in precedence order:

    - **explicit ``-o``** — used verbatim, for both fresh runs and resumes. This
      is what keeps the Streamlit UI and any ``.bat`` launcher working unchanged.
    - **default + ``--resume``** — read the ``.last-run`` pointer. Failing loud
      here matters: the alternative is resuming into an empty fresh directory and
      silently re-running a mission that was already half-finished.
    - **default + fresh run** — a new timestamped directory, recorded in the
      pointer so the next bare ``--resume`` can find it.
    """
    if args.output_dir is not None:
        return Path(args.output_dir)

    # Resolved here, not as a parameter default, so the root stays a single
    # late-bound source of truth rather than a value frozen at import time.
    root = DEFAULT_OUTPUT_DIR if root is None else root
    pointer = root / LAST_RUN_POINTER
    if args.resume:
        if not pointer.exists():
            raise SystemExit(
                f"[ERROR] Nothing to resume: no run pointer at {pointer}. "
                "Pass -o <dir> to resume a specific run directory."
            )
        target = Path(pointer.read_text(encoding="utf-8").strip())
        if not target.is_dir():
            raise SystemExit(
                f"[ERROR] The last run directory recorded in {pointer} no longer "
                f"exists: {target}. Pass -o <dir> to resume a specific run."
            )
        return target

    run_dir = _new_run_dir(root)
    run_dir.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(run_dir), encoding="utf-8")
    return run_dir


def _prune_spills(root: Path, days: int) -> tuple[int, int]:
    """Delete transport spill files older than ``days``; return (files, bytes).

    Deliberately narrow: it matches only the overflow-prompt scratch pattern and
    never touches ledgers, recommendations, metadata, evidence, or harvested
    artifacts. Those are the run's evidence and are not disposable.
    """
    cutoff = time.time() - days * 86_400
    removed = freed = 0
    for path in sorted(root.rglob(SPILL_GLOB)):
        try:
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError as exc:
            print(f"[WARNING] Could not remove {path}: {exc}")
            continue
        removed += 1
        freed += stat.st_size
    return removed, freed


def _write_ledger_meta(path: Path, meta: dict) -> None:
    """Start a fresh ledger.jsonl whose first line records how to resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"_meta": meta}) + "\n", encoding="utf-8")


def _load_resume_file(path: Path) -> tuple[dict, list[dict]]:
    """Read a persisted ledger.jsonl -> (meta, entry dicts). Fail loud, not late."""
    if not path.exists():
        raise SystemExit(
            f"[ERROR] Nothing to resume: {path} not found. "
            "A resumable record is written automatically by every run."
        )
    meta: dict = {}
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            print(f"[WARNING] Skipping corrupt ledger line: {line[:80]!r}")
            continue
        if "_meta" in data:
            meta = data["_meta"] or {}
        else:
            entries.append(data)
    return meta, entries


def _replay_entries(instance: MissionInstance, entries: list[dict]) -> None:
    """Rebuild the ledger AND the execution pointer from persisted entries.

    Every phase-bound RESPONSE is exactly one completed turn, so replaying them
    through the same state machine the interpreter drives (advance_turn ->
    phase-complete -> advance_phase) lands the pointer precisely where the
    mission stopped. Completed rounds are never re-run.
    """
    for data in entries:
        entry = instance.ledger.restore_entry(data)
        if entry.entry_type is not LedgerEntryType.RESPONSE:
            continue
        if entry.role == "synthesiser" or entry.phase_id is None:
            continue  # synthesis is terminal output, not a turn
        if entry.participant_id:
            instance.context[entry.participant_id] = entry.content
        if instance.advance_turn():
            if instance.is_round_cap_reached():
                instance.set_status(RuntimeStatus.COMPLETED)
                return
            if instance.is_phase_complete() and not instance.advance_phase():
                instance.set_status(RuntimeStatus.COMPLETED)
                return


def _restore_injected_context(instance: MissionInstance, meta: dict) -> None:
    """Re-read startup reference files/images recorded in the resume meta."""
    files: dict[str, str] = {}
    for path_str in meta.get("injected_files", []):
        p = Path(path_str)
        if not p.is_file():
            print(f"[WARNING] Resume: reference file missing, skipped: {path_str}")
            continue
        if is_binary_file(p):
            files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
        else:
            try:
                files[path_str] = p.read_text(encoding="utf-8")
            except Exception:
                files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
    if files:
        instance.context["injected_files"] = files
    images = list(meta.get("injected_images", []))
    if images:
        instance.context["injected_images"] = images


# Mission-metadata keys -> transport refresh-policy fields. One schema for
# every engine; a mission tunes WHEN refreshes fire, never per-engine favorites.
_REFRESH_KEYS = {
    "refresh_stalled_seconds": "stalled_refresh_seconds",
    "refresh_max_per_turn": "max_refreshes_per_turn",
    "refresh_lag_seconds": "lag_seconds",
    "refresh_max_redeliveries": "max_redeliveries_per_turn",
}


def _refresh_policy(mission: Mission) -> dict[str, int]:
    """Parse the engine-agnostic refresh schema from mission metadata.

    Invalid values warn and fall back to the transport defaults — never halt.
    """
    policy: dict[str, int] = {}
    for key, field in _REFRESH_KEYS.items():
        raw = mission.metadata.get(key)
        if raw is None:
            continue
        try:
            policy[field] = int(raw)
        except (TypeError, ValueError):
            print(f"[WARNING] Ignoring invalid refresh policy {key}={raw!r} (not an integer).")
    return policy


_PEER_NOTE = (
    "\n\nNote: Treat previous outputs from other engines as peer analysis, "
    "not authoritative conclusions."
)

#: A contract declares ``metadata.claude_peer: "unsupported"`` when injecting a
#: third peer would break the thing the template exists to do. The asymmetric
#: Red/Blue review is the worked example: ``_inject_claude_peer`` appends a peer
#: to EVERY phase, so Claude would argue both the attack and the defence — the
#: one outcome the duty split exists to prevent.
#:
#: The restriction is metadata rather than a mission id on purpose. A YAML
#: comment is stripped by the loader and never reaches the Founder, and a
#: hard-coded ``red_blue_review`` would not protect the next template that needs
#: the same guarantee. Any contract can now declare it, and the runtime and the
#: dashboard both read the declaration (MISSION-CONTRACT.md §2).
CLAUDE_PEER_UNSUPPORTED = "unsupported"

_CLAUDE_PEER_REFUSED = (
    "[REFUSED] '{name}' declares metadata.claude_peer: {value} — this meeting "
    "type cannot seat Claude as an extra peer, because the injection adds a "
    "peer to every phase and that would undo the contract's role separation.\n"
    "          Continuing WITHOUT Claude. Use a symmetric meeting type "
    "(missions/candidates/document_review.yaml) if you want three peers."
)


def claude_peer_supported(mission: Mission) -> bool:
    """Whether ``mission`` permits Claude to be injected as an extra peer.

    A read-only reading of declared contract data — the loader coerces metadata
    values to strings, so the comparison is against the string, case- and
    whitespace-insensitively. A contract that says nothing permits the peer,
    which is what every existing contract means.
    """
    declared = str(mission.metadata.get("claude_peer", "")).strip().lower()
    return declared != CLAUDE_PEER_UNSUPPORTED


def _claude_peer_supported_at(path: Path) -> bool:
    """``claude_peer_supported`` for a contract that is not loaded yet.

    Permissive when the file will not load: the menu is not the place to report
    a broken contract, and ``_inject_claude_peer`` gates the run regardless.
    """
    try:
        return claude_peer_supported(load_mission(path))
    except (MissionValidationError, OSError):
        return True


def _inject_claude_peer(mission: Mission) -> Mission:
    """Inject Claude as a full peer into every phase, appended last.

    Equity rule: Claude gets the same peer role as the other engines and joins
    all phases (not just the final review), speaking after ChatGPT and Gemini.
    Capabilities are drawn from whatever the loaded mission declares, so the
    injection validates against both the new templates and the legacy debate.

    Returns the mission **unchanged** when it declares
    ``metadata.claude_peer: "unsupported"``. The guard lives here as well as at
    the call site so the refusal holds for every caller, not only the one that
    remembered to ask; the call site owns telling the Founder.
    """
    if not claude_peer_supported(mission):
        return mission
    if any(p.id == "claude" for p in mission.participants):
        return mission

    declared = {c.id for c in mission.capabilities}
    required = tuple(
        cap for cap in ("reasoning.strategic", "critique", "reasoning")
        if cap in declared
    )
    claude = Participant(
        id="claude",
        display_name="Claude",
        assigned_engine=AssignedEngine(
            role="peer_analyst",
            required_capabilities=required,
            transport_provider="browser",
            execution_engine="claude",
        ),
    )

    new_phases = tuple(
        replace(
            phase,
            participant_ids=tuple(list(phase.participant_ids) + ["claude"]),
            instructions=phase.instructions
            + ("" if _PEER_NOTE in phase.instructions else _PEER_NOTE),
        )
        for phase in mission.phases
    )
    return replace(
        mission,
        participants=tuple(list(mission.participants) + [claude]),
        phases=new_phases,
    )


def _make_transport(
    args: argparse.Namespace,
    mission: Mission,
    context: dict,
    artifact_dir: Path | None = None,
):
    """Build the transport for one mission run.

    Extracted so the mission and the optional discovery stage construct the
    transport identically — one place to change, no drift. This keeps the
    module docstring's promise: swapping to a future API/MCP transport is still
    a one-line change, and it is still only made here.

    ``artifact_dir`` is where the transport writes overflow spills and
    downloaded artifacts; it defaults to the run's output directory so `-o`
    governs every file a run produces.
    """
    if args.manual:
        return BrowserTransport(
            auto=args.auto,
            topic_override=context.get("topic_override"),
            injected_files=context.get("injected_files"),
            injected_images=context.get("injected_images"),
        )
    from frelan.transport.playwright_auto import PlaywrightAutomatedTransport

    return PlaywrightAutomatedTransport(
        cdp_url=args.cdp_url,
        auto=args.auto,
        topic_override=context.get("topic_override"),
        injected_files=context.get("injected_files"),
        injected_images=context.get("injected_images"),
        limit_overrides=_limit_overrides(mission),
        refresh_policy=_refresh_policy(mission),
        artifact_dir=artifact_dir if artifact_dir is not None else args.output_dir,
    )


def _run_discovery(args: argparse.Namespace, context: dict) -> Path | None:
    """Run the optional Capability Discovery stage; return the profile path.

    Read-only with respect to this machine: it researches, writes a report plus
    one profile, and changes nothing else. It runs an ORDINARY mission contract
    through the ORDINARY interpreter and transport — no interpreter, renderer,
    contract, or Playwright change exists for this feature.

    Returns None when the Founder declines to continue (or discovery could not
    start), which aborts before the mission launches.
    """
    try:
        mission = load_mission(DISCOVERY_MISSION)
    except MissionValidationError as exc:
        print(f"[DISCOVERY] Skipping — the discovery contract is invalid:\n{exc}", file=sys.stderr)
        return None

    out_dir = args.output_dir / "discovery"
    # The ledger autosaves after every turn, so the directory must exist BEFORE
    # the first one — write_outputs only creates it at the end, which is far too
    # late (a live run warned on every single turn and persisted nothing).
    out_dir.mkdir(parents=True, exist_ok=True)
    instance = MissionInstance(
        mission=mission, ledger=Ledger(autosave_path=out_dir / "ledger.jsonl")
    )
    # Discovery researches the SAME subject the Founder just supplied.
    for key in ("topic_override", "injected_files", "injected_images"):
        if context.get(key):
            instance.context[key] = context[key]

    print("\n" + "=" * 70)
    print("              CAPABILITY DISCOVERY  (pre-planning)")
    print("=" * 70)
    print("Read-only research: nothing is installed, nothing on this machine changes.\n")

    transport = _make_transport(args, mission, instance.context, artifact_dir=out_dir)
    try:
        MissionInterpreter(transport, artifact_dir=out_dir).run(instance)
    except KeyboardInterrupt:
        instance.ledger.append(
            LedgerEntryType.SYSTEM, "Capability Discovery interrupted by user (Ctrl+C)."
        )
        print("\n[INTERRUPTED] Discovery stopped; writing whatever was gathered.")
    finally:
        if not args.manual:
            transport.close()

    # Discovery's evidence stays in its own directory: the root evidence-log is
    # the missions' cumulative record and discovery must not dilute it.
    for path in write_outputs(
        instance, out_dir, evidence_log=out_dir / "discovery-evidence-log.jsonl"
    ):
        print(f"  wrote {path}")

    profile = build_profile(instance)
    profile_path = write_profile(profile, PROFILE_PATH)
    total = count_items(profile)
    print(f"  wrote {profile_path}")
    if total == 0:
        # Fail loudly: an empty profile that looks successful is worse than a
        # warning, because the next conductor would consume silence as "nothing
        # needed" rather than "parsing failed".
        print(
            "[WARNING] No capability profile block was found in the transcript — "
            f"the profile is empty. The readable report is at {out_dir / 'capability-report.md'}."
        )
    else:
        print(
            f"[DISCOVERY] {total} recommendation(s) across "
            f"{len(profile['categories'])} categories."
        )

    if args.auto or not sys.stdin.isatty():
        return profile_path
    print(f"\nReview {profile_path} before continuing. It is informational only.")
    try:
        answer = input("Proceed to mission launch? [Y/n]\n> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return profile_path if answer in ("", "y", "yes") else None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.report:
        print(render_report(load_log(EVIDENCE_LOG)))
        return 0

    if args.prune_spills is not None:
        root = args.output_dir if args.output_dir is not None else DEFAULT_OUTPUT_DIR
        removed, freed = _prune_spills(root, args.prune_spills)
        print(
            f"Removed {removed} spill file(s) older than {args.prune_spills} day(s) "
            f"from {root} ({freed / 1_048_576:.1f} MB freed)."
        )
        return 0

    include_claude = args.claude or args.review or args.high_complexity
    explicit_output_dir = args.output_dir is not None

    resume_meta: dict = {}
    resume_entries: list[dict] = []
    if args.resume:
        # A resume must locate its run directory before anything else — that
        # directory IS the mission being continued.
        args.output_dir = _resolve_run_dir(args)
        resume_meta, resume_entries = _load_resume_file(args.output_dir / "ledger.jsonl")
        args.mission = Path(resume_meta.get("mission_path", str(args.mission)))
        include_claude = include_claude or bool(resume_meta.get("claude_injected"))
        print(
            f"[RESUME] Continuing mission '{args.mission}' from "
            f"{len(resume_entries)} persisted ledger entries."
        )
    # Meeting-type menu: only when no explicit mission path was given and we
    # are interactive — existing .bat launchers and CI stay untouched.
    elif args.mission == DEFAULT_MISSION and sys.stdin.isatty():
        chosen, claude_from_menu = _prompt_meeting_type(ask_claude=not include_claude)
        if chosen:
            args.mission = chosen
        include_claude = include_claude or claude_from_menu

    try:
        mission = load_mission(args.mission)
        if include_claude and not claude_peer_supported(mission):
            # Reported, then cleared — so the run metadata records what actually
            # happened and a later --resume does not retry the injection.
            print(
                _CLAUDE_PEER_REFUSED.format(
                    name=mission.name, value=CLAUDE_PEER_UNSUPPORTED
                ),
                file=sys.stderr,
            )
            include_claude = False
        if include_claude:
            mission = _inject_claude_peer(mission)
    except MissionValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.resume:
        # Only now, with a valid contract in hand. Resolving earlier left an
        # empty run directory (and a pointer to it) behind every mistyped
        # mission path.
        args.output_dir = _resolve_run_dir(args)
        print(f"[RUN] Writing this run's outputs to {args.output_dir}")
    ledger_jsonl = args.output_dir / "ledger.jsonl"

    instance = MissionInstance(
        mission=mission, ledger=Ledger(autosave_path=ledger_jsonl)
    )

    if args.resume:
        # Rewrite the record fresh (meta first; replay re-persists the entries),
        # restore the startup context, and rebuild the execution pointer.
        _write_ledger_meta(ledger_jsonl, resume_meta)
        if resume_meta.get("topic_override"):
            instance.context["topic_override"] = resume_meta["topic_override"]
        _restore_injected_context(instance, resume_meta)
        _replay_entries(instance, resume_entries)
        if instance.status is RuntimeStatus.COMPLETED:
            print("[RESUME] The persisted mission already completed all phases; nothing to resume.")
            for path in write_outputs(instance, args.output_dir):
                print(f"  wrote {path}")
            return 0
        print(
            f"[RESUME] Position restored: phase '{instance.current_phase().id}', "
            f"round {instance.round_number}, next speaker "
            f"{instance.current_participant().display_name}."
        )

    if not args.resume:
        # Flag-based injections (for automated environments like Streamlit UI)
        if args.topic:
            instance.context["topic_override"] = args.topic
            print(f"[INJECT] Custom topic override: '{args.topic}'")

        if args.inject_files:
            injected_files = {}
            injected_images = instance.context.get("injected_images", [])
            for path_str in split_path_list(args.inject_files):
                p = Path(path_str)
                if p.is_file():
                    ext = p.suffix.lower()
                    if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.svg'):
                        if str(p.resolve()) not in injected_images:
                            injected_images.append(str(p.resolve()))
                    elif is_binary_file(p):
                        injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
                    else:
                        try:
                            content = p.read_text(encoding="utf-8")
                            injected_files[path_str] = content
                        except Exception:
                            injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
            if injected_files:
                instance.context["injected_files"] = injected_files
            if injected_images:
                instance.context["injected_images"] = injected_images

        if args.inject_images:
            injected_images = instance.context.get("injected_images", [])
            for img_str in split_path_list(args.inject_images):
                p = Path(img_str)
                if p.is_file():
                    abs_path = str(p.resolve())
                    if abs_path not in injected_images:
                        injected_images.append(abs_path)
                elif looks_like_local_path(img_str):
                    pass
                else:
                    if img_str not in injected_images:
                        injected_images.append(img_str)
            if injected_images:
                instance.context["injected_images"] = injected_images

    # Prompt for custom topic override at startup if running interactively
    if sys.stdin.isatty() and not args.resume:
        try:
            print("\n" + "=" * 70)
            print("                     FRELAN TOPIC INJECTION")
            print("=" * 70)
            print(f"Default Topic: '{mission.objective.strip()}'\n")
            print("Type your custom topic/objective below and press Enter.")
            print("(Or just press Enter to use the default topic)\n")
            custom_topic = input("Custom Topic:\n> ").strip()
            if custom_topic:
                instance.context["topic_override"] = custom_topic
                print(f"\n[SUCCESS] Topic successfully updated to: '{custom_topic}'")

            # Prompt for file injection
            print("\nWould you like to inject reference files? (comma-separated paths, e.g. 'inputs/schema.sql, inputs/notes.txt')")
            print("[INFO] You can place files in your newly created 'inputs/' folder and reference them easily!")
            print("(Or leave blank for none)")
            file_paths_str = input("Reference Files:\n> ").strip()
            if file_paths_str:
                injected_files = {}
                injected_images = instance.context.get("injected_images", [])
                for path_str in split_path_list(file_paths_str):
                    p = Path(path_str)
                    if p.is_file():
                        ext = p.suffix.lower()
                        # Auto-redirect image files to reference images
                        if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.svg'):
                            if str(p.resolve()) not in injected_images:
                                injected_images.append(str(p.resolve()))
                                print(f"  [IMAGE REDIRECT] Added {p.name} directly to Reference Images instead.")
                        # Check for PDF, PPTX, CAD, ZIP, or other binary formats using robust detector
                        elif is_binary_file(p):
                            # Register binary formats as browser attachments
                            injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
                            print(f"  [LOADED ATTACHMENT] {p.name} (Registered for automated browser uploading)")
                        else:
                            try:
                                content = p.read_text(encoding="utf-8")
                                injected_files[path_str] = content
                                print(f"  [LOADED TEXT] {p.name} ({len(content)} chars)")
                            except UnicodeDecodeError:
                                injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
                                print(f"  [LOADED ATTACHMENT] {p.name} (Binary file; registered for automated browser uploading)")
                            except Exception as e:
                                print(f"  [ERROR] Could not read file {path_str}: {e}")
                    else:
                        print(f"  [WARNING] File not found or is a directory: {path_str}")

                if injected_files:
                    instance.context["injected_files"] = injected_files
                if injected_images:
                    instance.context["injected_images"] = injected_images

            # Prompt for image injection
            print("\nWould you like to inject reference images? (comma-separated paths or descriptions, e.g. 'inputs/facade.jpg')")
            print("[INFO] You can place reference images in your 'inputs/' folder for automated Playwright uploads!")
            print("(Or leave blank for none)")
            img_paths_str = input("Reference Images:\n> ").strip()
            if img_paths_str:
                injected_images = instance.context.get("injected_images", [])
                for img_str in split_path_list(img_paths_str):
                    p = Path(img_str)
                    if p.is_file():
                        abs_path = str(p.resolve())
                        if abs_path not in injected_images:
                            injected_images.append(abs_path)
                            print(f"  [ADDED IMAGE] {p.name} ({abs_path})")
                    elif looks_like_local_path(img_str):
                        # A broken path silently becoming a text "description"
                        # means no upload and no error — fail loudly instead.
                        print(f"  [WARNING] Image file not found: {img_str} — skipped. Check the path.")
                    else:
                        if img_str not in injected_images:
                            injected_images.append(img_str)
                            print(f"  [ADDED DESCRIPTION/URL] {img_str}")
                if injected_images:
                    instance.context["injected_images"] = injected_images

            print("=" * 70 + "\n")
        except (EOFError, KeyboardInterrupt):
            pass

    if not args.resume:
        # Record how to resume BEFORE the first turn: the meta line opens a
        # fresh ledger.jsonl and every entry autosaves after it.
        _write_ledger_meta(
            ledger_jsonl,
            {
                "mission_path": str(args.mission),
                "claude_injected": include_claude,
                "topic_override": instance.context.get("topic_override"),
                "injected_files": list(
                    (instance.context.get("injected_files") or {}).keys()
                ),
                "injected_images": list(instance.context.get("injected_images") or []),
            },
        )

    # Optional pre-planning stage. Runs AFTER the topic/reference prompts (so it
    # researches the real project) and BEFORE the mission transport is built, so
    # a declined discovery costs nothing. Skipped on --resume: a resumed mission
    # already discovered.
    if args.discover and not args.resume:
        if _run_discovery(args, instance.context) is None:
            print(
                "[DISCOVERY] Stopped before mission launch. "
                "Nothing was installed and nothing on this machine changed."
            )
            return 0

    transport = _make_transport(args, mission, instance.context)

    exit_code = 0
    try:
        MissionInterpreter(transport, artifact_dir=args.output_dir).run(instance)
    except KeyboardInterrupt:
        # Completed rounds are already on disk (ledger autosave); make the
        # interruption itself part of the record and tell the user how to
        # continue instead of losing the mission.
        instance.ledger.append(
            LedgerEntryType.SYSTEM, "Mission interrupted by user (Ctrl+C)."
        )
        # With no explicit -o the .last-run pointer already names this run, so a
        # bare --resume finds it; an explicit directory must be repeated.
        resume_hint = "python main.py --resume"
        if explicit_output_dir:
            resume_hint += f" -o {args.output_dir}"
        print(f"\n[INTERRUPTED] Progress saved. Continue later with: {resume_hint}")
        exit_code = 130
    finally:
        if not args.manual:
            transport.close()
        # Founder rating: the Mission Library promotion signal (§7 change #2).
        # Interactive, non-auto runs only; every other path skips it silently.
        rating = None
        if sys.stdin.isatty() and not args.auto:
            rating = _prompt_founder_rating()
        written = write_outputs(instance, args.output_dir, founder_rating=rating)
        print(f"\nMission ended: {instance.status.value}")
        for path in written:
            print(f"  wrote {path}")
    return exit_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI-Conductor B Runtime (browser engine)."
    )
    parser.add_argument(
        "mission",
        nargs="?",
        default=str(DEFAULT_MISSION),
        help="Path to a mission contract (.yaml/.yml/.json).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Directory for generated outputs. Omit to write to a fresh "
        f"{DEFAULT_OUTPUT_DIR}/{RUN_DIR_PREFIX}<timestamp>/ directory so run "
        "history is preserved; given explicitly, the path is used verbatim.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Run in manual clipboard mode instead of the default automated browser mode.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Automate checkpoints by automatically continuing until natural completion.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the interrupted mission recorded in <output-dir>/ledger.jsonl "
        "(written automatically by every run); completed rounds are never re-run. "
        f"With no -o, the most recent run is located via {DEFAULT_OUTPUT_DIR}/"
        f"{LAST_RUN_POINTER}.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print a read-only summary of the accumulated evidence log "
        "(per-engine scores, per-meeting-type run counts, peer-score capture "
        "rate) and exit. Runs no mission and changes nothing.",
    )
    parser.add_argument(
        "--prune-spills",
        nargs="?",
        type=int,
        const=14,
        default=None,
        metavar="DAYS",
        help="Delete transport overflow-prompt scratch files older than DAYS "
        "(default 14) from every run directory, then exit. Ledgers, "
        "recommendations, evidence, and harvested artifacts are never touched.",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://localhost:9223",
        help="CDP remote debugging URL for the active Chrome browser.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run the optional read-only Capability Discovery stage before the "
        "mission: each engine researches one ecosystem lane, the findings are "
        "merged into capability-profile.yaml, and you approve before launch. "
        "Installs nothing and changes nothing on this machine.",
    )
    # One behaviour, three spellings kept for existing launchers and habits.
    # The help text says "equal peer" because that is what _inject_claude_peer
    # actually does: Claude joins EVERY phase with the same peer role as the
    # other engines. Calling it a "reviewer" described a design that was
    # deliberately replaced by the equitable-peer model.
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Add Claude to the mission as an equal peer in every phase, "
        "speaking after the other engines.",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Alias for --claude.",
    )
    parser.add_argument(
        "--high-complexity",
        action="store_true",
        help="Alias for --claude.",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Custom main topic override for the mission objective.",
    )
    parser.add_argument(
        "--inject-files",
        default="",
        help="Comma-separated list of local file paths to inject into the context.",
    )
    parser.add_argument(
        "--inject-images",
        default="",
        help="Comma-separated list of image paths or text descriptions to inject into the context.",
    )
    args = parser.parse_args(argv)
    args.mission = Path(args.mission)
    # Left as None when not given: the sentinel that means "resolve a run
    # directory for me" rather than "write to ./outputs".
    if args.output_dir is not None:
        args.output_dir = Path(args.output_dir)
    return args


def _prompt_founder_rating(input_fn=input) -> int | None:
    """Ask the Founder to rate the mission's usefulness (1-5) at the end.

    The rating is the promotion signal for the Mission Library
    (MISSION-LIBRARY-RESOLUTION.md §7 change #2). Optional by design: Enter, a
    non-1-5 value, or EOF/interrupt all skip it (returns None) — never coerced.
    """
    try:
        answer = input_fn(
            "\nRate this mission's usefulness [1-5, Enter to skip]:\n> "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if answer.isdigit() and 1 <= int(answer) <= 5:
        return int(answer)
    return None


def write_outputs(
    instance: MissionInstance,
    output_dir: Path,
    evidence_log: Path | None = None,
    founder_rating: int | None = None,
) -> list[Path]:
    """Write the canonical artifacts; return the paths written.

    ``evidence_log`` is resolved late rather than bound as a parameter default:
    a default evaluated at import time cannot be redirected, which silently
    appended test runs to the real cumulative evidence log.
    """
    evidence_log = EVIDENCE_LOG if evidence_log is None else evidence_log
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    ledger_path = output_dir / "ledger.md"
    ledger_path.write_text(instance.ledger.to_markdown(), encoding="utf-8")
    written.append(ledger_path)

    checkpoints_path = output_dir / "checkpoints.md"
    checkpoints_path.write_text(_render_checkpoints(instance), encoding="utf-8")
    written.append(checkpoints_path)

    recommendation = instance.context.get("final_recommendation")
    if recommendation:
        written += _write_deliverables(instance, output_dir, recommendation)

    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(_metadata(instance), indent=2), encoding="utf-8"
    )
    written.append(metadata_path)

    evidence = collect_evidence(instance)
    if founder_rating is not None:
        evidence["founder_rating"] = founder_rating
    evidence_path = output_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    written.append(evidence_path)
    _append_evidence_log(evidence, evidence_log, output_dir)
    return written


def _write_deliverables(
    instance: MissionInstance, output_dir: Path, recommendation: str
) -> list[Path]:
    """Write every declared output, not just the first.

    A multi-deliverable mission asks the synthesiser for one sentinel-delimited
    section per declared output. Whatever sections come back are written to
    their own filenames; any output the model omitted is reported rather than
    left as a file that silently does not exist. When no sections are found at
    all — a single-output mission, or a model that ignored the format — the
    whole response becomes the primary deliverable, exactly as before.
    """
    outputs = instance.mission.outputs
    if not outputs:
        path = output_dir / "recommendation.md"
        path.write_text(
            _render_recommendation(instance, "Final Recommendation", recommendation),
            encoding="utf-8",
        )
        return [path]

    sections = split_outputs(recommendation, outputs)
    written: list[Path] = []
    if not sections:
        primary = outputs[0]
        path = output_dir / primary.filename
        path.write_text(
            _render_recommendation(instance, primary.title, recommendation),
            encoding="utf-8",
        )
        if len(outputs) > 1:
            print(
                f"[WARNING] {len(outputs)} outputs are declared but the synthesis "
                "carried no deliverable sections; the whole response was written "
                f"to {primary.filename}."
            )
        return [path]

    for output in outputs:
        body = sections.get(output.id)
        if body is None:
            print(
                f"[WARNING] Declared output '{output.id}' "
                f"({output.filename}) was not produced by the synthesis."
            )
            continue
        path = output_dir / output.filename
        path.write_text(
            _render_recommendation(instance, output.title, body), encoding="utf-8"
        )
        written.append(path)
    return written


def _append_evidence_log(evidence: dict, log_path: Path, run_dir: Path) -> None:
    """Append one compact evidence line per mission; never halt on failure.

    ``run_dir`` is what makes an accumulated row navigable: without it the
    cumulative log records that a run happened but not which transcript proves
    it, so no accumulated score can be traced back to the discussion behind it.
    """
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "mission_id": evidence["mission_id"],
        "meeting_type": evidence["meeting_type"],
        "objective": evidence.get("objective"),
        "briefed": evidence.get("briefed"),
        "founder_rating": evidence.get("founder_rating"),
        "status": evidence["status"],
        "participants": {
            pid: {
                "engine": p["engine"],
                "score_means": p["scores_received"]["means"],
                "turns": p["metrics"]["turns"],
                "citations": p["metrics"]["citations"],
                "mean_turn_seconds": p["metrics"]["mean_turn_seconds"],
            }
            for pid, p in evidence["participants"].items()
        },
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError as exc:
        print(f"[WARNING] Could not append evidence to {log_path}: {exc}")


def _render_checkpoints(instance: MissionInstance) -> str:
    lines = [
        "# Checkpoint Summaries",
        "",
        f"**Mission:** {instance.mission.name}",
        "",
    ]
    if not instance.checkpoint_history:
        lines.append("_No checkpoints were reached._")
        return "\n".join(lines) + "\n"
    for i, record in enumerate(instance.checkpoint_history, start=1):
        lines += [
            f"## Checkpoint {i}",
            f"- rounds completed: {record.round_number}",
            f"- phase: {record.phase_id}",
            f"- decision: {record.decision.value}",
        ]
        if record.note.strip():
            lines.append(f"- note: {record.note.strip()}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _effective_objective(instance: MissionInstance) -> str:
    """The objective the mission was actually run against.

    A Founder-supplied ``topic_override`` is what every prompt was rendered
    from, so it — not the contract's default — is what the artifacts must
    report. Printing the default on a briefed run made the deliverable
    contradict the discussion it summarised.
    """
    return instance.context.get(
        "topic_override", instance.mission.objective
    ).strip()


def _render_recommendation(
    instance: MissionInstance, title: str, recommendation: str
) -> str:
    return (
        "\n".join(
            [
                f"# {title}",
                "",
                f"**Mission:** {instance.mission.name}",
                f"**Objective:** {_effective_objective(instance)}",
                f"**Outcome:** {instance.status.value}",
                "",
                "---",
                "",
                recommendation.strip(),
            ]
        )
        + "\n"
    )


def _mission_shape(mission: Mission) -> dict:
    """What this mission IS, structurally — recorded so History can compare runs.

    Every value is read from the contract that actually ran. Nothing here is
    inferred or defaulted into something the contract did not say: an unlabelled
    phase reports an empty stage, and a mission with no declared workflow
    reports none. The point of the record is evidence-based comparison of future
    experiments, which a guessed field would quietly poison.
    """
    return {
        "meeting_type": mission.metadata.get("meeting_type", ""),
        "workflow": mission.metadata.get("workflow", ""),
        "interactions": sorted({ph.interaction for ph in mission.phases}),
        "stages": [ph.stage for ph in mission.phases if ph.stage],
        "phases": [
            {
                "id": ph.id,
                "name": ph.name,
                "stage": ph.stage,
                "interaction": ph.interaction,
                "context": ph.context,
                "participants": list(ph.participant_ids),
            }
            for ph in mission.phases
        ],
        "participants": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "type": p.type,
                "model": p.assigned_engine.execution_engine,
                "transport": p.assigned_engine.transport_provider,
                "role": p.assigned_engine.role,
                "capabilities": list(p.assigned_engine.required_capabilities),
                "has_standing_brief": bool(p.instructions.strip()),
            }
            for p in mission.participants
        ],
    }


def _metadata(instance: MissionInstance) -> dict:
    entries = instance.ledger.entries
    responses = [e for e in entries if e.entry_type is LedgerEntryType.RESPONSE]
    return {
        "mission_id": instance.mission.id,
        "mission_name": instance.mission.name,
        # The mission's structure — meeting type, workflow, interactions,
        # stages, and who took part as what. History reads this.
        **_mission_shape(instance.mission),
        # Both objectives are recorded: what the contract declared, and what the
        # run was actually about. Without the latter a briefed run's metadata
        # could not say what was discussed.
        "objective": _effective_objective(instance),
        "contract_objective": instance.mission.objective.strip(),
        "topic_override": instance.context.get("topic_override"),
        "status": instance.status.value,
        "phases_total": len(instance.mission.phases),
        "rounds_completed": instance.rounds_completed,
        "turns": len(responses),
        "checkpoints": len(instance.checkpoint_history),
        "peer_scoring": instance.mission.metadata.get("peer_scoring") == "true",
        "started_at": entries[0].timestamp if entries else None,
        "ended_at": entries[-1].timestamp if entries else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
