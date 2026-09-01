"""Chunked inline delivery — the overflow path that needs no upload (step 4).

Truncation loses context permanently and an attachment can fail silently. N
sequential composer messages lose nothing and depend on nothing but the
composer, so they sit between "attachment verified" and "truncate" in the
delivery ladder.
"""

from __future__ import annotations

from frelan.transport.playwright_auto import (
    _MAX_PROMPT_CHUNKS,
    _chunk_prompt,
)


def test_a_prompt_that_fits_is_still_one_chunk():
    chunks = _chunk_prompt("short prompt", 9_000)
    assert len(chunks) == 1
    assert chunks[0].endswith("short prompt")
    assert "FINAL" in chunks[0]


def test_every_chunk_fits_the_composer_limit():
    prompt = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(60))
    chunks = _chunk_prompt(prompt, 9_000)

    assert chunks, "a 25k prompt must be chunkable within the budget"
    assert all(len(c) <= 9_000 for c in chunks)


def test_chunking_loses_no_content():
    prompt = "\n\n".join(f"para-{i} " + "y" * 300 for i in range(40))
    chunks = _chunk_prompt(prompt, 6_000)

    rejoined = "".join(
        c.split("]\n\n", 1)[1] for c in chunks
    )
    # Paragraph splits consume the separating blank line; content survives.
    for i in range(40):
        assert f"para-{i} " in rejoined


def test_parts_are_numbered_and_only_the_last_invites_a_response():
    prompt = "\n\n".join("z" * 900 for _ in range(20))
    chunks = _chunk_prompt(prompt, 5_000)

    assert len(chunks) > 1
    for c in chunks[:-1]:
        assert "DO NOT RESPOND YET" in c
    assert "FINAL" in chunks[-1]
    assert f"OF {len(chunks)}" in chunks[0]


def test_a_prompt_too_large_for_the_chunk_budget_is_refused():
    """Refusing lets the caller degrade to truncation instead of spamming."""
    prompt = "q" * (9_000 * (_MAX_PROMPT_CHUNKS + 3))
    assert _chunk_prompt(prompt, 9_000) == []


def test_a_useless_limit_is_refused_rather_than_looping():
    assert _chunk_prompt("anything", 0) == []
    assert _chunk_prompt("anything", 50) == []  # smaller than the header itself
