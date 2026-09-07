"""Pre-filter robot using cheap and simple high-recall model."""

import asyncio
from collections import OrderedDict
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, LinkedRobotError, RobotAutomationIn
from litellm.exceptions import BadRequestError
from opentelemetry import trace

from app.classifiers.llm import LLMClassifier, PromptConfig, PromptError
from app.util import Runner, get_title_abstract_from_reference

# Failures that will recur for a reference no matter how often we retry.
PERMANENT_ERRORS = (BadRequestError, PromptError)


class EnhancementRunner(Runner):
    """Runner for pre-filtering with a cheap and simple high-recall model."""

    NAME = "Climate and health (IC1) LLM 3-level inclusion robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)

        self.prompts = OrderedDict(
            [
                (label, LLMClassifier(config=PromptConfig.from_file(config_file), scheme=self.settings.annotation_scheme_incl, label=label))
                for label, config_file in self.settings.prompt_configs.items()
            ],
        )

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
                                            {"term": {"changeset.enhancements.content.annotations.scheme": self.settings.annotation_scheme_incl}},
                                            {"term": {"changeset.enhancements.content.annotations.label": self.settings.annotation_label_prefilter}},
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

    async def _annotate_reference(self, reference: Reference) -> list[BooleanAnnotation]:
        """Run every prompt over one reference, stopping at the first exclusion."""
        title, abstract = get_title_abstract_from_reference(reference)
        text = f"{title or ''}. {abstract or ''}"
        usable = title is not None and abstract is not None and len(text) >= self.settings.min_text_length

        annotations = []
        for label, prompt in self.prompts.items():
            with self.tracer.start_as_current_span("llm.prompt") as span:
                span.set_attribute("app.llm.label", label)
                if usable:
                    annotation = await prompt.annotate(text=text)
                else:
                    annotation = BooleanAnnotation(scheme=self.settings.annotation_scheme_incl, label=label, value=False, score=None)
                span.set_attribute("app.llm.included", annotation.value)
            annotations.append(annotation)
            if not annotation.value:
                break
        return annotations

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        results: dict[UUID, list[BooleanAnnotation]] = {}
        failures: dict[UUID, str] = {}

        with self.tracer.start_as_current_span("llm.batch") as span:
            span.set_attribute("app.reference.count", len(references))

            # One coroutine per reference, each walking the whole prompt cascade, so a slow
            # prompt only delays its own reference rather than the whole batch.
            outcomes = await asyncio.gather(
                *(self._annotate_reference(reference) for reference in references),
                return_exceptions=True,
            )

            for reference, outcome in zip(references, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    if not isinstance(outcome, PERMANENT_ERRORS):
                        # Returning nothing lets the lease lapse so the batch is redelivered
                        # This leans on the repository's lease mechanism to retry a few times
                        self.loop_logger.error(f"Abandoning batch {batch_info.id} for redelivery: {outcome!r}")
                        span.set_status(trace.StatusCode.ERROR, f"abandoned for redelivery: {outcome!r}")
                        return False
                    failures[reference.id] = f"{type(outcome).__name__}: {outcome}"
                    continue
                results[reference.id] = outcome

            included = sum(1 for annotations in results.values() if annotations and annotations[-1].value)
            span.set_attributes({"app.llm.included": included, "app.llm.failures": len(failures)})

        entries: list[EnhancementResultEntry] = [
            Enhancement(
                reference_id=reference_id,
                source=self.NAME,
                visibility=self.settings.enhancement_visibility,
                robot_version=self.settings.robot_version,
                content=AnnotationEnhancement(annotations=annotations),
            )
            for reference_id, annotations in results.items()
            if reference_id not in failures
        ]
        entries += [LinkedRobotError(reference_id=reference_id, message=message) for reference_id, message in failures.items()]

        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=entries)

        num_annotations = sum(len(annotations) for annotations in results.values())
        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(entries):,} results "
            f"with {num_annotations:,} annotations and {len(failures):,} failed references.",
        )

        return True
