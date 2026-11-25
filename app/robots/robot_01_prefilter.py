"""Pre-filter robot using cheap and simple high-recall model."""

from collections.abc import Callable
from functools import cached_property
from itertools import batched
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
from app.util import Runner
from app.util.enhancements import ResultHandler, find_enhancements
from app.util.util import gather_dict


class EnhancementRunner(Runner):
    """
    Runner for pre-filtering with a relatively simple high-recall model.

    This runner trusts that the query runner upstream *always* writes an updated or new annotation
    when this pre-filter runner should (re-)classify a reference.
    This runner therefore explicitly does *not* check for changing abstracts directly.
    This runner will only accept exactly one query annotation chain (incl Tombstone) and otherwise respond with an error.

    Covered cases:
         Case: has title and abstract
      1.1) Latest query annotation is True and prior pre-filter is True      -> Apply classifier -> true: supersede with label, false: supersede with Tombstone
      2.1) Latest query annotation is True and prior pre-filter is False     -> Apply classifier -> true: supersede with label, false: supersede with Tombstone
      3.1) Latest query annotation is True and prior pre-filter is Tombstone -> Apply classifier -> true: supersede with label, false: do nothing
      4.1) Latest query annotation is True and no prior pre-filter exists    -> Apply classifier -> if true: create enhancement, if false: do nothing

          Case: missing title or abstract
      1.2) Latest query annotation is True and prior pre-filter is True      -> Supersede with Tombstone
      2.2) Latest query annotation is True and prior pre-filter is False     -> Supersede with Tombstone
      3.2) Latest query annotation is True and prior pre-filter is Tombstone -> Do nothing
      4.2) Latest query annotation is True and no prior pre-filter exists    -> Do nothing

      5) Latest query annotation is False and prior pre-filter is True      -> Supersede with Tombstone
      6) Latest query annotation is False and prior pre-filter is False     -> Supersede with Tombstone
      7) Latest query annotation is False and prior pre-filter is Tombstone -> Do nothing
      8) Latest query annotation is False and no prior pre-filter exists    -> Do nothing

      9) Latest query annotation is Tombstone and prior pre-filter is True      -> Supersede with Tombstone
     10) Latest query annotation is Tombstone and prior pre-filter is False     -> Supersede with Tombstone
     11) Latest query annotation is Tombstone and prior pre-filter is Tombstone -> Do nothing
     12) Latest query annotation is Tombstone and no prior pre-filter exists    -> Do nothing

    Cases 5–8 should have been a Tombstone upstream, so treating 9–12 the same way.

    Produces per-reference error on multiple query annotation chains with this scheme/label combination.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.classifier = load_classifier(path=self.settings.model_prefilter)

    def _automation_query(self) -> RobotAutomationIn:
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "bool": {
                    "must": [
                        {
                            "nested": {
                                "path": "changeset.enhancements",
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": {"changeset.enhancements.source": self.settings.robot_name_query}},
                                        ]
                                    }
                                },
                            }
                        }
                    ]
                },
            },
        )

    @cached_property
    def enhancement_filter(self) -> dict[str, Callable[[Enhancement], bool]]:
        return {
            # Include annotations by query runner
            "query-incl": lambda e: (
                e.source == self.settings.robot_name_query
                and isinstance(e.content, AnnotationEnhancement)
                and any(
                    (
                        isinstance(annotation, BooleanAnnotation) and annotation.value == True  # noqa: E712
                        for annotation in e.content.annotations
                    ),
                )
            ),
            # Excluded or "tombstoned" annotations by query runner
            "query-excl": lambda e: (
                e.source == self.settings.robot_name_query
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
            "self-anno": lambda e: isinstance(e.content, AnnotationEnhancement) and e.source == self.settings.robot_name_prefilter,
            "self-tomb": lambda e: isinstance(e.content, TombstoneEnhancement) and e.source == self.settings.robot_name_prefilter,
            "self": lambda e: e.source == self.settings.robot_name_prefilter,
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

                # 5) Latest query annotation is False and prior pre-filter is True       -> Supersede with Tombstone
                # 9) Latest query annotation is Tombstone and prior pre-filter is True   -> Supersede with Tombstone
                # 6) Latest query annotation is False and prior pre-filter is False      -> Supersede with Tombstone
                # 10) Latest query annotation is Tombstone and prior pre-filter is False -> Supersede with Tombstone
                if (
                    not existing_enhancements.get("query-incl")
                    and len(existing_enhancements.get("query-excl", [])) == 1
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    # existing_enhancements.get("abstract") not relevant
                ):
                    results.retract(
                        reference_id=reference.id,
                        supersedes=cast("UUID", existing_enhancements["self-anno"][0].id),
                        derived_from=[query_enhancement.id for query_enhancement in existing_enhancements["query-excl"] if query_enhancement.id is not None],
                        reason="Continued upstream exclusion",
                    )

                # 7) Latest query annotation is False and prior pre-filter is Tombstone -> Do nothing
                # 8) Latest query annotation is False and no prior pre-filter exists    -> Do nothing
                # 11) Latest query annotation is Tombstone and prior pre-filter is Tombstone -> Do nothing
                # 12) Latest query annotation is Tombstone and no prior pre-filter exists    -> Do nothing
                elif (
                    not existing_enhancements.get("query-incl")
                    and len(existing_enhancements.get("query-excl", [])) == 1
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    # existing_enhancements.get("abstract") not relevant
                ):
                    results.skip(reference_id=reference.id, reason=None)

                #  Case: has title AND abstract
                # 1.1) Latest query annotation is True and prior pre-filter is True      -> Apply classifier -> supersede with (true: label, false: Tombstone)
                # 2.1) Latest query annotation is True and prior pre-filter is False     -> Apply classifier -> supersede with (true: label, false: Tombstone)
                # 3.1) Latest query annotation is True and prior pre-filter is Tombstone -> Apply classifier -> true: supersede with label, false: do nothing
                # 4.1) Latest query annotation is True and no prior pre-filter exists    -> Apply classifier -> true: create enhancement, false: do nothing
                elif (
                    len(existing_enhancements.get("query-incl", [])) == 1
                    and not existing_enhancements.get("query-excl")
                    and len(existing_enhancements.get("self", [])) < 2  # has one or none self annotation (tombstone or annotation)
                    and len(existing_enhancements.get("title", [])) > 0  # has at least one title
                    and len(existing_enhancements.get("abstract", [])) > 0  # has at least one abstract
                ):
                    needs_classification.append((reference.id, existing_enhancements))

                #   Case: missing title or abstract
                # 1.2) Latest query annotation is True and prior pre-filter is True      -> Supersede with Tombstone
                # 2.2) Latest query annotation is True and prior pre-filter is False     -> Supersede with Tombstone
                elif (
                    len(existing_enhancements.get("query-incl", [])) == 1
                    and not existing_enhancements.get("query-excl")
                    and len(existing_enhancements.get("self-anno", [])) == 1
                    and not existing_enhancements.get("self-tomb")
                    and (not existing_enhancements.get("title") or not existing_enhancements.get("abstract"))
                ):
                    results.retract(
                        reference_id=reference.id,
                        supersedes=cast("UUID", existing_enhancements["self-anno"][0].id),
                        derived_from=[query_enhancement.id for query_enhancement in existing_enhancements["query-excl"] if query_enhancement.id is not None],
                        reason="Missing title or abstract",
                    )

                #   Case: missing title or abstract
                # 3.2) Latest query annotation is True and prior pre-filter is Tombstone -> do nothing
                # 4.2) Latest query annotation is True and no prior pre-filter exists    -> do nothing
                elif (
                    len(existing_enhancements.get("query-incl", [])) == 1
                    and not existing_enhancements.get("query-excl")
                    and not existing_enhancements.get("self-anno")
                    and len(existing_enhancements.get("self-tomb", [])) == 1
                    and (not existing_enhancements.get("title") or not existing_enhancements.get("abstract"))
                ):
                    results.skip(reference_id=reference.id, reason=None)

                # Inconsistent data -> Error!
                else:
                    results.fail(reference_id=reference.id, reason="Undefined constellation of prior enhancements!")

            except Exception as e:  # noqa: BLE001
                results.fail(reference_id=reference.id, reason=f"Failed to process reference: {e}")
        return needs_classification

    def _handle_classification(self, results: ResultHandler, needs_classification: list[tuple[UUID, dict[str, list[Enhancement]]]]) -> None:
        for index, batch in enumerate(batched(needs_classification, self.settings.batch_size_prefilter, strict=False)):
            with self.tracer.start_as_current_span("prefilter.predict") as span:
                span.set_attribute("app.batch.index", index)
                span.set_attribute("app.reference.count", len(batch))

                texts = [
                    (
                        f"{cast('BibliographicMetadataEnhancement', enhancements['title'][-1].content).title} "
                        f"{cast('AbstractContentEnhancement', enhancements['abstract'][-1].content).abstract}"
                    )
                    for _reference_id, enhancements in batch
                ]
                y_pred = self.classifier.predict_proba(texts)
                span.set_attribute("app.prefilter.included", int((y_pred > self.classifier.threshold_).sum()))

                for (reference_id, enhancements), score in zip(batch, y_pred, strict=True):
                    derived_by = [
                        cast("UUID", enhancements["query-incl"][0].id),
                        cast("UUID", enhancements["title"][-1].id),
                        cast("UUID", enhancements["abstract"][-1].id),
                    ]

                    # 1.1.1) Latest query annotation is True and prior pre-filter is True      -> Apply classifier -> true: supersede with label
                    # 2.1.1) Latest query annotation is True and prior pre-filter is False     -> Apply classifier -> true: supersede with label
                    # 3.1.1) Latest query annotation is True and prior pre-filter is Tombstone -> Apply classifier -> true: supersede with label
                    # 4.1.1) Latest query annotation is True and no prior pre-filter exists    -> Apply classifier -> true: create enhancement
                    if score > self.classifier.threshold_:
                        annotation = AnnotationEnhancement(
                            annotations=[
                                BooleanAnnotation(
                                    scheme=self.settings.annotation_scheme_incl,
                                    label=self.settings.annotation_label_prefilter,
                                    value=True,
                                    score=score,
                                )
                            ],
                        )
                        results.enhance(
                            reference_id=reference_id,
                            content=annotation,
                            derived_from=derived_by,
                            supersedes=enhancements["self"][-1].id if enhancements.get("self") else None,
                        )

                    # 1.1.2) Latest query annotation is True and prior pre-filter is True      -> Apply classifier -> false: supersede with Tombstone
                    # 2.1.2) Latest query annotation is True and prior pre-filter is False     -> Apply classifier -> false: supersede with Tombstone
                    elif enhancements.get("self-anno"):
                        results.retract(reference_id=reference_id, derived_from=derived_by, supersedes=cast("UUID", enhancements["self-anno"][0].id))

                    # 3.1.2) Latest query annotation is True and prior pre-filter is Tombstone -> Apply classifier -> false: do nothing
                    # 4.1.2) Latest query annotation is True and no prior pre-filter exists    -> Apply classifier -> false: do nothing
                    else:
                        results.skip(reference_id=reference_id, reason=None)

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        # Set up result handler
        results = ResultHandler(
            robot_name=self.settings.robot_name_prefilter,
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
            self._handle_classification(results=results, needs_classification=needs_classification)

        # Submit to repository
        enhancements = results.prepare_response()
        await self.repository.submit_enhancements(
            batch_info=batch_info,
            enhancements=enhancements,
        )

        self.loop_logger.info(f"Submitted {len(enhancements):,} enhancements.")
        return True
