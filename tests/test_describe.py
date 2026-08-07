"""Tests for repository descriptions and GitHub topics.

The README explains a repository to someone already looking at it. The
description and topics decide whether anyone gets that far -- they are what a
search result and a repository listing show. Thirteen repositories named
``us-congress-bills-114`` with nothing between them are indistinguishable from
each other and from an abandoned scratch directory.
"""

from __future__ import annotations

import pytest

from uscongress.jobs.describe import (
    MAX_TOPICS,
    _TOPIC,
    metadata_for,
    repositories,
)

NAMES = ("us-congress-pipeline", "us-congress-code", "us-congress-bills-113")


@pytest.mark.parametrize("name", NAMES)
def test_every_topic_is_legal(name: str) -> None:
    """GitHub silently drops a malformed topic.

    Lowercase alphanumerics and hyphens, 50 characters at most. An uppercase
    letter or a space leaves the repository looking configured when it is not.
    """
    for topic in metadata_for(name).topics:
        assert _TOPIC.match(topic), topic


@pytest.mark.parametrize("name", NAMES)
def test_topic_count_is_within_the_limit(name: str) -> None:
    """GitHub accepts twenty; beyond that the call fails."""
    assert 0 < len(metadata_for(name).topics) <= MAX_TOPICS


@pytest.mark.parametrize("name", NAMES)
def test_topics_are_unique(name: str) -> None:
    """A duplicate wastes one of the twenty and reads as carelessness."""
    topics = metadata_for(name).topics
    assert len(topics) == len(set(topics))


@pytest.mark.parametrize("name", NAMES)
def test_description_is_present_and_fits(name: str) -> None:
    """A description is truncated in listings, so the first words must carry it."""
    description = metadata_for(name).description
    assert 40 < len(description) <= 350
    assert description[0].isupper()


def test_a_bills_repository_names_its_congress() -> None:
    """Twelve otherwise identical repositories have to be told apart."""
    meta = metadata_for("us-congress-bills-113")

    assert "113th Congress" in meta.description
    assert "congress-113" in meta.topics


def test_bills_repositories_differ_from_each_other() -> None:
    """A shared description would make the listing useless."""
    assert metadata_for("us-congress-bills-113").description != metadata_for(
        "us-congress-bills-114"
    ).description


def test_data_repositories_point_home() -> None:
    """The homepage is the one link GitHub shows beside the description."""
    assert metadata_for("us-congress-code").homepage.endswith("us-congress-pipeline")
    assert metadata_for("us-congress-bills-113").homepage.endswith("us-congress-pipeline")


def test_the_pipeline_does_not_point_at_itself() -> None:
    """A homepage linking the repository you are already on is noise."""
    assert metadata_for("us-congress-pipeline").homepage == ""


def test_public_domain_is_advertised_on_data_repositories_only() -> None:
    """The federal text is public domain; the pipeline is not.

    Tagging the pipeline that way would advertise terms that do not apply to it.
    """
    assert "public-domain" in metadata_for("us-congress-code").topics
    assert "public-domain" not in metadata_for("us-congress-pipeline").topics


def test_shards_are_expanded_in_numeric_order() -> None:
    """Sorting shard names as text puts the 109th after the 110th."""
    names = [n for n in repositories() if n.startswith("us-congress-bills-")]
    numbers = [int(n.rsplit("-", 1)[-1]) for n in names]

    assert numbers == sorted(numbers)


def test_an_unknown_repository_still_gets_something() -> None:
    """A repository added to the registry must not describe itself as nothing."""
    meta = metadata_for("us-congress-statutes")

    assert meta.description
    assert meta.topics
