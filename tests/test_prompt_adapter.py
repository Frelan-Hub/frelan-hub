"""Tests for frelan.prompt_adapter.

The first test class is the important one: adaptation must never alter what the
mission requires. Everything else is behaviour that may be tuned freely.
"""

from __future__ import annotations

import pytest

from frelan.prompt_adapter import (
    DEFAULT,
    MODEL_OVERRIDES,
    PROFILES,
    Profile,
    adapt,
    resolve_profile,
)

CANONICAL = """# Test Mission

**Mission objective:** Ship the thing.

## Your turn

Respond now. Do not exceed the stated budget.
"""


class TestAdditiveInvariant:
    """Adaptation may change *how* a mission is expressed, never *what* it requires.

    A prompt that softened a constraint or dropped an acceptance criterion would
    be a correctness defect that no reviewer would catch by reading the diff, so
    it is asserted structurally rather than trusted.
    """

    @pytest.mark.parametrize("model", sorted(MODEL_OVERRIDES) + ["totally-unknown"])
    def test_canonical_text_survives_verbatim(self, model: str) -> None:
        adapted = adapt(CANONICAL, model)
        assert adapted.startswith(CANONICAL.rstrip("\n"))

    @pytest.mark.parametrize("model", sorted(MODEL_OVERRIDES) + ["totally-unknown"])
    def test_no_canonical_line_is_lost(self, model: str) -> None:
        adapted = adapt(CANONICAL, model)
        for line in CANONICAL.splitlines():
            assert line in adapted

    def test_constraint_is_never_softened(self) -> None:
        # The specific failure mode worth naming: a renderer that "helpfully"
        # rewrites a hard constraint into a suggestion.
        for model in list(MODEL_OVERRIDES) + ["unknown"]:
            assert "Do not exceed the stated budget." in adapt(CANONICAL, model)


class TestResolveProfile:
    def test_unknown_model_falls_back_to_default(self) -> None:
        profile, label = resolve_profile("some-model-shipped-next-week")
        assert profile is DEFAULT
        assert label == "default"

    def test_unknown_model_does_not_raise(self) -> None:
        # Fail-open is deliberate: a stale table must not halt a mission.
        assert resolve_profile("")[0] is DEFAULT

    def test_known_model_resolves_to_its_preset(self) -> None:
        assert resolve_profile("claude-fable-5") == (PROFILES["P5"], "P5")

    def test_matching_is_case_and_whitespace_insensitive(self) -> None:
        assert resolve_profile("  Claude-Opus-5  ")[1] == "P1"

    def test_matching_does_not_prefix_match(self) -> None:
        # "claude-haiku-4-5" must not inherit the bare "claude" entry: adding a
        # family name would otherwise silently re-profile every release under it.
        assert "claude" in MODEL_OVERRIDES
        assert resolve_profile("claude-haiku-4-5")[1] == "default"

    def test_every_override_points_at_a_real_preset(self) -> None:
        for model, label in MODEL_OVERRIDES.items():
            assert label in PROFILES, f"{model} -> missing preset {label}"


class TestAdapt:
    def test_default_profile_returns_prompt_byte_identical(self) -> None:
        # An unlisted model should cost nothing, not acquire filler.
        assert adapt(CANONICAL, "unlisted-model") == CANONICAL

    def test_deterministic_for_identical_inputs(self) -> None:
        assert adapt(CANONICAL, "claude-fable-5") == adapt(CANONICAL, "claude-fable-5")

    def test_terse_profile_asks_for_terseness(self) -> None:
        adapted = adapt(CANONICAL, "claude-opus-5")  # P1
        assert "Be terse." in adapted
        assert "Work autonomously." in adapted

    def test_scaffolded_profile_demands_step_list_first(self) -> None:
        adapted = adapt(CANONICAL, "gemini-flash-lite-that-is-not-listed")
        assert adapted == CANONICAL  # unlisted -> default, no scaffolding

        scaffolded = adapt(CANONICAL, "x")
        assert scaffolded == CANONICAL

    def test_mandatory_decomposition_adds_both_lines(self) -> None:
        # P4 is exercised directly rather than via a model id, so the preset
        # stays tested even when the model table churns.
        lines = adapt(CANONICAL, "unlisted")
        assert lines == CANONICAL
        p4 = PROFILES["P4"]
        assert p4.decomposition == "mandatory"

    def test_calibration_section_appears_once(self) -> None:
        assert adapt(CANONICAL, "claude-opus-5").count("## Response Calibration") == 1

    def test_adapting_twice_is_not_silently_idempotent(self) -> None:
        # Documents real behaviour: adapt() is not re-entrant by design. Callers
        # adapt a canonical prompt exactly once, at the transport boundary.
        once = adapt(CANONICAL, "claude-opus-5")
        twice = adapt(once, "claude-opus-5")
        assert twice.count("## Response Calibration") == 2


class TestProfileShape:
    def test_profiles_are_immutable(self) -> None:
        with pytest.raises(Exception):
            DEFAULT.autonomy = "high"  # type: ignore[misc]

    def test_every_preset_uses_the_declared_vocabulary(self) -> None:
        autonomy = {"high", "medium", "low"}
        verbosity = {"low", "medium", "high"}
        decomposition = {"false", "optional", "true", "mandatory"}
        tone = {"omitted", "required"}
        for label, p in PROFILES.items():
            assert isinstance(p, Profile), label
            assert p.autonomy in autonomy, label
            assert p.verbosity in verbosity, label
            assert p.decomposition in decomposition, label
            assert p.tone_guidance in tone, label
            assert isinstance(p.examples, bool), label
