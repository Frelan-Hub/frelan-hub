"""Browser-specific adapters for the Playwright automated transport.

These adapters encapsulate the DOM selectors, JavaScript extractors, and
navigation logic required to interact with specific web-based AI models.
"""

from __future__ import annotations
from typing import Any, Callable, Protocol

# Shared JS that detects a visible "message too long" rejection banner/toast.
# Scoped to alert/toast/dialog/error containers so ordinary message text that
# happens to mention length never triggers recovery. Only valid CSS here (the
# Playwright ':has-text()' pseudo throws SyntaxError inside page.evaluate).
_LIMIT_ERROR_JS = '''() => {
    const needles = [
        "exceeds length limit",
        "exceeding length limit",
        "exceed the length limit",
        "message is too long",
        "prompt is too long",
        "was too long",
    ];
    const scopes = Array.from(document.querySelectorAll(
        '[role="alert"], [role="dialog"], [class*="toast"], [class*="error"], [class*="banner"], [data-testid*="error"]'
    ));
    for (const el of scopes) {
        try {
            if (el.checkVisibility && !el.checkVisibility()) continue;
        } catch (e) {}
        const text = (el.innerText || '').toLowerCase();
        if (needles.some(n => text.includes(n))) return true;
    }
    return false;
}'''

# Shared JS that detects human-only blockers: quota/usage walls, login walls,
# and verification challenges. These are NOT recoverable by reload/refresh —
# the correct response is to surface them and hand the turn to the human.
# (Verification challenges are never auto-solved; that is deliberate policy.)
_BLOCKER_JS = '''() => {
    const needles = [
        "reached your limit",
        "you've reached your",
        "usage limit",
        "out of free messages",
        "upgrade to continue",
        "verify you are human",
        "unusual activity",
        "sign in to continue",
        "session has expired",
        "logged out",
    ];
    const scopes = Array.from(document.querySelectorAll(
        '[role="alert"], [role="dialog"], [class*="banner"], [class*="toast"], main h1, main h2'
    ));
    for (const el of scopes) {
        try {
            if (el.checkVisibility && !el.checkVisibility()) continue;
        } catch (e) {}
        const text = (el.innerText || '').toLowerCase();
        const hit = needles.find(n => text.includes(n));
        if (hit) return text.slice(0, 160);
    }
    return null;
}'''

# Shared JS for best-effort composer chip removal.
_REMOVE_CHIPS_JS = '''(names) => {
    const clicked = [];
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const name of names) {
        const needle = name.toLowerCase();
        for (const btn of buttons) {
            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
            if (!(label.includes('remove') || label.includes('delete'))) continue;
            const scope = btn.closest('div, li, span');
            const ctx = (((scope && scope.parentElement) ? scope.parentElement.innerText : '') + ' ' + label).toLowerCase();
            if (ctx.includes(needle)) {
                btn.click();
                clicked.push(name);
                break;
            }
        }
    }
    return clicked;
}'''

