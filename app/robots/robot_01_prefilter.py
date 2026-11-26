"""Pre-filter robot using cheap and simple high-recall model."""

from itertools import batched
from typing import cast
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement

from app.classifiers.runner import ClassificationRunner
from app.util.enhancements import ResultHandler


class EnhancementRunner(ClassificationRunner):
    @property
    def parent_source(self) -> str:
        return self.settings.robot_name_query

    @property
    def self_source(self) -> str:
        return self.settings.robot_name_prefilter

    async def _handle_classification(self, results: ResultHandler, needs_classification: list[tuple[UUID, dict[str, list[Enhancement]]]]) -> None:
        for index, batch in enumerate(batched(needs_classification, self.settings.batch_size_prefilter, strict=False)):
            with self.tracer.start_as_current_span("prefilter.predict") as span:
                span.set_attribute("app.batch.index", index)
                span.set_attribute("app.reference.count", len(batch))

                try:
                    texts = [cast("str", self._enhancements_to_text(enhancements)) for _reference_id, enhancements in needs_classification]
                    y_pred = self.classifier.predict_proba(texts)
                    span.set_attribute("app.prefilter.included", int((y_pred > self.classifier.threshold_).sum()))

                    for (reference_id, enhancements), score in zip(batch, y_pred, strict=True):
                        # Submit annotations
                        results.enhance(
                            reference_id=reference_id,
                            content=AnnotationEnhancement(
                                annotations=[
                                    BooleanAnnotation(
                                        scheme=self.settings.annotation_scheme_incl,
                                        label=self.settings.annotation_label_prefilter,
                                        value=score > self.classifier.threshold_,
                                        score=score,
                                    )
                                ],
                            ),
                            **self._enhancement_context(enhancements),
                        )
                except Exception as e:  # noqa: BLE001
                    for reference_id, _enhancements in needs_classification:
                        results.fail(reference_id=reference_id, reason=f"Failed to classify: {e}")
