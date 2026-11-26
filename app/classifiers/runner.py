from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import cached_property
from typing import cast
from uuid import UUID

from destiny_sdk.enhancements import (
    AbstractContentEnhancement,
    AnnotationEnhancement,
    BibliographicMetadataEnhancement,
    BooleanAnnotation,
    Enhancement,
    TombstoneEnhancement,
)
from destiny_sdk.references import Reference
from destiny_sdk.robots import RobotAutomationIn

from app.classifiers import load_classifier
from app.util.enhancements import ResultHandler, find_enhancements
from app.util.runner import Runner
from app.util.util import gather_dict


class ClassificationRunner(Runner, ABC):
    """
    Runner wrapper to handle all cases for classification tasks listening on a parent source.

    This runner trusts that the parent runner upstream *always* writes an updated or new annotation
    when this self runner should (re-)classify a reference.
    This runner therefore explicitly does *not* check for changing abstracts directly.
    This runner will only accept exactly one parent annotation chain (incl Tombstone) and otherwise respond with an error.

    Covered cases:
         Case: has title and abstract
      1.1) Latest parent annotation is True and existing self is True      -> Apply classifier -> supersede with label
      2.1) Latest parent annotation is True and existing self is False     -> Apply classifier -> supersede with label
      3.1) Latest parent annotation is True and existing self is Tombstone -> Apply classifier -> supersede with label
      4.1) Latest parent annotation is True and no existing self exists    -> Apply classifier -> supersede with label

          Case: missing title or abstract
      1.2) Latest parent annotation is True and existing self is True      -> Supersede with Tombstone
      2.2) Latest parent annotation is True and existing self is False     -> Supersede with Tombstone
      3.2) Latest parent annotation is True and existing self is Tombstone -> Do nothing
      4.2) Latest parent annotation is True and no existing self exists    -> Do nothing

      5) Latest parent annotation is False and existing self is True      -> Supersede with Tombstone
      6) Latest parent annotation is False and existing self is False     -> Supersede with Tombstone
      7) Latest parent annotation is False and existing self is Tombstone -> Do nothing
      8) Latest parent annotation is False and no existing self exists    -> Do nothing

      9) Latest parent annotation is Tombstone and existing self is True      -> Supersede with Tombstone
     10) Latest parent annotation is Tombstone and existing self is False     -> Supersede with Tombstone
     11) Latest parent annotation is Tombstone and existing self is Tombstone -> Do nothing
     12) Latest parent annotation is Tombstone and no existing self exists    -> Do nothing

    Cases 5–8 should have been a Tombstone upstream, so treating 9–12 the same way.

    Produces per-reference error on multiple parent annotation chains with this scheme/label combination.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.classifier = load_classifier(path=self.settings.model_prefilter)

    @property
    @abstractmethod
    def parent_source(self) -> str: ...

    @property
    @abstractmethod
    def self_source(self) -> str: ...

    def _automation_query(self) -> RobotAutomationIn:
        """Trigger whenever an enhancement from the parent robot changes on a reference."""
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "nested": {
                    "path": "changeset.enhancements",
                    "query": {"term": {"changeset.enhancements.source": self.parent_source}},
                }
            },
        )

    @abstractmethod
    async def _handle_classification(self, results: ResultHandler, needs_classification: list[tuple[UUID, dict[str, list[Enhancement]]]]) -> None:
        # 1.1.1) Latest parent annotation is True and existing self is True      -> Apply classifier -> true: supersede with label
        # 1.1.2) Latest parent annotation is True and existing self is True      -> Apply classifier -> false: supersede with label
        # 2.1.1) Latest parent annotation is True and existing self is False     -> Apply classifier -> true: supersede with label
        # 2.1.2) Latest parent annotation is True and existing self is False     -> Apply classifier -> false: supersede with label
        # 3.1.1) Latest parent annotation is True and existing self is Tombstone -> Apply classifier -> true: supersede with label
        # 3.1.2) Latest parent annotation is True and existing self is Tombstone -> Apply classifier -> false: supersede with label
        # 4.1.1) Latest parent annotation is True and no existing self exists    -> Apply classifier -> true: create enhancement
        # 4.1.2) Latest parent annotation is True and no existing self exists    -> Apply classifier -> false: create enhancement
        ...

    @cached_property
    def enhancement_filter(self) -> dict[str, Callable[[Enhancement], bool]]:
        return {
            # Include annotations by parent runner
            "parent-incl": lambda e: (
                e.source == self.parent_source
                and isinstance(e.content, AnnotationEnhancement)
                and any(
                    (
                        isinstance(annotation, BooleanAnnotation) and annotation.value == True  # noqa: E712
                        for annotation in e.content.annotations
                    ),
                )
            ),
            # Excluded or "tombstoned" annotations by parent runner
            "parent-excl": lambda e: (
                e.source == self.parent_source
                and (
                    isinstance(e.content, TombstoneEnhancement)
                    or (
                        isinstance(e.content, AnnotationEnhancement)
                        and any(
                            (
                                isinstance(annotation, BooleanAnnotation) and annotation.value == False  # noqa: E712
                                for annotation in e.content.annotations
                            ),
                        )
                    )
                )
            ),
            "self-anno": lambda e: isinstance(e.content, AnnotationEnhancement) and e.source == self.self_source,
            "self-tomb": lambda e: isinstance(e.content, TombstoneEnhancement) and e.source == self.self_source,
            "self": lambda e: e.source == self.self_source,
            # "ta": lambda e: isinstance(e.content, AbstractContentEnhancement) or isinstance(e.content, BibliographicMetadataEnhancement),
            "title": lambda e: isinstance(e.content, BibliographicMetadataEnhancement) and e.content.title is not None,
            "abstract": lambda e: isinstance(e.content, AbstractContentEnhancement) and e.content.abstract is not None,
        }

    def _handle_classification_free(self, results: ResultHandler, references: list[Reference]) -> list[tuple[UUID, dict[str, list[Enhancement]]]]:

        needs_classification: list[tuple[UUID, dict[str, list[Enhancement]]]] = []

        # First loop handles all cases that don't need classification
        for reference in references:
            try:
                existing_enhancements = gather_dict(find_enhancements(reference, self.enhancement_filter, check_reference_id=True, skip_superseded=True))

                # 5) Latest parent annotation is False and existing self is True       -> Supersede with Tombstone
                # 9) Latest parent annotation is Tombstone and existing self is True   -> Supersede with Tombstone
                # 6) Latest parent annotation is False and existing self is False      -> Supersede with Tombstone
                # 10) Latest parent annotation is Tombstone and existing self is False -> Supersede with Tombstone
                if (
                    not existing_enhancements.get("parent-incl")
                    and len(existing_enhancements.get("parent-excl", [])) == 1
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    # existing_enhancements.get("abstract") not relevant
                ):
                    results.retract(
                        reference_id=reference.id,
                        supersedes=cast("UUID", existing_enhancements["self-anno"][0].id),
                        derived_from=[
                            parent_enhancement.id for parent_enhancement in existing_enhancements["parent-excl"] if parent_enhancement.id is not None
                        ],
                        reason="Continued upstream exclusion",
                    )

                # 7) Latest parent annotation is False and existing self is Tombstone -> Do nothing
                # 8) Latest parent annotation is False and no existing self exists    -> Do nothing
                # 11) Latest parent annotation is Tombstone and existing self is Tombstone -> Do nothing
                # 12) Latest parent annotation is Tombstone and no existing self exists    -> Do nothing
                elif (
                    not existing_enhancements.get("parent-incl")
                    and len(existing_enhancements.get("parent-excl", [])) == 1
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    # existing_enhancements.get("abstract") not relevant
                ):
                    results.skip(reference_id=reference.id, reason=None)

                #   Case: missing title or abstract
                # 1.2) Latest parent annotation is True and existing self is True      -> Supersede with Tombstone
                # 2.2) Latest parent annotation is True and existing self is False     -> Supersede with Tombstone
                elif (
                    len(existing_enhancements.get("parent-incl", [])) == 1
                    and not existing_enhancements.get("parent-excl")
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    and self._enhancements_to_text(existing_enhancements) is None  # title_abstract not valid
                ):
                    results.retract(
                        reference_id=reference.id,
                        supersedes=cast("UUID", existing_enhancements["self-anno"][0].id),
                        derived_from=[
                            parent_enhancement.id for parent_enhancement in existing_enhancements["parent-excl"] if parent_enhancement.id is not None
                        ],
                        reason="Missing title or abstract",
                    )

                #   Case: missing title or abstract
                # 3.2) Latest parent annotation is True and existing self is Tombstone -> do nothing
                # 4.2) Latest parent annotation is True and no existing self exists    -> do nothing
                elif (
                    len(existing_enhancements.get("parent-incl", [])) == 1
                    and not existing_enhancements.get("parent-excl")
                    and not existing_enhancements.get("self-anno")
                    and len(existing_enhancements.get("self-tomb", [])) == 1
                    and self._enhancements_to_text(existing_enhancements) is None  # title_abstract not valid
                ):
                    results.skip(reference_id=reference.id, reason=None)

                #  Case: has title AND abstract
                # 1.1) Latest parent annotation is True and existing self is True      -> Apply classifier -> supersede with (true: label, false: Tombstone)
                # 2.1) Latest parent annotation is True and existing self is False     -> Apply classifier -> supersede with (true: label, false: Tombstone)
                # 3.1) Latest parent annotation is True and existing self is Tombstone -> Apply classifier -> true: supersede with label, false: do nothing
                # 4.1) Latest parent annotation is True and no existing self exists    -> Apply classifier -> true: create enhancement, false: do nothing
                elif (
                    len(existing_enhancements.get("parent-incl", [])) == 1
                    and not existing_enhancements.get("parent-excl")
                    and len(existing_enhancements.get("self", [])) < 2  # has one or none self annotation (tombstone or annotation)
                    and self._enhancements_to_text(existing_enhancements) is not None  # has valid title_abstract
                ):
                    needs_classification.append((reference.id, existing_enhancements))

                # Inconsistent data -> Error!
                else:
                    results.fail(reference_id=reference.id, reason="Undefined constellation of prior enhancements!")

            except Exception as e:  # noqa: BLE001
                results.fail(reference_id=reference.id, reason=f"Failed to process reference: {e}")
        return needs_classification

    # @lru_cache(maxsize=10)
    def _enhancements_to_text(self, enhancements: dict[str, list[Enhancement]]) -> str | None:
        if not enhancements.get("title") or not enhancements.get("abstract"):
            return None
        text = (
            f"{cast('BibliographicMetadataEnhancement', enhancements['title'][-1].content).title} "
            f"{cast('AbstractContentEnhancement', enhancements['abstract'][-1].content).abstract}"
        )
        if len(text) < self.settings.min_text_length:
            return None
        return text

    @classmethod
    def _enhancement_context(cls, enhancements: dict[str, list[Enhancement]]) -> dict[str, list[UUID] | UUID | None]:
        return {
            "derived_from": [
                cast("UUID", enhancements["parent-incl"][0].id),
                cast("UUID", enhancements["title"][-1].id),
                cast("UUID", enhancements["abstract"][-1].id),
            ],
            "supersedes": enhancements["self"][-1].id if enhancements.get("self") else None,
        }

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        # Set up result handler
        results = ResultHandler(
            robot_name=self.self_source,
            robot_version=self.settings.robot_version,
            enhancement_visibility=self.settings.enhancement_visibility,
            max_batch_failure_rate=self.settings.max_failure_rate_prefilter,
            n_total=len(references),
            check_unique_reference_id=True,
            logger=self.loop_logger.getChild("results"),
        )

        # Process simple cases that do not require classification
        needs_classification = self._handle_classification_free(results=results, references=references)
        # Run remaining references through classifier
        if len(needs_classification) > 0:
            await self._handle_classification(results=results, needs_classification=needs_classification)

        # Submit to repository
        enhancements = results.prepare_response()
        await self.repository.submit_enhancements(
            batch_info=batch_info,
            enhancements=enhancements,
        )

        self.loop_logger.info(f"Submitted {len(enhancements):,} enhancements.")
        return True