class BrowserAdapter(Protocol):
    """Protocol defining browser-specific behavior for the transport layer."""

    @property
    def engine_key(self) -> str:
        """The key used for logging and tracking."""
        ...

    @property
    def domain(self) -> str:
        """The primary domain to search for when locating active tabs."""
        ...

    @property
    def default_url(self) -> str:
        """The URL to navigate to if no active tab is found."""
        ...

    @property
    def extract_js(self) -> str:
        """JavaScript function that extracts the latest response and artifacts."""
        ...

    @property
    def remove_chips_js(self) -> str:
        """JavaScript function that removes pending attachment chips."""
        return _REMOVE_CHIPS_JS

    @property
    def max_inline_chars(self) -> int:
        """Safe number of chars to type into the composer before spilling to a file."""
        ...

    @property
    def chat_budget_chars(self) -> int | None:
        """Approx. whole-conversation char budget before rollover; None = per-message only."""
        ...

    @property
    def new_chat_url(self) -> str:
        """URL that opens a fresh conversation (used for auto-rollover)."""
        ...

    @property
    def limit_error_js(self) -> str:
        """JavaScript function returning true when a length-limit rejection is visible."""
        return _LIMIT_ERROR_JS

    @property
    def blocker_js(self) -> str:
        """JavaScript returning the text of a human-only blocker (quota/login/
        verification) if one is visible, else null."""
        return _BLOCKER_JS

    @property
    def attach_menu(self) -> dict[str, list[str]]:
        """Selectors for opening the attachment menu and clicking the file upload item."""
        ...

    @property
    def file_input_selectors(self) -> list[str]:
        """Selectors for locating hidden file inputs directly."""
        ...

    def deliver_prompt(self, page: Any, prompt: str, output_fn: Callable[[str], None], wait_until_enabled: Callable[[Any, Any], None]) -> None:
        """Inject the prompt into the browser and click send."""
        ...


