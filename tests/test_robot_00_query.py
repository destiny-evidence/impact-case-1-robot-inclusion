"""Tests for the query robot (`app.robots.robot_00_query`): automation query and the six loop sub-cases."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from destiny_sdk.enhancements import AnnotationEnhancement, Enhancement, TombstoneEnhancement
from destiny_sdk.robots import LinkedRobotError

from app.robots.robot_00_query import EnhancementRunner, EnhancementRunnerMode
from tests.conftest import DEFAULT_QUERY_TEXT, FakeRepository, InstallSettings, MakeAnnotation, MakeReference, MakeSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from destiny_sdk.references import Reference
    from destiny_sdk.robots import EnhancementResultEntry

ROBOT_NAME = EnhancementRunner.NAME


def _make_runner(
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    mode: EnhancementRunnerMode,
    **settings_overrides: object,
) -> EnhancementRunner:
    install_settings(make_settings(**settings_overrides))
    return EnhancementRunner(name="query", mode=mode)


def _run_loop(runner: EnhancementRunner, repository: FakeRepository) -> tuple[bool, list[EnhancementResultEntry]]:
    """Run one `_loop_task` iteration; returns its result and every submitted entry."""
    did_work = asyncio.run(runner._loop_task())  # noqa: SLF001
    return did_work, repository.submitted


def _reference_with(
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
    *,
    values: list[bool],
    source: str = ROBOT_NAME,
) -> tuple[Reference, list[Enhancement]]:
    """Build a reference carrying our own annotations with the given boolean values."""
    reference = make_reference()
    reference.enhancements = [make_annotation(reference.id, source=source, value=value) for value in values]
    return reference, reference.enhancements


# --- _automation_query -------------------------------------------------------


def test_automation_query_match(fake_runner_dependencies: FakeRepository, make_settings: MakeSettings, install_settings: InstallSettings) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    assert runner.repository is fake_runner_dependencies  # hermetic: no real repository client

    automation = runner._automation_query()  # noqa: SLF001

    assert automation.robot_id == runner.settings.robot_id
    query = automation.query
    must = query["bool"]["must"]
    # Loop-breaker: only fires when an abstract actually changed
    assert must[0]["nested"]["path"] == "changeset.enhancements"
    assert must[0]["nested"]["query"]["term"]["changeset.enhancements.content.enhancement_type"] == "abstract"
    # Whitespace in the query file is flattened for `simple_query_string`
    assert must[1]["simple_query_string"]["query"] == DEFAULT_QUERY_TEXT
    assert must[1]["simple_query_string"]["fields"] == ["changeset.enhancements.content.text"]
    assert "must_not" not in query["bool"]


def test_automation_query_match_flattens_query_file(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match, query_text="air  pollution\nAND\thealth")
    assert runner.repository is fake_runner_dependencies

    query = runner._automation_query().query  # noqa: SLF001

    assert query["bool"]["must"][1]["simple_query_string"]["query"] == DEFAULT_QUERY_TEXT


def test_automation_query_unmatch(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.unmatch)
    assert runner.repository is fake_runner_dependencies

    query = runner._automation_query().query  # noqa: SLF001

    must = query["bool"]["must"]
    must_not = query["bool"]["must_not"]
    # Loop-breaker: only fires when an abstract actually changed
    assert must[0]["nested"]["query"]["term"]["changeset.enhancements.content.enhancement_type"] == "abstract"
    # Document currently carries the query annotation
    assert must[1] == {"term": {"enhancements.content.annotations.scheme": runner.settings.annotation_scheme_query}}
    assert must[2] == {"term": {"enhancements.content.annotations.label": runner.settings.annotation_label_query}}
    # The updated abstract no longer matches the search query
    assert must_not[0]["simple_query_string"]["query"] == DEFAULT_QUERY_TEXT
    assert must_not[0]["simple_query_string"]["fields"] == ["changeset.enhancements.content.text"]


# --- _loop_task: no batch -----------------------------------------------------


def test_loop_task_without_batch_returns_false(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is False
    assert submissions == []


# --- _loop_task: the six documented sub-cases ---------------------------------


def test_case1_match_no_existing_annotation_creates_inclusion(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    reference = make_reference()
    fake_runner_dependencies.queue_batch([reference])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    (enhancement,) = [e for e in submissions if isinstance(e, Enhancement)]
    assert enhancement.reference_id == reference.id
    assert enhancement.source == ROBOT_NAME
    assert enhancement.robot_version == runner.settings.robot_version
    assert enhancement.supersedes is None
    assert isinstance(enhancement.content, AnnotationEnhancement)
    (annotation,) = enhancement.content.annotations
    assert annotation.scheme == runner.settings.annotation_scheme_query
    assert annotation.label == runner.settings.annotation_label_query
    assert annotation.value is True


@pytest.mark.parametrize(
    ("mode", "existing"),
    [
        # Case 3: existing True annotation -> supersede with a fresh True annotation
        pytest.param(EnhancementRunnerMode.match, True, id="case3-supersede-existing-true"),
        # Case 4: existing False annotation -> supersede with True annotation
        pytest.param(EnhancementRunnerMode.match, False, id="case4-flip-false-to-true"),
    ],
)
def test_match_supersedes_existing_annotation(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
    mode: EnhancementRunnerMode,
    existing: bool,
) -> None:
    runner = _make_runner(make_settings, install_settings, mode)
    reference, (old,) = _reference_with(make_reference, make_annotation, values=[existing])
    fake_runner_dependencies.queue_batch([reference])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    (enhancement,) = [e for e in submissions if isinstance(e, Enhancement)]
    assert enhancement.reference_id == reference.id
    assert enhancement.supersedes == old.id
    (annotation,) = enhancement.content.annotations
    assert annotation.value is True


@pytest.mark.parametrize(
    "existing",
    [
        # Case 5: annotation no longer justified -> tombstone it
        pytest.param(True, id="case5-retract-true"),
        # Case 6: open exclusion chain closed by the unmatch query -> tombstone it
        pytest.param(False, id="case6-retract-false"),
    ],
)
def test_unmatch_retracts_existing_annotation(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
    existing: bool,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.unmatch)
    reference, (old,) = _reference_with(make_reference, make_annotation, values=[existing])
    fake_runner_dependencies.queue_batch([reference])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    (retraction,) = [e for e in submissions if isinstance(e, Enhancement)]
    assert isinstance(retraction.content, TombstoneEnhancement)
    assert retraction.reference_id == reference.id
    assert retraction.supersedes == old.id
    assert retraction.source == ROBOT_NAME


# --- _loop_task: supersession and edge behaviour ------------------------------


def test_live_annotation_that_supersedes_is_still_the_chain(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
) -> None:
    """ODS sends only live records: a live annotation may itself carry `supersedes`; the robot supersedes that live one."""
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    reference = make_reference()
    live = make_annotation(reference.id, source=ROBOT_NAME, value=True, supersedes=uuid4())
    reference.enhancements = [live]
    fake_runner_dependencies.queue_batch([reference])

    _did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    (enhancement,) = [e for e in submissions if isinstance(e, Enhancement)]
    assert enhancement.supersedes == live.id


def test_superseded_annotation_in_payload_is_inconsistent_data(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
) -> None:
    """
    The robot runs `find_enhancements(skip_superseded=True)`, i.e. it trusts ODS to send live records only.

    If a batch nevertheless contains both a superseded and its live successor, that reads as two
    chains and is reported as an error rather than silently superseding one of them.
    """
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    reference = make_reference()
    old = make_annotation(reference.id, source=ROBOT_NAME, value=True)
    live = make_annotation(reference.id, source=ROBOT_NAME, value=True, supersedes=old.id)
    reference.enhancements = [old, live]
    fake_runner_dependencies.queue_batch([reference])

    _did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert len(submissions) == 1
    (error,) = submissions
    assert isinstance(error, LinkedRobotError)
    assert "more than one" in error.message


def test_enhancements_from_other_sources_are_ignored(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
) -> None:
    """Annotations by other robots share the scheme/label but must not count as our chain."""
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    reference, _ = _reference_with(make_reference, make_annotation, values=[True], source="some-other-robot")
    fake_runner_dependencies.queue_batch([reference])

    _did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    (enhancement,) = [e for e in submissions if isinstance(e, Enhancement)]
    assert enhancement.supersedes is None  # treated as case 1: no existing annotation of ours


def test_multiple_chains_fails_with_error(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    make_annotation: MakeAnnotation,
) -> None:
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    reference, _ = _reference_with(make_reference, make_annotation, values=[True, False])
    fake_runner_dependencies.queue_batch([reference])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    assert len(submissions) == 1
    (error,) = submissions
    assert isinstance(error, LinkedRobotError)
    assert error.reference_id == reference.id


def test_per_reference_exception_is_reported_as_error(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing reference becomes a `LinkedRobotError`; the batch still completes."""
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.match)
    bad = make_reference()
    good = make_reference()

    def _exploding_find(reference: Reference, *_args: object, **_kwargs: object) -> Iterator[Enhancement]:
        if reference.id == bad.id:
            msg = "kaboom"
            raise ValueError(msg)
        return iter(())

    monkeypatch.setattr("app.robots.robot_00_query.find_enhancements", _exploding_find)
    fake_runner_dependencies.queue_batch([bad, good])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    errors = [e for e in submissions if isinstance(e, LinkedRobotError)]
    assert [e.reference_id for e in errors] == [bad.id]
    assert "kaboom" in errors[0].message
    enhancements = [e for e in submissions if isinstance(e, Enhancement)]
    assert [e.reference_id for e in enhancements] == [good.id]  # good reference treated as case 1


def test_batch_is_always_submitted(
    fake_runner_dependencies: FakeRepository,
    make_settings: MakeSettings,
    install_settings: InstallSettings,
    make_reference: MakeReference,
) -> None:
    """Even a reference the unmatch mode cannot act on (no annotation) closes the batch with one submission."""
    runner = _make_runner(make_settings, install_settings, EnhancementRunnerMode.unmatch)
    reference = make_reference()  # unmatch query guarantees an annotation, but if data is off, the batch must still close
    fake_runner_dependencies.queue_batch([reference])

    did_work, submissions = _run_loop(runner, fake_runner_dependencies)

    assert did_work is True
    assert len(fake_runner_dependencies.submissions) == 1  # one batch-level submission call
    # The un-actionable reference falls through to the `else` branch and is reported as an error
    assert len(submissions) == 1
    assert isinstance(submissions[0], LinkedRobotError)
