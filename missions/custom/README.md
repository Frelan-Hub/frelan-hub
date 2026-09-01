# Custom meeting types

Drop a mission contract (`.yaml` / `.yml`) in this folder and it becomes a
meeting type: `python main.py` discovers it, lists it as `[custom] <name>`, and
runs it like any shipped template. There is no registration step and no code
change — the menu is a dynamic filesystem scan.

Start from [TEMPLATE.yaml.example](TEMPLATE.yaml.example) — copy it to
`missions/custom/<your_name>.yaml` and edit. The `.example` suffix is what keeps
the template itself out of the menu.

Validate before running:

```bash
python -c "from frelan.mission_loader import load_mission; print(load_mission('missions/custom/my_meeting.yaml').name)"
```

An invalid contract here is skipped by the menu rather than crashing it, so a
file that never shows up is a file that failed validation — run the check above
to see why.

## Custom *session* vs. custom *meeting type*

Most "custom" runs need no new file at all. A meeting type is the reusable
choreography (phases, rounds, roles); the subject, reference files, images, and
extra instructions are **session briefing**, supplied at startup on top of an
existing type (FORMATS.md §B.7). Author a new meeting type only when the
*choreography* differs — not when only the topic does.

## Field reference

[MISSION-CONTRACT.md](../../MISSION-CONTRACT.md) is authoritative for the
schema; [FORMATS.md](../FORMATS.md) Part A lists the six proven phase
choreographies worth copying.
