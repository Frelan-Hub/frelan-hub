"""Interpretation Layer — Capability Discovery profile extraction.

Turns a finished Capability Discovery mission's transcript into one reusable
artifact: ``capability-profile.yaml``.

Read-only boundary (the whole point of this stage): this module parses text and
writes exactly one file. It never installs, configures, downloads, or probes
local software, and the profile it emits carries no install commands. Discovery
never changes the system; execution does — and execution belongs to the future
CLI/Local Conductors. The profile is the bridge between them, and it is
informational only.

NAMING: "capability" here means *an ecosystem component the project needs* (an
MCP server, an SDK, a VSCode extension). This is NOT the sense used in the
CAPABILITY-ORCHESTRATION.md, where a capability is *what a model can do*
(``reasoning.strategic``). Two concepts, one word. See CAPABILITY-DISCOVERY.md.

Boundary, same as evidence.py: discovery PRODUCES a profile; it never decides,
promotes, or writes any model's capability standing.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from frelan.enums import LedgerEntryType
from frelan.mission_instance import MissionInstance

BEGIN_SENTINEL = "BEGIN-CAPABILITY-PROFILE"
END_SENTINEL = "END-CAPABILITY-PROFILE"

PROFILE_VERSION = 1

# Plain-text sentinels, NOT markdown fences: responses are harvested from the
# rendered DOM via innerText, which strips fences entirely (see evidence.py —
# a real run parsed 0 score blocks that were plainly visible on screen).
_BLOCK_RE = re.compile(
    rf"^[^\S\n]*{BEGIN_SENTINEL}[^\S\n]*$(.*?)^[^\S\n]*{END_SENTINEL}[^\S\n]*$",
    re.DOTALL | re.MULTILINE,
)

# Ordering ONLY. Unknown categories are kept and appended alphabetically, so
# adding a category is a mission-instructions edit, never a code change
# ("support future expansion without redesign").
CATEGORY_ORDER = (
    "claude_ecosystem",
    "openai_ecosystem",
    "gemini_open_source",
    "sdks",
    "apis",
    "frameworks",
    "libraries",
    "mcp_servers",
    "connectors",
    "vscode_extensions",
    "cli_tools",
    "runtime_requirements",
    "local_services",
    "optional_components",
    "experimental_components",
    "risks",
    "version_compatibility",
)

# Free-text fields copied through verbatim (trimmed). Absent -> omitted.
_ITEM_FIELDS = ("kind", "source", "version", "status", "rationale")

# Every key a record may carry. `category` and `name` are structural.
_RECORD_KEYS = ("category", "name", "discovered_by") + _ITEM_FIELDS

# A model that echoes the instruction template rather than replacing it emits
# these angle-bracket placeholders. They are not findings.
_PLACEHOLDER_RE = re.compile(r"^<.*>$")


def _clean_category(name: Any) -> str:
    """Normalise a category key so 'MCP Servers' and 'mcp-servers' merge."""
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def _normalise_item(item: Any, discovered_by: str) -> dict[str, Any] | None:
    """Coerce one recommendation into the profile item shape, or drop it.

    Tolerant by design — models format loosely. A bare string becomes a name;
    unknown keys are ignored; template placeholders and nameless entries are
    dropped rather than propagated into the artifact.
    """
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict):
        return None

    name = str(item.get("name", "")).strip()
    if not name or _PLACEHOLDER_RE.match(name):
        return None

    out: dict[str, Any] = {"name": name}
    for key in _ITEM_FIELDS:
        value = item.get(key)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text and not _PLACEHOLDER_RE.match(text):
            out[key] = text

    # Provenance: trust an explicit claim, else attribute to the responder.
    declared = item.get("discovered_by")
    if isinstance(declared, str):
        # Flat wire format: "claude, gemini".
        sources = [s.strip() for s in declared.split(",")]
    elif isinstance(declared, list):
        sources = [str(s).strip() for s in declared if str(s).strip()]
    else:
        sources = []
    out["discovered_by"] = sorted({s for s in sources if s} or {discovered_by})
    return out


def _merge_item(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Fold a duplicate into the entry already held — never lose provenance.

    Two peers finding the same tool is corroboration, so ``discovered_by``
    unions. First non-empty value wins for every other field: the earliest
    (owning-lane) description is the authoritative one.
    """
    merged = dict(existing)
    for key in _ITEM_FIELDS:
        if key not in merged and key in incoming:
            merged[key] = incoming[key]
    merged["discovered_by"] = sorted(
        set(existing.get("discovered_by", [])) | set(incoming.get("discovered_by", []))
    )
    return merged


