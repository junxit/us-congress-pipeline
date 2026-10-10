"""Tests for comparing what two toolchains render.

The comparison is worth something only if it cannot report a match it did not
see, so these are weighted towards the ways it could: an output on one side
only, a corpus rendered on one side only, an empty cache -- two empty manifests
compare as identical -- and inputs that fail the same way on both sides.

That last one happened. The first version passed a roll call's filename prefix
where ``votes.parse`` wants the chamber constant, so all 19,471 votes raised the
same ValueError under both interpreters and matched perfectly.
"""

from __future__ import annotations

import json
from pathlib import Path

from uscongress import config, votes
from uscongress.jobs import renderdigest


def _manifest(directory: Path, corpus: str, outputs: dict[str, str]) -> None:
    """Write one corpus manifest the way ``run`` does.

    Args:
        directory: Run output directory.
        corpus: Corpus name.
        outputs: Output key to digest.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{corpus}.json").write_text(json.dumps(outputs, sort_keys=True))


def test_equal_structures_hash_alike_whatever_their_order() -> None:
    """A manifest assembled in a different order is not a different render."""
    assert renderdigest.digest({"a": 1, "b": [2]}) == renderdigest.digest({"b": [2], "a": 1})
    assert renderdigest.digest("text") == renderdigest.digest(b"text")
    assert renderdigest.digest("text") != renderdigest.digest("text\n")


def test_samples_nest_so_a_quick_run_checks_against_a_thorough_one() -> None:
    """Every 50th item is also every 5th, whatever order they arrive in."""
    items = [f"BILLSTATUS-{n}.xml" for n in range(1000)]

    assert set(renderdigest.every(items, 50)) <= set(renderdigest.every(items, 5))
    assert renderdigest.every(reversed(items), 10) == renderdigest.every(items, 10)


def test_votes_are_parsed_as_their_chamber_not_their_file_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    """The bug that made every vote an identical error, and so a perfect match."""
    folder = tmp_path / "votes" / "108"
    folder.mkdir(parents=True)
    (folder / "house-108-1-0001.xml").write_bytes(b"<x/>")
    (folder / "senate-108-1-0002.xml").write_bytes(b"<x/>")
    seen: list[str] = []

    def _parse(payload: bytes, chamber: str) -> str:
        seen.append(chamber)
        return chamber

    monkeypatch.setattr(renderdigest.votes_text, "parse", _parse)
    monkeypatch.setattr(renderdigest.votes_text, "roll_markdown", lambda roll: f"vote {roll}")

    out = renderdigest.votes_corpus(tmp_path)

    assert seen == [votes.HOUSE, votes.SENATE]
    assert not any(value.startswith("ERROR") for value in out.values())


def test_a_difference_and_a_one_sided_output_both_count(tmp_path: Path, capsys) -> None:
    """An output on one side only is a difference, not something to skip."""
    _manifest(tmp_path / "a", "votes", {"v1": "x", "v2": "y", "v3": "z"})
    _manifest(tmp_path / "b", "votes", {"v1": "x", "v2": "CHANGED"})

    assert renderdigest.compare(tmp_path / "a", tmp_path / "b") == 2
    assert "v2" in capsys.readouterr().out


def test_a_corpus_rendered_on_one_side_only_is_reported(tmp_path: Path, capsys) -> None:
    """Whichever side it is on: walking only the left would miss the right's."""
    _manifest(tmp_path / "a", "votes", {"v1": "x"})
    _manifest(tmp_path / "b", "votes", {"v1": "x"})
    _manifest(tmp_path / "b", "record", {"r1": "x"})

    assert renderdigest.compare(tmp_path / "a", tmp_path / "b") == 1
    assert "record: only in" in capsys.readouterr().out


def test_an_empty_cache_warns_rather_than_matching_silently(tmp_path: Path, capsys) -> None:
    """Two empty manifests compare as identical, which proves nothing."""
    meta = renderdigest.run(tmp_path / "out", raw=tmp_path / "nothing-here")

    assert all(meta[name]["outputs"] == 0 for name in renderdigest.CORPORA)
    assert capsys.readouterr().out.count("WARNING: nothing cached") == len(renderdigest.CORPORA)


def test_the_cache_location_is_put_back(tmp_path: Path) -> None:
    """Vote paths read ``config.RAW_DIR``, so a run repoints it and must restore it."""
    before = config.RAW_DIR

    renderdigest.run(tmp_path / "out", raw=tmp_path / "elsewhere", corpora=("votes",))

    assert config.RAW_DIR == before
