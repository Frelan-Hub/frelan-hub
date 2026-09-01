"""Transport Layer — the Browser (human-in-the-loop) adapter.

For the MVP the *Founder is the transport*: the tool copies each prompt to the
clipboard and prints it; the Founder pastes it into the relevant browser tab
(ChatGPT / Gemini), then pastes the model's reply back into the CLI.

All I/O goes through injectable callables (``input_fn`` / ``output_fn`` /
``clipboard_copy``) so the interactive behaviour can be driven deterministically
in tests without a real terminal or clipboard.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from frelan.enums import CheckpointDecision
from frelan.mission_contract import Participant
from frelan.transport.adapters import get_adapter

_RULE = "=" * 70
_DASH = "-" * 70

_CHECKPOINT_KEYS = {
    "C": CheckpointDecision.CONTINUE,
    "V": CheckpointDecision.CONVERGED,
    "E": CheckpointDecision.ESCALATE,
    "T": CheckpointDecision.TERMINATE,
}


def _default_clipboard_copy(text: str) -> bool:
    """Copy ``text`` to the clipboard; return ``True`` on success.

    Degrades gracefully: if pyperclip or a clipboard backend is unavailable, the
    prompt is still printed, so the workflow never hard-fails on clipboard.
    """
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


def split_path_list(raw: str) -> list[str]:
    """Split a user-entered list of paths into individual entries.

    Accepts comma-separated bare paths AND quoted paths separated by spaces or
    commas (quoted paths may contain spaces), e.g.:
        inputs/a.sql, inputs/b.txt
        "C:\\refs\\plan v2.jpg" "C:\\refs\\site.jpg"
    """
    quoted = [a or b for a, b in re.findall(r'"([^"]+)"|\'([^\']+)\'', raw)]
    rest = re.sub(r'"[^"]+"|\'[^\']+\'', ",", raw)
    parts = quoted + rest.split(",")
    return [p.strip().strip("\"'") for p in parts if p.strip(" \"',")]


def looks_like_local_path(s: str) -> bool:
    """Heuristic: does this entry mean a local file path (vs a description/URL)?

    Used to warn-and-skip missing files instead of silently registering a
    broken path as a free-text image 'description'.
    """
    if s.lower().startswith(("http://", "https://")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", s) or "\\" in s:
        return True
    return bool(re.search(r"\.[A-Za-z]\w{0,4}$", s))


def _is_binary_file(filepath: Path) -> bool:
    """Detect if a file is binary by checking for null bytes in its first kilobyte."""
    try:
        with open(filepath, "rb") as f:
            return b"\x00" in f.read(1024)
    except Exception:
        return True


class BrowserTransport:
    """Human-mediated transport over the CLI and system clipboard."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        clipboard_copy: Callable[[str], bool] | None = None,
        sentinel: str = "END",
        auto: bool = False,
        topic_override: str | None = None,
        prompt_inject: str | None = None,
        injected_files: dict[str, str] | None = None,
        injected_images: list[str] | None = None,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self._clipboard_copy = clipboard_copy or _default_clipboard_copy
        self._sentinel = sentinel
        self.auto = auto
        self.topic_override = topic_override
        self.prompt_inject = prompt_inject
        self.injected_files = injected_files
        self.injected_images = injected_images

    def deliver_prompt(self, participant: Participant, prompt: str) -> None:
        engine = participant.assigned_engine
        # Humans pasting keep the platform's native oversize handling (e.g.
        # ChatGPT auto-converts big pastes to attachments) — just warn them.
        adapter = get_adapter(engine.execution_engine)
        if adapter and len(prompt) > adapter.max_inline_chars:
            self._output(
                f"[ADVISORY] Prompt is {len(prompt)} chars — above "
                f"{engine.execution_engine}'s ~{adapter.max_inline_chars}-char safe "
                "paste threshold. The browser may auto-convert it to an attachment "
                "or reject it; consider saving it as a .md file and attaching it."
            )
        copied = self._clipboard_copy(prompt)
        self._output(_RULE)
        self._output(
            f"PROMPT FOR: {participant.display_name} "
            f"[{engine.execution_engine} via {engine.transport_provider}]"
        )
        self._output(_RULE)
        self._output(prompt)
        self._output(_DASH)
        if copied:
            self._output("(prompt copied to clipboard — paste it into the browser tab)")
        else:
            self._output("(clipboard unavailable — copy the prompt above manually)")

    def collect_response(self, participant: Participant) -> str:
        self._output(
            f"\nPaste {participant.display_name}'s response below. "
            f"Finish with a line containing only '{self._sentinel}'."
            f"\n(Or enter '[P]' to edit prompt / topic / files / images for this turn):"
        )
        try:
            first_line = self._input("> ").strip()
        except EOFError:
            return ""

        if first_line.upper() in ("P", "[P]"):
            self._manage_prompt_menu()
            return "__EDIT_PROMPT__"

        lines = [first_line]
        while True:
            try:
                line = self._input("")
            except EOFError:
                break
            if line.strip() == self._sentinel:
                break
            lines.append(line)
        return "\n".join(lines).strip()

    def ask_checkpoint(self, summary: str) -> CheckpointDecision:
        self._output(summary)
        if self.auto:
            self._output("\nAutonomous mode: Automatically choosing CONTINUE to proceed...")
            return CheckpointDecision.CONTINUE

        while True:
            self._output(
                "\nCheckpoint — choose:  "
                "[C] Continue   [V] Converged   [E] Escalate   [T] Terminate   [F] Fully Automate   [P] Edit Prompt"
            )
            choice = self._input("> ").strip().upper()
            if choice == "F":
                self.auto = True
                self._output("\nSwitching to fully automated mode for all succeeding checkpoints...")
                return CheckpointDecision.CONTINUE

            if choice == "P":
                self._manage_prompt_menu()
                continue

            decision = _CHECKPOINT_KEYS.get(choice)
            if decision is not None:
                return decision
            self._output("Please enter one of C, V, E, T, F, P.")

    def _manage_prompt_menu(self) -> None:
        while True:
            self._output("\n--- Edit Prompt / Topic Override ---")
            self._output("1. Edit/change the main topic (Mission Objective)")
            self._output("2. Inject custom instructions for the next turn")
            self._output("3. Manage/inject reference files")
            self._output("4. Manage/inject reference images")
            self._output("5. Clear all custom overrides, instructions, files, and images")
            self._output("6. Save & Exit (Done)")
            sub_choice = self._input("Choose an option [1-6]: ").strip()
            if sub_choice == "1":
                new_topic = self._input("Enter new main topic: ").strip()
                if new_topic:
                    self.topic_override = new_topic
                    self._output(f"\nMain topic updated to: '{new_topic}'")
            elif sub_choice == "2":
                inject_text = self._input("Enter custom instructions for next turn: ").strip()
                if inject_text:
                    self.prompt_inject = inject_text
                    self._output(f"\nCustom instructions injected!")
            elif sub_choice == "3":
                self._output("\n--- Manage Reference Files ---")
                if self.injected_files:
                    self._output("Currently Injected Files:")
                    for filepath in self.injected_files:
                        self._output(f" - {filepath}")
                else:
                    self._output("No reference files injected.")
                file_action = self._input("\nChoose file action: [A] Add/update a file, [D] Delete a file, [C] Cancel: ").strip().upper()
                if file_action == "A":
                    path_str = self._input("Enter file path to inject: ").strip().strip("\"'")
                    if path_str:
                        p = Path(path_str)
                        if p.is_file():
                            ext = p.suffix.lower()
                            # Auto-redirect image files to reference images
                            if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'):
                                if self.injected_images is None:
                                    self.injected_images = []
                                if path_str not in self.injected_images:
                                    self.injected_images.append(path_str)
                                self._output(f"\n[IMAGE REDIRECT] Automatically redirected image '{p.name}' to Reference Images!")
                                continue

                            # Handle known binary/document formats using robust null-byte check
                            if _is_binary_file(p):
                                if self.injected_files is None:
                                    self.injected_files = {}
                                self.injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
                                self._output(f"\n[LOADED ATTACHMENT] Registered binary/model file '{p.name}' for automated uploading.")
                                continue

                            # Try reading as text
                            try:
                                content = p.read_text(encoding="utf-8")
                                if self.injected_files is None:
                                    self.injected_files = {}
                                self.injected_files[path_str] = content
                                self._output(f"\n[SUCCESS] Loaded text file: {p.name} ({len(content)} chars)")
                            except UnicodeDecodeError:
                                if self.injected_files is None:
                                    self.injected_files = {}
                                self.injected_files[path_str] = "[Binary file attachment — automatically uploaded to browser]"
                                self._output(f"\n[LOADED ATTACHMENT] Registered binary file '{p.name}' for automated uploading.")
                            except Exception as e:
                                self._output(f"\n[ERROR] Could not read file {path_str}: {e}")
                        else:
                            self._output(f"\n[WARNING] File not found: {path_str}")
                elif file_action == "D":
                    path_str = self._input("Enter file path or name to delete: ").strip().strip("\"'")
                    if path_str and self.injected_files:
                        deleted = False
                        if path_str in self.injected_files:
                            self.injected_files.pop(path_str)
                            deleted = True
                        else:
                            for key in list(self.injected_files.keys()):
                                if Path(key).name == path_str:
                                    self.injected_files.pop(key)
                                    deleted = True
                                    break
                        if deleted:
                            self._output(f"\nFile '{path_str}' removed.")
                            if not self.injected_files:
                                self.injected_files = None
                        else:
                            self._output(f"\nFile '{path_str}' not found in injected list.")
            elif sub_choice == "4":
                self._output("\n--- Manage Reference Images ---")
                if self.injected_images:
                    self._output("Currently Injected Images/Descriptions:")
                    for idx, img in enumerate(self.injected_images, 1):
                        self._output(f" {idx}. {img}")
                else:
                    self._output("No reference images injected.")
                img_action = self._input("\nChoose image action: [A] Add image/description, [D] Delete an image, [C] Cancel: ").strip().upper()
                if img_action == "A":
                    img_str = self._input("Enter image path or description: ").strip().strip("\"'")
                    if img_str:
                        if not Path(img_str).is_file() and looks_like_local_path(img_str):
                            self._output(f"\n[WARNING] Image file not found: {img_str} — not added. Check the path.")
                            continue
                        if self.injected_images is None:
                            self.injected_images = []
                        if img_str not in self.injected_images:
                            self.injected_images.append(img_str)
                            self._output(f"\n[SUCCESS] Added image reference: {img_str}")
                elif img_action == "D":
                    if self.injected_images:
                        idx_str = self._input("Enter number of the image to delete: ").strip().strip("\"'")
                        try:
                            idx = int(idx_str) - 1
                            if 0 <= idx < len(self.injected_images):
                                removed = self.injected_images.pop(idx)
                                self._output(f"\nRemoved image reference: {removed}")
                                if not self.injected_images:
                                    self.injected_images = None
                            else:
                                self._output("\nInvalid number.")
                        except ValueError:
                            self._output("\nInvalid input. Please enter a number.")
            elif sub_choice == "5":
                self.topic_override = None
                self.prompt_inject = None
                self.injected_files = None
                self.injected_images = None
                self._output("\nCleared all custom overrides, instructions, files, and images.")
            elif sub_choice == "6":
                break

    def _read_multiline(self) -> str:
        lines: list[str] = []
        while True:
            try:
                line = self._input("")
            except EOFError:
                break
            if line.strip() == self._sentinel:
                break
            lines.append(line)
        return "\n".join(lines).strip()