def _parse_body(body: str) -> dict[str, Any]:
    """Scan one block body into ``{"project": str, "items": [record, ...]}``.

    FLAT and indentation-immune on purpose. Responses are harvested from the
    rendered DOM via innerText, which strips BOTH markdown fences AND every
    level of leading whitespace — a live run produced a perfectly-formed block
    whose YAML was unparseable because all nesting had been flattened. So the
    wire format carries no nesting to lose: ``key: value`` lines, records
    separated by ``---``. This is the same lesson evidence.py encodes for its
    score blocks.

    Tolerant: leading "- " bullets and stray fences are ignored, unknown keys
    are dropped, and a missing final separator still yields the last record.
    """
    project = ""
    section = ""  # category header currently in force (see the bare-header rule)
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if current:
            # A record inherits the header it sits under unless it names its own.
            if "category" not in current and section:
                current["category"] = section
            records.append(current)
            current = {}

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if not line or line.startswith("```"):
            continue
        if set(line) == {"-"}:  # a '---' separator (any dash run)
            flush()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if not value:
            # A bare "claude_ecosystem:" header. This is the shape a model
            # naturally emits when it thinks in YAML and innerText then strips
            # the indentation (observed in a live run), so treat it as the
            # category for the records that follow.
            if key and key != "categories" and key not in _RECORD_KEYS:
                flush()
                section = key
            continue
        if key == "project":
            if not project:
                project = value
            continue
        # A repeated structural key means the previous record ended, even if
        # the model forgot the separator.
        if key in ("category", "name") and key in current:
            flush()
        if key in _RECORD_KEYS:
            current[key] = value
    flush()
    return {"project": project, "items": records}


def parse_profile_blocks(text: str) -> list[dict[str, Any]]:
    """Extract every sentinel-delimited profile block from one response.

    A block with no usable records is dropped, so prose that merely mentions
    the sentinels never becomes a phantom finding.
    """
    blocks = [_parse_body(body) for body in _BLOCK_RE.findall(text)]
    return [b for b in blocks if b["items"] or b["project"]]


def merge_categories(
    blocks: list[tuple[str, dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge ``(responder_id, block)`` pairs into deduplicated categories.

    Dedupe key is (category, casefolded name), so 'Playwright' and 'playwright'
    in the same category collapse to one entry with both discoverers recorded.
    Ordering is deterministic: known categories first in CATEGORY_ORDER, then
    unknown ones alphabetically; items alphabetically within each category.
    """
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for responder, block in blocks:
        for record in block.get("items", []):
            category = _clean_category(record.get("category", ""))
            if not category or _PLACEHOLDER_RE.match(category):
                continue  # a record with no home is not a finding
            normalised = _normalise_item(record, responder)
            if normalised is None:
                continue
            bucket = merged.setdefault(category, {})
            key = normalised["name"].casefold()
            bucket[key] = (
                _merge_item(bucket[key], normalised) if key in bucket else normalised
            )

    known = [c for c in CATEGORY_ORDER if merged.get(c)]
    unknown = sorted(c for c in merged if c not in CATEGORY_ORDER and merged[c])
    return {
        category: [merged[category][k] for k in sorted(merged[category])]
        for category in known + unknown
    }


def build_profile(instance: MissionInstance) -> dict[str, Any]:
    """Build the capability profile from a finished discovery mission.

    Every response is scanned, not just the merge phase's: merging and
    deduplication happen HERE, deterministically, rather than depending on one
    model to have done it faithfully. If the merge phase behaved, its block is
    simply folded in with the rest.
    """
    blocks: list[tuple[str, dict[str, Any]]] = [
        (entry.participant_id or "unknown", block)
        for entry in instance.ledger.entries
        if entry.entry_type is LedgerEntryType.RESPONSE
        for block in parse_profile_blocks(entry.content)
    ]
    project = ""
    for _responder, block in blocks:
        candidate = str(block.get("project", "")).strip()
        if candidate and not _PLACEHOLDER_RE.match(candidate):
            project = candidate
            break
    # `discovered_by` arrives as a flat string ("claude, gemini") on the wire.

    return {
        "version": PROFILE_VERSION,
        "project": project
        or instance.context.get("topic_override", instance.mission.objective).strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mission": instance.mission.id,
        "categories": merge_categories(blocks),
    }


def write_profile(profile: dict[str, Any], path: Path) -> Path:
    """Write the profile as YAML. Informational only — never install logic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Capability Profile — generated by FRELAN Capability Discovery.\n"
        "# INFORMATIONAL ONLY. Discovery never changes the system; this file\n"
        "# records what was recommended, not how to install it. Consuming it\n"
        "# (install/configure/build) is the CLI/Local Conductor's job.\n"
    )
    path.write_text(
        header + yaml.safe_dump(profile, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def count_items(profile: dict[str, Any]) -> int:
    """Total recommendations in a profile — used to report an empty discovery."""
    return sum(len(items) for items in profile.get("categories", {}).values())