class ChatGPTBrowserAdapter:
    """Adapter for chatgpt.com"""

    engine_key = "chatgpt"
    domain = "chatgpt.com"
    default_url = "https://chatgpt.com/"
    new_chat_url = "https://chatgpt.com/"

    # Stay under OpenAI's own 10k paste->attachment conversion line; insert_text
    # is treated as typing, so that native safety net never fires for us.
    max_inline_chars = 9_000
    chat_budget_chars = None  # composer limit is per-message, not per-conversation

    remove_chips_js = _REMOVE_CHIPS_JS
    limit_error_js = _LIMIT_ERROR_JS
    blocker_js = _BLOCKER_JS

    extract_js = '''() => {
        // Automatically handle A/B test feedback by choosing Option 1
        const feedbackButtons = Array.from(document.querySelectorAll('button')).filter(b => b.textContent && b.textContent.includes('I prefer this response'));
        if (feedbackButtons.length > 0) {
            feedbackButtons[0].click();
        }

        // Long outputs pause at the token cap with a "Continue generating"
        // button; without clicking it, the text looks stable and only HALF the
        // response would be harvested (documented OpenAI community behavior).
        let continueClicked = false;
        const contBtn = Array.from(document.querySelectorAll('button')).find(
            b => b.textContent && b.textContent.trim().includes('Continue generating')
        );
        if (contBtn) {
            contBtn.click();
            continueClicked = true;
        }

        const selectors = [
            'article[data-testid*="message-assistant"]',
            'div[data-message-author-role="assistant"]',
            '.agent-turn'
        ];
        let messages = [];
        for (const sel of selectors) {
            messages = Array.from(document.querySelectorAll(sel));
            if (messages.length > 0) break;
        }
        if (messages.length === 0) return null;
        const lastMessage = messages[messages.length - 1];
        const markdownDiv = lastMessage.querySelector('.markdown, .prose');
        const text = markdownDiv ? markdownDiv.innerText : (lastMessage.innerText || lastMessage.textContent);
        const scope = lastMessage;

        const imgs = Array.from(lastMessage.querySelectorAll('img'))
            .filter(img => img.naturalWidth > 128 || img.width > 128 || img.src.includes('oaiusercontent') || img.src.includes('googleusercontent'))
            .map(img => img.src);

        const fileLinks = Array.from(lastMessage.querySelectorAll('a[href]'))
            .map(a => ({ url: a.href, name: (a.getAttribute('download') || a.textContent || '').trim() }))
            .filter(l => l.url && (
                l.url.startsWith('blob:') ||
                l.url.includes('oaiusercontent') ||
                l.url.includes('googleusercontent') ||
                l.url.includes('usercontent.google') ||
                l.url.includes('backend-api/estuary') ||
                l.url.includes('/download')
            ));

        const stopBtn = document.querySelector(
            'button[data-testid="stop-button"], button[aria-label*="Stop streaming"], ' +
            'button[aria-label*="Stop response"], button[aria-label*="Stop generating"]'
        );
        const imgsLoading = Array.from(scope.querySelectorAll('img')).some(img => !img.complete);
        const generating = !!scope.querySelector(
            '[aria-busy="true"], mat-spinner, [class*="skeleton"], [class*="shimmer"], [class*="generating"]'
        );
        // continueClicked counts as busy: generation is about to resume.
        const busy = !!stopBtn || imgsLoading || generating || continueClicked;

        // msgCount: strong identity signal — a re-delivered prompt can make the
        // text baseline equal the finished response, but the message count
        // still proves a new reply exists.
        return { text: text || "", imageUrls: imgs, fileLinks: fileLinks, busy: busy, msgCount: messages.length };
    }'''

    attach_menu = {
        "open": [
            'button[data-testid="composer-plus-btn"]',
            'button[aria-label*="Add photos"]',
            'button[aria-label*="Add files"]',
            'button[aria-label*="Attach"]',
            'button[aria-label*="Upload"]',
        ],
        "item": [
            '[role="menuitem"]:has-text("Add photos")',
            '[role="menuitem"]:has-text("photos")',
            '[role="menuitem"]:has-text("Upload")',
            '[role="menuitem"]:has-text("file")',
        ],
    }

    file_input_selectors = [
        'div:has(#prompt-textarea) input[type="file"]',
        'form:has(#prompt-textarea) input[type="file"]',
        'input[type="file"]',
        'input[accept*="image"]',
        '.hidden input[type="file"]'
    ]

    def deliver_prompt(self, page: Any, prompt: str, output_fn: Callable[[str], None], wait_until_enabled: Callable[[Any, Any], None]) -> None:
        input_selectors = [
            "#prompt-textarea",
            "div[contenteditable='true']",
            "div[role='textbox']",
            "textarea[placeholder*='Ask']",
            "textarea[placeholder*='Message']",
            "textarea"
        ]

        selector_found = None
        for selector in input_selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    selector_found = selector
                    break
            except Exception:
                continue

        if selector_found:
            page.locator(selector_found).click(timeout=2000, force=True)
            page.locator(selector_found).focus(timeout=2000)
            page.wait_for_timeout(200)

            page.keyboard.insert_text(prompt)
            page.wait_for_timeout(200)

            page.keyboard.press(" ")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(500)

            send_selectors = [
                'button[data-testid="send-button"]',
                'button[data-testid="fruitjuice-send-button"]',
                'button[aria-label="Send message"]',
                'button:has(svg)'
            ]

            button_clicked = False
            for b_sel in send_selectors:
                try:
                    btn = page.locator(b_sel)
                    if btn.count() > 0 and btn.is_visible():
                        output_fn("Waiting for ChatGPT uploads to complete (Send button to enable)...")
                        wait_until_enabled(page, btn)
                        btn.click(timeout=2000, force=True)
                        button_clicked = True
                        break
                except Exception:
                    continue

            if not button_clicked:
                page.keyboard.press("Enter")
        else:
            raise Exception("Could not find ChatGPT input box selector.")


