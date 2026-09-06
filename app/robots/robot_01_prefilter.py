"""Pre-filter robot using cheap and simple high-recall model."""

from itertools import batched

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import RobotAutomationIn

from app.classifiers.sklearn import SklearnClassifier
from app.util import Runner, get_title_abstract_from_reference


class EnhancementRunner(Runner):
    """Runner for pre-filtering with a cheap and simple high-recall model."""

    NAME = "Climate and health (IC1) pre-filter robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.classifier = SklearnClassifier(path=self.settings.model_prefilter)

    def _automation_query(self) -> RobotAutomationIn:
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "bool": {
                    "must": [
                        {
                            "nested": {
                                "path": "changeset.enhancements.content.annotations",
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": {"changeset.enhancements.content.annotations.scheme": self.settings.annotation_scheme_query}},
                                            {"term": {"changeset.enhancements.content.annotations.label": self.settings.annotation_label_query}},
                                            {"term": {"changeset.enhancements.content.annotations.value": True}},
                                        ]
                                    }
                                },
                            }
                        }
                    ]
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

        enhancements = []
        for batch in batched(references, self.settings.batch_size_prefilter, strict=False):
            documents = [get_title_abstract_from_reference(reference) for reference in batch]
            texts = [f"{title or ''}. {abstract or ''}" for title, abstract in documents]
            mask = [len(text) > 0 and title is not None and abstract is not None for text, (title, abstract) in zip(texts, documents, strict=False)]

            # Write implicit exclude enhancements
            self.loop_logger.debug("Caching batch exclusion results.")
            enhancements += [self._assemble_enhancement(reference, value=False, score=0.0) for reference, mask_ in zip(batch, mask, strict=False) if not mask_]

            filtered_references = [reference for reference, mask_ in zip(batch, mask, strict=False) if mask_]
            filtered_texts = [text for text, mask_ in zip(texts, mask, strict=False) if mask_]
            y_pred = self.classifier.predict_proba(filtered_texts)

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
