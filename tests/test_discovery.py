"""Capability Discovery — profile extraction, merge, and deduplication."""

from __future__ import annotations

import yaml

from frelan.discovery import (
    build_profile,
    count_items,
    merge_categories,
    parse_profile_blocks,
    write_profile,
)
from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance

CLAUDE_LANE = """Here are my findings for the Claude ecosystem.

BEGIN-CAPABILITY-PROFILE
project: A note-taking app
---
category: claude_ecosystem
name: filesystem-mcp
kind: mcp_server
source: https://example.invalid/fs
status: recommended
rationale: The app reads local notes.
---
category: mcp_servers
name: Playwright
kind: mcp_server
status: optional
END-CAPABILITY-PROFILE
"""

# Same tool, different casing and category spelling, found by another lane —
# this is what deduplication has to collapse.
GEMINI_LANE = """My open-source findings.

BEGIN-CAPABILITY-PROFILE
---
category: MCP Servers
name: playwright
source: https://example.invalid/pw
rationale: Browser automation.
---
category: libraries
name: pyyaml
status: recommended
END-CAPABILITY-PROFILE
"""

# The shape a model ACTUALLY emitted in a live run: it thought in YAML, and
# innerText stripped every leading space, leaving bare category headers and
# flat key/value lines. This exact text once parsed to zero findings.
FLATTENED_BY_THE_DOM = """BEGIN-CAPABILITY-PROFILE
project: FRELAN CLI Conductor
categories:
claude_ecosystem:
- name: Claude Agent SDK
kind: sdk
source: https://pypi.org/project/claude-agent-sdk/
status: optional
rationale: Embeddable Python agent loop.
- name: Claude Code
kind: cli_tool
status: recommended
gemini_open_source:
- name: Pydantic
kind: library
status: recommended
END-CAPABILITY-PROFILE
"""


def _instance(make_mission, responses: list[tuple[str, str]]) -> MissionInstance:
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    for participant_id, content in responses:
        instance.ledger.append(
            LedgerEntryType.RESPONSE, content, participant_id=participant_id
        )
    return instance


def test_parses_sentinel_block_even_when_the_dom_strips_fences() -> None:
    # Responses are harvested via innerText, which strips markdown fences, so a
    # fenced block must still parse -- and a bare one must parse too.
    fenced = CLAUDE_LANE.replace(
        "BEGIN-CAPABILITY-PROFILE", "BEGIN-CAPABILITY-PROFILE\n```yaml"
    ).replace("END-CAPABILITY-PROFILE", "```\nEND-CAPABILITY-PROFILE")

    for text in (CLAUDE_LANE, fenced):
        blocks = parse_profile_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["project"] == "A note-taking app"


def test_ignores_prose_and_blocks_with_nothing_usable() -> None:
    assert parse_profile_blocks("No profile here, just prose.") == []
    empty = "BEGIN-CAPABILITY-PROFILE\nnothing but words\nEND-CAPABILITY-PROFILE"
    assert parse_profile_blocks(empty) == []


def test_recovers_findings_the_dom_flattened() -> None:
    # REGRESSION: a live run harvested exactly this shape and produced ZERO
    # findings, because innerText strips every leading space and the parser
    # then required real YAML nesting. Bare category headers must still work.
    categories = merge_categories(
        [("claude", parse_profile_blocks(FLATTENED_BY_THE_DOM)[0])]
    )
    assert [i["name"] for i in categories["claude_ecosystem"]] == [
        "Claude Agent SDK",
        "Claude Code",
    ]
    assert [i["name"] for i in categories["gemini_open_source"]] == ["Pydantic"]
    # Records inherit the header they sit under, and each record keeps its own
    # fields rather than bleeding them into the next.
    assert categories["claude_ecosystem"][0]["kind"] == "sdk"
    assert categories["claude_ecosystem"][1]["kind"] == "cli_tool"


def test_merge_deduplicates_across_lanes_and_unions_provenance() -> None:
    blocks = [
        ("claude", parse_profile_blocks(CLAUDE_LANE)[0]),
        ("gemini", parse_profile_blocks(GEMINI_LANE)[0]),
    ]
    categories = merge_categories(blocks)

    # 'MCP Servers' and 'mcp_servers' are the same category.
    assert "mcp servers" not in categories
    playwright = [i for i in categories["mcp_servers"] if i["name"] == "Playwright"]
    assert len(playwright) == 1, "same tool from two lanes must collapse to one entry"

    # Corroboration is preserved, and the first lane's fields survive while the
    # second lane fills in what the first left blank.
    assert playwright[0]["discovered_by"] == ["claude", "gemini"]
    assert playwright[0]["status"] == "optional"          # from claude
    assert playwright[0]["source"] == "https://example.invalid/pw"  # filled by gemini


def test_template_placeholders_are_not_recorded_as_findings() -> None:
    echoed = """BEGIN-CAPABILITY-PROFILE
project: <one line naming the project>
---
category: sdks
name: <name>
source: <url>
---
category: sdks
name: real-sdk
kind: <mcp_server|sdk>
END-CAPABILITY-PROFILE
"""
    categories = merge_categories([("chatgpt", parse_profile_blocks(echoed)[0])])
    assert [i["name"] for i in categories["sdks"]] == ["real-sdk"]
    # A placeholder field is dropped, not carried into the artifact.
    assert "kind" not in categories["sdks"][0]


def test_known_categories_are_ordered_and_unknown_ones_are_kept() -> None:
    block = """BEGIN-CAPABILITY-PROFILE
---
category: quantum_widgets
name: novel-thing
---
category: claude_ecosystem
name: some-skill
END-CAPABILITY-PROFILE
"""
    categories = merge_categories([("claude", parse_profile_blocks(block)[0])])
    # Known category first (CATEGORY_ORDER), unknown appended -- a new category
    # is a mission-instructions edit, never a code change. A live run really did
    # invent one ("protocols"), and it survived intact.
    assert list(categories) == ["claude_ecosystem", "quantum_widgets"]


def test_build_profile_merges_every_response_not_just_the_last(make_mission) -> None:
    instance = _instance(
        make_mission, [("claude", CLAUDE_LANE), ("gemini", GEMINI_LANE)]
    )
    profile = build_profile(instance)

    assert profile["version"] == 1
    assert profile["project"] == "A note-taking app"
    assert profile["mission"] == "m1"
    # claude_ecosystem(1) + mcp_servers(1, deduped) + libraries(1)
    assert count_items(profile) == 3


def test_profile_falls_back_to_the_topic_and_reports_an_empty_discovery(
    make_mission,
) -> None:
    instance = _instance(make_mission, [("claude", "I found nothing useful.")])
    instance.context["topic_override"] = "A note-taking app"
    profile = build_profile(instance)

    assert profile["project"] == "A note-taking app"
    # An empty profile must be visibly empty, so the caller can warn rather than
    # let a downstream conductor read silence as "nothing needed".
    assert count_items(profile) == 0


def test_written_profile_is_valid_yaml_and_carries_no_install_logic(
    make_mission, tmp_path
) -> None:
    instance = _instance(make_mission, [("claude", CLAUDE_LANE)])
    path = write_profile(build_profile(instance), tmp_path / "capability-profile.yaml")

    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert loaded["categories"]["claude_ecosystem"][0]["name"] == "filesystem-mcp"
    # The artifact is informational: it records WHAT, never HOW to install.
    assert "INFORMATIONAL ONLY" in text
    for forbidden in ("pip install", "npm install", "apt-get", "winget"):
        assert forbidden not in text