class GeminiBrowserAdapter:
    """Adapter for gemini.google.com"""

    engine_key = "gemini"
    domain = "gemini.google.com"
    default_url = "https://gemini.google.com/"
    new_chat_url = "https://gemini.google.com/app"

    max_inline_chars = 18_000  # most composer headroom of the three platforms
    chat_budget_chars = None  # composer limit is per-message, not per-conversation

    remove_chips_js = _REMOVE_CHIPS_JS
    limit_error_js = _LIMIT_ERROR_JS
    blocker_js = _BLOCKER_JS

    extract_js = '''() => {
        const selectors = [
            'message-content',
            '.message-content',
            'div.model-response',
            'gemini-message'
        ];
        let messages = [];
        for (const sel of selectors) {
            messages = Array.from(document.querySelectorAll(sel));
            if (messages.length > 0) break;
        }
        if (messages.length === 0) return null;
        const lastMessage = messages[messages.length - 1];
        const text = lastMessage.innerText || lastMessage.textContent;

        let scope = lastMessage.closest(
            'model-response, .model-response, response-container, .response-container'
        );
        if (!scope) {
            scope = lastMessage;
            for (let i = 0; i < 3 && scope.parentElement; i++) {
                const parent = scope.parentElement;
                if (parent.querySelector('user-query, .user-query, [class*="user-query"]')) break;
                scope = parent;
                if (scope.querySelector('img')) break;
            }
        }

        const imgs = Array.from(scope.querySelectorAll('img'))
            .filter(img => img.naturalWidth > 128 || img.width > 128)
            .map(img => img.src);

        const fileLinks = Array.from(scope.querySelectorAll('a[href]'))
            .map(a => ({ url: a.href, name: (a.getAttribute('download') || a.textContent || '').trim() }))
            .filter(l => l.url && (
                l.url.startsWith('blob:') ||
                l.url.includes('oaiusercontent') ||
                l.url.includes('googleusercontent') ||
                l.url.includes('usercontent.google') ||
                l.url.includes('backend-api/estuary') ||
                l.url.includes('/download')
            ));

        const stopBtn = document.querySelector(
            'button[data-testid="stop-button"], button[aria-label*="Stop streaming"], ' +
            'button[aria-label*="Stop response"], button[aria-label*="Stop generating"]'
        );
        const imgsLoading = Array.from(scope.querySelectorAll('img')).some(img => !img.complete);
        // NOTE: deliberately no aria-busy attribute probe here — Gemini keeps
        // it set on the completed markdown panel FOREVER (observed live
        // 2026-07-11), which held busy=true and burned the whole artifact
        // grace on every turn.
        const generating = !!scope.querySelector(
            'mat-spinner, [class*="skeleton"], [class*="shimmer"], [class*="generating"]'
        );
        const busy = !!stopBtn || imgsLoading || generating;

        return { text: text || "", imageUrls: imgs, fileLinks: fileLinks, busy: busy, msgCount: messages.length };
    }'''

    attach_menu = {
        "open": [
            # Current UI (probed live 2026-07-11): one plus-button labeled
            # "Upload and tools"; no file input exists until it is opened.
            # CSS attribute matching is case-sensitive — use the `i` flag.
            'button[aria-label="Upload and tools"]',
            'button[aria-label*="upload" i]',
            'button[aria-label*="Open upload file menu"]',
            'button[aria-label*="Add files"]',
            '.upload-card-button',
        ],
        "item": [
            # Playwright text engine first: matches the smallest clickable
            # element regardless of tag/role (Gemini's menu items are not
            # role="menuitem" in the current UI — observed 2026-07-11).
            # Menu probe 2026-07-11: the item is now labeled just "Files"
            # (Files / Drive / Notebooks / Create image / ...); clicking it
            # opens the OS chooser, which expect_file_chooser handles.
            'text="Files"',
            'text="Upload files"',
            '[role="menuitem"]:has-text("Upload files")',
            'button:has-text("Upload files")',
            '[role="menuitem"]:has-text("file")',
        ],
    }

    file_input_selectors = [
        'rich-textarea input[type="file"]',
        'input[type="file"]',
        'input[accept*="image"]',
        '.hidden input[type="file"]'
    ]

    def deliver_prompt(self, page: Any, prompt: str, output_fn: Callable[[str], None], wait_until_enabled: Callable[[Any, Any], None]) -> None:
        if "gemini.google.com/app" not in page.url:
            page.goto("https://gemini.google.com/app")
            page.wait_for_load_state("domcontentloaded")

        page.wait_for_timeout(1500)
        page.keyboard.press("Escape")

        input_selectors = [
            "rich-textarea div[contenteditable='true']",
            "rich-textarea",
            ".ql-editor",
            "div[data-placeholder*='prompt'][contenteditable='true']",
            "div[data-placeholder*='Ask'][contenteditable='true']"
        ]

        selector_found = None
        for selector in input_selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    selector_found = selector
                    break
            except Exception:
                continue

        if selector_found:
            target = page.locator(selector_found).first
            target.click(timeout=2000, force=True)
            target.focus(timeout=2000)
            page.wait_for_timeout(500)

            page.keyboard.insert_text(prompt)
            page.wait_for_timeout(500)

            page.keyboard.press(" ")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(500)

            send_selectors = [
                'button[aria-label*="Send"]',
                'button.send-button',
                '.send-button-container button'
            ]

            button_clicked = False
            for b_sel in send_selectors:
                try:
                    btn = page.locator(b_sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        output_fn("Waiting for Gemini uploads to complete (Send button to enable)...")
                        wait_until_enabled(page, btn.first)
                        btn.first.click(timeout=2000, force=True)
                        button_clicked = True
                        break
                except Exception:
                    continue

            if not button_clicked:
                page.keyboard.press("Enter")
        else:
            raise Exception("Could not find Gemini main input field selector.")


class ClaudeBrowserAdapter:
    """Adapter for claude.ai"""

    engine_key = "claude"
    domain = "claude.ai"
    default_url = "https://claude.ai/"
    new_chat_url = "https://claude.ai/new"

    # Chip detection cannot see Claude's current attachment UI, but uploads
    # demonstrably arrive (a real run showed Claude reading five uploaded
    # files while every chip check reported failure — causing a wasteful
    # attachment + truncated-inline double delivery each turn).
    trust_unverified_uploads = True

    max_inline_chars = 12_000
    # Claude resends the whole conversation every turn; past this rough budget
    # (~75k tokens via the chars/4 heuristic) sends start getting rejected, so
    # the transport rolls over to a fresh chat.
    chat_budget_chars = 300_000

    remove_chips_js = _REMOVE_CHIPS_JS
    limit_error_js = _LIMIT_ERROR_JS
    blocker_js = _BLOCKER_JS

    extract_js = '''() => {
        // Automatically handle Claude interactive questionnaires by clicking "Skip" or "X" to continue generation
        const skipButtons = Array.from(document.querySelectorAll('button')).filter(b => {
            const text = (b.textContent || '').trim().toLowerCase();
            const ariaLabel = (b.getAttribute('aria-label') || '').trim().toLowerCase();
            return text === 'skip' || ariaLabel === 'skip' || ariaLabel === 'close';
        });

        for (const btn of skipButtons) {
            if (btn.closest('div[role="dialog"]') || btn.closest('[data-testid="modal"]') || btn.closest('.fixed') || btn.closest('.absolute')) {
                btn.click();
                break;
            }
        }

        let lastMessage = null;

        // 1. [data-is-streaming] containers wrap each assistant message —
        //    present whether streaming or finished. Probed live 2026-07-11:
        //    this is the ONLY selector that isolates just the response text;
        //    the legacy class selectors match nothing on the current UI.
        const streamContainers = document.querySelectorAll('[data-is-streaming]');
        if (streamContainers.length) {
            lastMessage = streamContainers[streamContainers.length - 1];
        }

        // 2. Legacy class-name fallbacks (older UI builds).
        if (!lastMessage) {
            const selectors = [
                'div[data-message-author="assistant"]',
                '.font-claude-message',
                '.prose.break-words',
                '.prose'
            ];
            for (const sel of selectors) {
                const elements = document.querySelectorAll(sel);
                if (elements.length > 0) {
                    lastMessage = elements[elements.length - 1];
                    break;
                }
            }
        }

        // 3. Copy-button ancestor as LAST resort, guarded: on the current UI
        //    this ancestor climb reached a page-wide container and harvested
        //    the sidebar + full history (observed live 2026-07-11: 39k chars
        //    of "New chat / Chats / Projects..." junk relayed to peers).
        if (!lastMessage) {
            const copyBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                const aria = b.getAttribute('aria-label') || '';
                const title = b.getAttribute('title') || '';
                return aria.includes('Copy') || title.includes('Copy');
            });
            if (copyBtns.length > 0) {
                const lastCopyBtn = copyBtns[copyBtns.length - 1];
                const container = lastCopyBtn.closest('.grid') || lastCopyBtn.closest('.flex-col') || lastCopyBtn.parentElement.parentElement.parentElement;
                if (container && !(container.innerText || '').includes('New chat')) {
                    lastMessage = container;
                }
            }
        }

        if (!lastMessage) return null;

        const text = lastMessage.innerText || lastMessage.textContent;
        const scope = lastMessage;

        const imgs = Array.from(scope.querySelectorAll('img'))
            .filter(img => img.naturalWidth > 128 || img.width > 128)
            .map(img => img.src);

        const fileLinks = Array.from(scope.querySelectorAll('a[href]'))
            .map(a => ({ url: a.href, name: (a.getAttribute('download') || a.textContent || '').trim() }))
            .filter(l => l.url && l.url.startsWith('blob:'));

        // NOTE: only valid CSS here. The Playwright text pseudo is NOT real CSS —
        // putting it in querySelector throws SyntaxError and aborts this whole
        // extractor, so Claude never returns text and looks permanently "lagged".
        // aria-label*="Stop" already covers the "Stop generating" button.
        const stopBtn = document.querySelector('button[aria-label*="Stop"]');
        const isStreaming = document.querySelector('[data-is-streaming="true"]') !== null;
        const busy = !!stopBtn || isStreaming;

        // Message count: streaming containers are the reliable per-message
        // anchor (legacy selectors matched zero on the current UI, which made
        // msgCount permanently 0 and disabled the collision guard for Claude).
        const msgCount = streamContainers.length || document.querySelectorAll(
            'div[data-message-author="assistant"], .font-claude-message'
        ).length;

        return { text: text || "", imageUrls: imgs, fileLinks: fileLinks, busy: busy, msgCount: msgCount };
    }'''

    attach_menu = {
        "open": [],
        "item": [],
    }

    file_input_selectors = [
        'input[type="file"]'
    ]

    def deliver_prompt(self, page: Any, prompt: str, output_fn: Callable[[str], None], wait_until_enabled: Callable[[Any, Any], None]) -> None:
        input_selectors = [
            "div[contenteditable='true']",
            "fieldset textarea",
            "textarea[placeholder*='Reply']"
        ]

        selector_found = None
        for selector in input_selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    selector_found = selector
                    break
            except Exception:
                continue

        if selector_found:
            target = page.locator(selector_found).first
            target.click(timeout=2000, force=True)
            target.focus(timeout=2000)
            page.wait_for_timeout(500)

            page.keyboard.insert_text(prompt)
            page.wait_for_timeout(500)

            # Wake up the UI state
            page.keyboard.press(" ")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(500)

            # Force submission using guaranteed keyboard shortcuts instead of fragile button clicks
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            page.keyboard.press("Control+Enter")
            page.wait_for_timeout(200)
            page.keyboard.press("Meta+Enter")

            output_fn("Prompt submitted via keyboard shortcuts (Enter / Cmd+Enter).")
        else:
            raise Exception("Could not find Claude main input field selector.")

def get_adapter(engine_name: str) -> BrowserAdapter | None:
    """Return the corresponding browser adapter for the given engine name."""
    engine_name = engine_name.lower()
    if "chatgpt" in engine_name:
        return ChatGPTBrowserAdapter()
    elif "gemini" in engine_name:
        return GeminiBrowserAdapter()
    elif "claude" in engine_name:
        return ClaudeBrowserAdapter()
    return None
