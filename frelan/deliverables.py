"""Interpretation Layer — splitting one synthesis into the declared outputs.

A Mission Contract may declare several ``outputs``. Only the first was ever
written, so a mission asking for three deliverables silently produced one. This
module turns a synthesis response into one section per declared output.

Two ways to ask for them, one wire format. ``render_output_instructions`` asks
for every deliverable in a single reply; ``render_output_request`` asks for one
deliverable per turn, which is what a multi-document mission uses — a four-part
bundle does not fit in one browser reply, and splitting the ask across turns is
the only thing that raises that ceiling. Both produce the same sentinels, so
``split_outputs`` parses either, including the several per-output replies joined
back together.

Wire format: plain-text sentinels, NOT markdown fences — the same lesson
``discovery.py`` and ``evidence.py`` encode. Responses are harvested from the
rendered DOM via innerText, which strips fences entirely and flattens
indentation, so anything relying on them parses as zero sections while being
plainly visible on screen.
"""

from __future__ import annotations

import re

from frelan.mission_contract import OutputDefinition

BEGIN_SENTINEL = "BEGIN-OUTPUT"
END_SENTINEL = "END-OUTPUT"

# `BEGIN-OUTPUT: <id>` ... `END-OUTPUT: <id>` — the trailing id on END is
# optional, because models frequently omit it.
_SECTION_RE = re.compile(
    rf"^[^\S\n]*{BEGIN_SENTINEL}[^\S\n]*:[^\S\n]*(?P<id>[\w.\-]+)[^\S\n]*$"
    rf"(?P<body>.*?)"
    rf"^[^\S\n]*{END_SENTINEL}[^\S\n]*(?::[^\S\n]*[\w.\-]*)?[^\S\n]*$",
    re.DOTALL | re.MULTILINE,
)


def split_outputs(text: str, outputs: tuple[OutputDefinition, ...]) -> dict[str, str]:
    """Map declared output ids to their section of ``text``.

    Only ids the contract declares are returned — a model inventing a section
    name does not create a deliverable. Ids are matched case-insensitively and
    the first non-empty section for an id wins. An empty result means the
    response carried no usable sections, and the caller should fall back to
    treating the whole response as the primary deliverable.
    """
    declared = {o.id.casefold(): o.id for o in outputs}
    found: dict[str, str] = {}
    for match in _SECTION_RE.finditer(text or ""):
        output_id = declared.get(match.group("id").casefold())
        if output_id is None or output_id in found:
            continue
        body = match.group("body").strip()
        if body:
            found[output_id] = body
    return found


def render_output_instructions(outputs: tuple[OutputDefinition, ...]) -> list[str]:
    """Prompt lines asking the synthesiser for one section per declared output.

    Returns an empty list for a single-output mission: there is nothing to split,
    and the extra ceremony would only invite the model to wrap its whole answer
    in sentinels it does not need.
    """
    if len(outputs) < 2:
        return []

    lines = [
        "",
        "## Required deliverables",
        "",
        f"This mission declares {len(outputs)} separate deliverables. Produce ALL "
        "of them in this one response, each wrapped in its own sentinel block. "
        "Write the sentinel lines as plain text exactly as shown — no code "
        "fences, no bullets, no extra indentation.",
        "",
    ]
    for output in outputs:
        lines += [
            f"- **{output.title}** (`{output.id}`) — {output.description.strip()}",
        ]
    lines += [
        "",
        "Format:",
        "",
    ]
    for output in outputs:
        lines += [
            f"{BEGIN_SENTINEL}: {output.id}",
            f"<the complete {output.title} here>",
            f"{END_SENTINEL}: {output.id}",
            "",
        ]
    return lines


def render_output_request(
    output: OutputDefinition,
    index: int,
    total: int,
    produced_titles: tuple[str, ...] = (),
) -> list[str]:
    """Prompt lines asking for ONE declared deliverable, in its own turn.

    The counterpart to ``render_output_instructions`` for the per-output
    synthesis path: every deliverable gets a full turn instead of sharing one
    reply with the others, so the ceiling scales with the bundle rather than
    with a single model response.

    ``produced_titles`` names the documents already written in this run. They
    are in the synthesiser's own conversation, so they are named rather than
    restated — the point is consistency between documents, not re-reading them.
    """
    lines = [
        "",
        f"## Deliverable {index + 1} of {total} — {output.title}",
        "",
        f"Produce the complete **{output.title}** and nothing else this turn. "
        f"It is written to `{output.filename}` exactly as you write it, so it "
        "must stand on its own: no cross-references to the discussion, no "
        "\"as agreed above\", no meta-commentary about the synthesis.",
        "",
        f"**What this document is:** {output.description.strip()}",
    ]
    if produced_titles:
        already = ", ".join(produced_titles)
        lines += [
            "",
            f"Already written in this run: {already}. Stay consistent with "
            "them — do not contradict, restate, or replace them.",
        ]
    lines += [
        "",
        "Wrap the whole document in these two plain-text sentinel lines, "
        "written exactly as shown — no code fences, no bullets, no extra "
        "indentation:",
        "",
        f"{BEGIN_SENTINEL}: {output.id}",
        f"<the complete {output.title} here>",
        f"{END_SENTINEL}: {output.id}",
    ]
    return lines


def ensure_wrapped(text: str, output: OutputDefinition) -> tuple[str, bool]:
    """``(text, False)`` if it already carries ``output``'s section, else wrap it.

    A reply to "produce document X, and nothing else" *is* document X. When the
    model omits the sentinels — a formatting slip, not a content failure — the
    alternative is losing the whole document to a "not produced" warning. The
    boolean is the caller's cue to record the repair in the ledger, so a wrapped
    response is never mistaken for one the model formatted correctly.
    """
    if split_outputs(text, (output,)):
        return text, False
    return (
        f"{BEGIN_SENTINEL}: {output.id}\n"
        f"{(text or '').strip()}\n"
        f"{END_SENTINEL}: {output.id}"
    ), True
