"""Interpretation Layer — the Prompt Adapter.

Renders a canonical, engine-agnostic prompt into the form one target model
prefers. Pure functions, no I/O, no state: given the same prompt and the same
model identifier, ``adapt`` returns the same string.

The Mission Contract is authored once and never varies by engine
(``mission_contract.py``), and ``prompt_renderer.py`` renders it without
consulting the execution engine. This module is the *only* place where the
target model influences wording — which is what keeps every engine
interchangeable and keeps engine-specific phrasing out of the renderer.

**Additive by construction.** ``adapt`` appends calibration guidance and never
edits, reorders, or removes any part of the canonical prompt. That is the load-
bearing invariant: adaptation may change *how* a mission is expressed, never
*what* it requires. A prompt that softened a constraint or dropped an acceptance
criterion would be a correctness defect, not a style choice, so
``test_prompt_adapter.py`` asserts the canonical text survives verbatim rather
than trusting the rendering code to be careful.

Relationship to ``AI-Library/Skills/Prompt-Adapter/``: that package is the
portable, vendor-agnostic *specification*; this module is *an implementation* of
it for this runtime. Stable contract, replaceable implementation — they are not
duplicates of one asset, and this module is free to be deleted or rewritten
without touching the spec.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Profile:
    """How a model prefers to be addressed.

    These five dimensions are the stable vocabulary. Preset *values* and the
    model table below are expected to churn as models and prompting technique
    evolve; the dimension names are the contract consumers depend on.
    """

    autonomy: str  # high | medium | low
    verbosity: str  # low | medium | high
    decomposition: str  # false | optional | true | mandatory
    examples: bool
    tone_guidance: str  # omitted | required


# The safe middle. Every unlisted model resolves here — see resolve_profile.
DEFAULT = Profile(
    autonomy="medium",
    verbosity="medium",
    decomposition="optional",
    examples=False,
    tone_guidance="omitted",
)

PROFILES: dict[str, Profile] = {
    "default": DEFAULT,
    # Terse and autonomous — for models that degrade when over-instructed.
    "P1": Profile("high", "low", "false", False, "omitted"),
    # Balanced. Same values as `default`, kept distinct so "assessed as middle"
    # reads differently from "never assessed" in the resolution record.
    "P2": Profile("medium", "medium", "optional", False, "omitted"),
    "P3": Profile("medium", "high", "true", True, "omitted"),
    # Maximum scaffolding — for models that need the work pre-broken.
    "P4": Profile("low", "high", "mandatory", True, "omitted"),
    # Generative, voice-sensitive work.
    "P5": Profile("high", "medium", "false", True, "required"),
}

# An OVERRIDE LIST, not a registry. Absence is the normal case and is never an
# error. Enumerating every model is a losing game: one provider alone ships nine
# active models as of 2026-08, and the list turns over on a timescale of weeks.
# Entries exist only to record an assessed deviation from DEFAULT.
#
# The bare names are this runtime's browser-engine identifiers
# (AssignedEngine.execution_engine); the hyphenated ones are release-level ids
# for when a mission names a specific model. Matching is exact — see
# resolve_profile for why nothing here prefix-matches.
MODEL_OVERRIDES: dict[str, str] = {
    "claude": "P1",
    "claude-opus-5": "P1",
    "claude-sonnet-5": "P2",
    "claude-fable-5": "P5",
    "chatgpt": "P2",
    "gemini": "P2",
}


def resolve_profile(target_model: str) -> tuple[Profile, str]:
    """Resolve ``target_model`` to a profile and the label that produced it.

    Returns ``(profile, label)`` where ``label`` is the preset name, or
    ``"default"`` when the model is unlisted.

    **Fail-open, exact-match.** An unrecognised model resolves to ``DEFAULT``
    and the run proceeds — an unknown model is a stale table, not a safety
    condition, and halting a mission over one would make the table load-bearing.
    Matching is exact on the normalised string with no prefix or fuzzy fallback:
    ``"claude-opus-5"`` must not silently inherit whatever ``"claude"`` happens
    to say today, because that would make adding a bare family name quietly
    re-profile every release under it.
    """
    key = target_model.strip().lower()
    label = MODEL_OVERRIDES.get(key, "default")
    return PROFILES[label], label


def _directives(profile: Profile) -> list[str]:
    """The calibration lines implied by a profile, in a fixed order.

    Order is fixed rather than derived so that two runs with the same profile
    are byte-identical — the prompt is part of the prompt cache key upstream and
    a reordered block is a cache miss.
    """
    lines: list[str] = []

    if profile.autonomy == "high":
        lines.append(
            "- Work autonomously. Decide and act; do not ask which option to take."
        )
    elif profile.autonomy == "low":
        lines.append(
            "- Take one step at a time and state what you did before continuing."
        )

    if profile.verbosity == "low":
        lines.append("- Be terse. Lead with the answer; omit preamble and recap.")
    elif profile.verbosity == "high":
        lines.append(
            "- Show your reasoning and state assumptions explicitly as you go."
        )

    if profile.decomposition in ("true", "mandatory"):
        lines.append(
            "- Break the work into numbered steps and address each one in order."
        )
        if profile.decomposition == "mandatory":
            lines.append("- Do not begin until the full step list is written out.")

    if profile.examples:
        lines.append("- Include a concrete worked example for each claim you make.")

    if profile.tone_guidance == "required":
        lines.append(
            "- Match register and voice to the audience implied by the objective."
        )

    return lines


def adapt(canonical_prompt: str, target_model: str) -> str:
    """Render ``canonical_prompt`` into the form ``target_model`` prefers.

    The canonical prompt is returned **unchanged** with calibration guidance
    appended; nothing in it is edited or dropped. A profile that implies no
    directives (the common ``default`` case) returns the prompt byte-identical,
    so an unlisted model costs nothing rather than acquiring filler.
    """
    profile, _ = resolve_profile(target_model)
    lines = _directives(profile)
    if not lines:
        return canonical_prompt

    return (
        canonical_prompt.rstrip("\n")
        + "\n\n## Response Calibration\n\n"
        + "\n".join(lines)
        + "\n"
    )
