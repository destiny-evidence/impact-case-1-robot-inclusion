"""Pre-filter robot using cheap and simple high-recall model."""

import asyncio
from collections import OrderedDict, defaultdict
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, LinkedRobotError, RobotAutomationIn
from litellm.exceptions import BadRequestError

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

    async def _annotate_reference(
        self,
        reference: Reference,
        label: str,
        prompt: LLMClassifier,
    ) -> tuple[BooleanAnnotation, bool]:
        """Annotate a single reference with one prompt."""
        title, abstract = get_title_abstract_from_reference(reference)
        text = f"{title or ''}. {abstract or ''}"

        # Ensure that title and abstract are set, and they are above a minimum length
        if title is None or abstract is None or len(text) < self.settings.min_text_length:
            annotation = BooleanAnnotation(scheme=self.settings.annotation_scheme_incl, label=label, value=False, score=None)
            return annotation, False

        annotation = await prompt.annotate(text=text)
        return annotation, annotation.value

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        results: dict[UUID, list[BooleanAnnotation]] = defaultdict(list)
        failures: dict[UUID, str] = {}

        filtered_references = references
        for label, prompt in self.prompts.items():
            # Merge parallel prompts before proceeding with remaining included references to the next prompt
            annotation_results = await asyncio.gather(
                *(self._annotate_reference(reference, label, prompt) for reference in filtered_references),
                return_exceptions=True,
            )

            included = []
            for reference, outcome in zip(filtered_references, annotation_results, strict=True):
                if isinstance(outcome, BaseException):
                    if not isinstance(outcome, PERMANENT_ERRORS):
                        # Returning nothing lets the lease lapse so the batch is redelivered
                        # This leans on the repository's lease mechanism to retry a few times
                        self.loop_logger.error(f"Abandoning batch {batch_info.id} for redelivery: {outcome!r}")
                        return False
                    failures[reference.id] = f"{type(outcome).__name__} on {label}: {outcome}"
                    continue

                annotation, decision = outcome
                results[reference.id].append(annotation)
                if decision:
                    included.append(reference)

            # In the next round, we only continue with included records
            filtered_references = included

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
