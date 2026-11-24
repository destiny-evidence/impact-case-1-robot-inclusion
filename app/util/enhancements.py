import logging
from collections.abc import Callable, Generator
from uuid import UUID

from destiny_sdk.enhancements import Enhancement, EnhancementContent, TombstoneEnhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import LinkedRobotError
from destiny_sdk.visibility import Visibility

from .util import list_or_none

_logger = logging.getLogger(__name__)


class FailureRateThresholdExceededError(RuntimeError):
    pass


def find_enhancements(
    reference: Reference,
    filters: dict[str, Callable[[Enhancement], bool]],
    check_reference_id: bool = True,
    skip_superseded: bool = False,
) -> Generator[tuple[str, Enhancement]]:
    """
    Get all non-superseded enhancements that match the filters.

    Typically, a robot should (and can) only supersede its own prior enhancements: therefore default to `check_reference_id=True`.
    By default, ODS only sends non-superseded records, which makes checking redundant: therefore default to `skip_superseded=False`.

    Example to find existing enhancements by this robot and inclusion enhancements:
    ```python
    enhancements = gather_dict(
        find_enhancements(
            reference,
            {
                # Find previous enhancements by this robot
                "self": lambda e: isinstance(e.content, LinkedDataEnhancement) and e.source == config.robot_name,
                # Find enhancements with inclusion label set to False
                "incl": lambda e: isinstance(enhancement.content, AnnotationEnhancement) and any((
                        isinstance(annotation, BooleanAnnotation)
                    and annotation.scheme == config.upstream_scheme
                    and annotation.label == config.upstream_label
                    and annotation.value == False  # noqa: E712
                    for annotation in e.content.annotations)),
            },
            check_reference_id=True,
            skip_superseded=True,
        ),
    )
    ```
    """
    # Reference without enhancements, end here.
    if not reference.enhancements:
        return

    # Build list of superseded enhancements
    superseded_ids: set[UUID]
    if skip_superseded:
        superseded_ids = set()
    else:
        superseded_ids = {enhancement.supersedes for enhancement in (reference.enhancements or []) if enhancement.supersedes is not None}

    for enhancement in reference.enhancements:
        # Skip superseded enhancement
        if enhancement.id in superseded_ids:
            continue
        # Skip enhancements not directly for this reference (e.g. enhancement for duplicate)
        if check_reference_id and enhancement.reference_id != reference.id:
            continue
        # Test for filters
        for key, check in filters.items():
            if check(enhancement):
                yield key, enhancement


def use_enhancement(key: str, enhancements: dict[str, list[Enhancement]], index: int = 0) -> Enhancement | None:
    """Pick an enhancement from the lookup we built with `find_enhancements()`."""
    if key not in enhancements or len(enhancements[key] or []) < (max(index, 0) + 1):
        return None
    return enhancements[key][index]


class ResultHandler:
    def __init__(
        self,
        robot_name: str,
        robot_version: str,
        enhancement_visibility: Visibility,
        max_batch_failure_rate: float | None = None,
        n_total: int | None = None,
        check_unique_reference_id: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or _logger
        self.robot_name = robot_name
        self.robot_version = robot_version
        self.enhancement_visibility = enhancement_visibility
        self.max_batch_failure_rate = max_batch_failure_rate
        self.n_total = n_total
        self.check_unique_reference_id = check_unique_reference_id

        self.enhancements: dict[UUID, Enhancement] = {}
        self.retractions: dict[UUID, Enhancement] = {}
        self.skipped: dict[UUID, str | None] = {}
        self.errors: dict[UUID, LinkedRobotError] = {}
        self.n_received = 0

    def _assert_unique_id(self, reference_id: UUID) -> bool:
        if self.check_unique_reference_id and (reference_id in self.enhancements or reference_id in self.retractions or reference_id in self.errors):
            raise AssertionError("There should only be one `Enhancement` (or error) per `Reference?`")
        return True

    def enhance(
        self,
        reference_id: UUID,
        content: EnhancementContent,
        supersedes: UUID | None = None,
        derived_from: UUID | list[UUID] | None = None,
    ) -> None:
        """Add enhancement to reference."""
        self.n_received += 1
        self._assert_unique_id(reference_id)
        self.enhancements[reference_id] = Enhancement(
            reference_id=reference_id,
            source=self.robot_name,
            visibility=self.enhancement_visibility,
            robot_version=self.robot_version,
            supersedes=supersedes,
            derived_from=list_or_none(derived_from),
            content=content,
        )

    def retract(
        self,
        reference_id: UUID,
        supersedes: UUID,
        reason: str | None = None,
        derived_from: UUID | list[UUID] | None = None,
    ) -> None:
        """Mark reference enhancement as obsolete ('tombstoned')."""
        self.n_received += 1
        self._assert_unique_id(reference_id)

        if supersedes is None:
            # This should never happen, checking just in case there's a runtime error
            self.fail(reference_id=reference_id, reason="Failed to set `TombstoneEnhancement` due to missing supersession ID.")
            return

        self.retractions[reference_id] = Enhancement(
            reference_id=reference_id,
            source=self.robot_name,
            visibility=self.enhancement_visibility,
            robot_version=self.robot_version,
            supersedes=supersedes,
            derived_from=list_or_none(derived_from),
            content=TombstoneEnhancement(reason=reason),
        )

    def skip(self, reference_id: UUID, reason: str | None = None) -> None:
        """Mark reference as neither failed, superseded, or enhanced."""
        self.n_received += 1
        self._assert_unique_id(reference_id)
        self.skipped[reference_id] = reason

    def fail(self, reference_id: UUID, reason: str | None = None) -> None:
        """Mark reference as failed."""
        self.n_received += 1
        self._assert_unique_id(reference_id)
        self.errors[reference_id] = LinkedRobotError(reference_id=reference_id, message=reason or "Encountered unspecified error.")

        if self.max_batch_failure_rate and self.n_total and (failure_rate := len(self.errors) / self.n_total) > self.max_batch_failure_rate:
            raise FailureRateThresholdExceededError(f"Failed to enhance over {failure_rate:.2%} of references.")

    def prepare_response(self) -> list[Enhancement | LinkedRobotError]:
        """Get prepared references to submit to ODS and log basic statistics."""
        self.logger.info(
            f"Received {self.n_received:,} ({(self.n_total or -1):,}) references of which "
            f"{len(self.enhancements):,} wil get an enhancement, "
            f"{len(self.retractions):,} will be retracted ('tombstoned'), "
            f"{len(self.errors):,} encountered an error during processing, and "
            f"{len(self.skipped):,} were skipped.",
        )
        return [*self.errors.values(), *self.enhancements.values(), *self.retractions.values()]
