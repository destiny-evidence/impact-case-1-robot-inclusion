"""
Test-wide environment setup and fixtures shared by all robot runner tests.

`app.classifiers.llm` calls `get_settings()` at module scope, so importing anything
under `app` requires the two settings that have no default. Set them here, before any
test module imports `app`.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, RobotEnhancementBatch
from destiny_sdk.visibility import Visibility

os.environ.setdefault("ROBOT_ID", str(uuid4()))
os.environ.setdefault("ROBOT_SECRET", "test-secret")
# Always disable telemetry for tests
os.environ["OTEL_ENABLED"] = "false"

if TYPE_CHECKING:
    from app.util.config import Settings

# Type aliases so test modules can annotate the factory fixtures below.
MakeSettings = Callable[..., "Settings"]
InstallSettings = Callable[["Settings"], "Settings"]
MakeReference = Callable[..., Reference]
MakeAnnotation = Callable[..., Enhancement]

# Settings fields the fixtures below pin explicitly; strip the matching env vars so
# values from the developer shell or CI cannot leak into `Settings(_env_file=None)`.
_PINNED_ENV_VARS = (
    "ROBOT_ID",
    "ROBOT_VERSION",
    "SEARCH_QUERY",
    "ANNOTATION_SCHEME_QUERY",
    "ANNOTATION_LABEL_QUERY",
    "ENHANCEMENT_VISIBILITY",
)

DEFAULT_QUERY_TEXT = "air pollution AND health"


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """
    Build hermetic `Settings` objects (no `.env` files, no ambient env vars).

    Usage: `settings = make_settings(query_text="asthma exposure", annotation_label_query="x")`.
    `query_text` is written to a temp file and wired to `search_query`; every other
    keyword is passed straight to `Settings`.
    """
    for var in _PINNED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    from app.util.config import Settings

    def _make(**overrides: object) -> Settings:
        query_text = overrides.pop("query_text", DEFAULT_QUERY_TEXT)
        query_file = tmp_path / "search-query.txt"
        query_file.write_text(query_text)
        args: dict[str, object] = {
            "robot_id": uuid4(),
            "robot_secret": "test-secret",
            "search_query": query_file,
        }
        args.update(overrides)
        return Settings(_env_file=None, **args)  # type: ignore[call-arg]

    return _make


@pytest.fixture
def install_settings(monkeypatch: pytest.MonkeyPatch):
    """Make a `Runner` subclass pick up custom settings via `app.util.runner.get_settings`."""

    def _install(settings: Settings) -> Settings:
        monkeypatch.setattr("app.util.runner.get_settings", lambda: settings)
        return settings

    return _install


class FakeRepository:
    """
    In-memory stand-in for `app.util.repository.Repository`.

    Serves queued batches through `get_next_batch()` and records every
    `submit_enhancements()` call in `.submissions` for assertions.
    """

    def __init__(self) -> None:
        self._batches: deque[tuple[RobotEnhancementBatch, list[Reference]]] = deque()
        self.submissions: list[tuple[RobotEnhancementBatch, list[EnhancementResultEntry]]] = []

    def queue_batch(self, references: Sequence[Reference]) -> RobotEnhancementBatch:
        batch = RobotEnhancementBatch(
            id=uuid4(),
            reference_storage_url=f"https://example.invalid/{uuid4()}/references.jsonl",
            result_storage_url=f"https://example.invalid/{uuid4()}/results.jsonl",
        )
        self._batches.append((batch, list(references)))
        return batch

    async def get_next_batch(self, batch_size: int | None = None) -> tuple[RobotEnhancementBatch | None, list[Reference] | None]:
        if not self._batches:
            return None, None
        return self._batches.popleft()

    async def submit_enhancements(self, batch_info: RobotEnhancementBatch, enhancements: Sequence[EnhancementResultEntry]) -> None:
        self.submissions.append((batch_info, list(enhancements)))

    @property
    def submitted(self) -> list[EnhancementResultEntry]:
        """All submitted entries across batches, in submission order."""
        return [entry for _, entries in self.submissions for entry in entries]


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_runner_dependencies(monkeypatch: pytest.MonkeyPatch, fake_repository: FakeRepository) -> FakeRepository:
    """
    Swap the network-bound `Repository` in `app.util.runner` for the fake.

    Construct any `Runner` subclass after requesting this fixture to get a runner
    whose `.repository` is the shared `FakeRepository`.
    """
    monkeypatch.setattr("app.util.runner.Repository", lambda *_args, **_kwargs: fake_repository)
    return fake_repository


@pytest.fixture
def make_reference():
    """Build minimal `Reference` objects: `make_reference(enhancements=[...])`."""

    def _make(enhancements: list[Enhancement] | None = None) -> Reference:
        return Reference(id=uuid4(), enhancements=enhancements)

    return _make


@pytest.fixture
def make_annotation():
    """
    Build annotation `Enhancement` objects as produced/consumed by the robots.

    Defaults match the query-robot settings defaults (`scheme="search"`,
    `label="destiny-ic1-inclusion"`); always pass `source` (the robot NAME whose
    filters match on it) and `reference_id`.
    """

    def _make(
        reference_id: UUID,
        *,
        source: str,
        scheme: str = "search",
        label: str = "destiny-ic1-inclusion",
        value: bool = True,
        supersedes: UUID | None = None,
        enhancement_id: UUID | None = None,
    ) -> Enhancement:
        return Enhancement(
            # Persisted enhancements as robots receive them always carry an id;
            # SDK default is None, so generate one unless a caller pins it.
            id=enhancement_id if enhancement_id is not None else uuid4(),
            reference_id=reference_id,
            source=source,
            visibility=Visibility.PUBLIC,
            robot_version="0.0.0-test",
            supersedes=supersedes,
            content=AnnotationEnhancement(
                annotations=[
                    BooleanAnnotation(
                        scheme=scheme,
                        label=label,
                        value=value,
                    ),
                ],
            ),
        )

    return _make
