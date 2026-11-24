"""Pre-filter robot using cheap and simple high-recall model."""

from itertools import batched

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import RobotAutomationIn

from app.util import Runner, get_title_abstract_from_reference
from app.classifiers import load_classifier


class EnhancementRunner(Runner):
    """
    Runner for pre-filtering with a relatively simple high-recall model.

    There are 2x4 main cases of abstract and query-annotation changing.
    Each has three sub-cases based this robot's prior enhancements.

        1.     Abstract changed -> existing query-annotation changed
        1.1)     True -> False
        1.1.1)     prefilter annotation exists (True)
        1.1.2)     prefilter annotation exists (False)
        1.1.3)     no prefilter annotation
        1.2)     False -> True
        1.2.1)     prefilter annotation exists (True)
        1.2.2)     prefilter annotation exists (False)
        1.2.3)     no prefilter annotation

        2.     Abstract changed -> existing query-annotation unchanged
        2.1)     True -> True
        2.1.1)     prefilter annotation exists (True)
        2.1.2)     prefilter annotation exists (False)
        2.1.3)     no prefilter annotation
        2.2)     False -> False
        2.2.1)     prefilter annotation exists (True)
        2.2.2)     prefilter annotation exists (False)
        2.2.3)     no prefilter annotation

        3.     Abstract unchanged -> query annotation changed
        3.1)     True -> True                             (Note: if query annotation changed, this would logically be True -> False, matching 1.1)
        3.1.1)     prefilter annotation exists (True)
        3.1.2)     prefilter annotation exists (False)
        3.1.3)     no prefilter annotation
        3.2)     False -> False                           (Note: if query annotation changed, this would logically be False -> True, matching 1.2)
        3.2.1)     prefilter annotation exists (True)
        3.2.2)     prefilter annotation exists (False)
        3.2.3)     no prefilter annotation

        4.     Abstract unchanged -> query annotation unchanged
        4.1)     True -> True                            (Note: if query annotation unchanged, this would logically be True -> True, matching 2.1)
        4.1.1)     prefilter annotation exists (True)
        4.1.2)     prefilter annotation exists (False)
        4.1.3)     no prefilter annotation
        4.2)     False -> False                          (Note: if query annotation unchanged, this would logically be False -> False, matching 2.2)
        4.2.1)     prefilter annotation exists (True)
        4.2.2)     prefilter annotation exists (False)
        4.2.3)     no prefilter annotation

    Actions
        I) Retire existing pre-filter enhancement
           1.1.*, 3.1.*
           supersede with TombstoneEnhancement

       II) Re-classify
           1.2.*, 2.1.1, 2.1.2, 2.2.1, 2.2.2, 3.2.*
           Old class  -> new class -> action
           True       ->  True     -> do nothing
           True       ->  False    -> supersede with TombstoneEnhancement
           False      ->  True     -> supersede with BooleanAnnotation
           False      ->  False    -> supersede with TombstoneEnhancement (if it's not already one)

       IV) No-Op (Ignore / Do Nothing)
           2.1.3, 2.2.3, 4.1.*, 4.2.*
    """

    NAME = "Climate and health (IC1) pre-filter robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.classifier = load_classifier(path=self.settings.model_prefilter)

    def _automation_query(self) -> RobotAutomationIn:
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "bool": {
                    "should": [
                        # Route 1) Query annotation was added or updated
                        {
                            "nested": {
                                "path": "changeset.enhancements.content.annotations",
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": {"changeset.enhancements.content.annotations.scheme": self.settings.annotation_scheme_query}},
                                            {"term": {"changeset.enhancements.content.annotations.label": self.settings.annotation_label_query}},
                                        ]
                                    }
                                },
                            }
                        },
                        # Route 2) Abstract changed and we already have a pre-filter label
                        {
                            "bool": {
                                "must": [
                                    {
                                        "exists": {
                                            "field": "changeset.enhancements.content.text"
                                        }
                                    },
                                    {
                                        "nested": {
                                            "path": "enhancements.content.annotations",
                                            "query": {
                                                "bool": {
                                                    "must": [
                                                        {"term": {"enhancements.content.annotations.scheme": self.settings.annotation_scheme_incl}},
                                                        {"term": {"enhancements.content.annotations.label": self.settings.annotation_label_prefilter}},
                                                        # {"term": {"enhancements.content.annotations.value": True}},
                                                    ]
                                                }
                                            },
                                        }
                                    },
                                ]
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        )

    def _assemble_enhancement(self, reference: Reference, value: bool, score: float) -> Enhancement:
        return Enhancement(
            reference_id=reference.id,
            source=self.NAME,
            visibility=self.settings.enhancement_visibility,
            robot_version=self.settings.robot_version,
            content=AnnotationEnhancement(
                annotations=[
                    BooleanAnnotation(
                        scheme=self.settings.annotation_scheme_incl,
                        label=self.settings.annotation_label_prefilter,
                        value=value,
                        score=score,
                    )
                ],
            ),
        )

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        enhancements = [] # TODO: switch to using `ResultHandler`
        for index, batch in enumerate(batched(references, self.settings.batch_size_prefilter, strict=False)):
            with self.tracer.start_as_current_span("prefilter.predict") as span:
                span.set_attribute("app.batch.index", index)

                # TODO: handle actions (I) and (III)
                # TODO: handle action (II)

                documents = [get_title_abstract_from_reference(reference) for reference in batch]
                texts = [f"{title or ''}. {abstract or ''}" for title, abstract in documents]
                mask = [len(text) > 0 and title is not None and abstract is not None for text, (title, abstract) in zip(texts, documents, strict=False)]

                # Write implicit exclude enhancements
                self.loop_logger.debug("Caching batch exclusion results.")
                enhancements += [
                    self._assemble_enhancement(reference, value=False, score=0.0) for reference, mask_ in zip(batch, mask, strict=False) if not mask_
                ]

                filtered_references = [reference for reference, mask_ in zip(batch, mask, strict=False) if mask_]
                filtered_texts = [text for text, mask_ in zip(texts, mask, strict=False) if mask_]
                span.set_attribute("app.reference.count", len(filtered_references))
                if not filtered_texts:
                    continue

                y_pred = self.classifier.predict_proba(filtered_texts)

                span.set_attribute("app.prefilter.included", int((y_pred >= self.classifier.threshold_).sum()))

                # Write SVM predictions exclude enhancements
                self.loop_logger.debug(f"Caching {len(filtered_references):,} batch prediction results.")
                enhancements += [
                    self._assemble_enhancement(reference, value=score >= self.classifier.threshold_, score=score)
                    for reference, score in zip(filtered_references, y_pred, strict=False)
                ]

        self.loop_logger.debug("Submitting enhancements to repository.")
        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=enhancements)

        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(enhancements):,} enhancements.",
        )

        return True
