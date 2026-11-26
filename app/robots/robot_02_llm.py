"""Pre-filter robot using cheap and simple high-recall model."""

import asyncio
from collections import OrderedDict
from typing import cast
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from litellm.exceptions import BadRequestError
from opentelemetry import trace

from app.classifiers.llm import LLMClassifier, PromptConfig, PromptError
from app.classifiers.runner import ClassificationRunner
from app.util.enhancements import ResultHandler

# Failures that will recur for a reference no matter how often we retry.
PERMANENT_ERRORS = (BadRequestError, PromptError)


class EnhancementRunner(ClassificationRunner):
    def __init__(self, name: str) -> None:
        super().__init__(name=name)

        self.prompts = OrderedDict(
            [
                (label, LLMClassifier(config=PromptConfig.from_file(config_file), scheme=self.settings.annotation_scheme_incl, label=label))
                for label, config_file in self.settings.prompt_configs.items()
            ],
        )

    @property
    def parent_source(self) -> str:
        return self.settings.robot_name_prefilter

    @property
    def self_source(self) -> str:
        return self.settings.robot_name_llm

    async def _annotate_reference(self, text: str) -> list[BooleanAnnotation]:
        """Run every prompt over one reference, stopping at the first exclusion."""
        annotations = []
        for label, prompt in self.prompts.items():
            with self.tracer.start_as_current_span("llm.prompt") as span:
                span.set_attributes({"app.llm.label": label})
                annotation = await prompt.annotate(text=text)
                span.set_attribute("app.llm.included", annotation.value)
            annotations.append(annotation)
            if not annotation.value:
                break
        return annotations

    async def _handle_classification(self, results: ResultHandler, needs_classification: list[tuple[UUID, dict[str, list[Enhancement]]]]) -> None:
        with self.tracer.start_as_current_span("llm.batch") as span:
            span.set_attribute("app.reference.count", len(needs_classification))
            texts = [cast("str", self._enhancements_to_text(enhancements)) for _reference_id, enhancements in needs_classification]

            # One coroutine per reference, each walking the whole prompt cascade, so a slow
            # prompt only delays its own reference rather than the whole batch.
            outcomes = await asyncio.gather(
                *(self._annotate_reference(reference) for reference in texts),
                return_exceptions=True,
            )

            for (reference_id, enhancements), outcome in zip(needs_classification, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    if not isinstance(outcome, PERMANENT_ERRORS):
                        # Returning nothing lets the lease lapse so the batch is redelivered
                        # This leans on the repository's lease mechanism to retry a few times
                        self.loop_logger.error(f"Abandoning batch for redelivery: {outcome!r}")
                        span.set_status(trace.StatusCode.ERROR, f"abandoned for redelivery: {outcome!r}")
                        raise RuntimeError("Encountered permanent error in LLM classifier.")

                    # Report back "normal" errors
                    results.fail(reference_id=reference_id, reason=f"{type(outcome).__name__}: {outcome}")
                    continue

                # Submit annotations
                results.enhance(
                    reference_id=reference_id,
                    content=AnnotationEnhancement(annotations=outcomes),
                    **self._enhancement_context(enhancements),
                )
