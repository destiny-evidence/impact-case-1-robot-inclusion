"""Task for single loop of the enhancement runner."""

import re
from enum import Enum
from typing import cast
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation
from destiny_sdk.robots import RobotAutomationIn

from app.util import Runner
from app.util.enhancements import ResultHandler, find_enhancements
from app.util.util import gather_dict


class EnhancementRunnerMode(str, Enum):  # noqa: UP042
    """Enum for types of runners."""

    match = "match"
    unmatch = "unmatch"


class EnhancementRunner(Runner):
    """
    Runner for setting the climate and health query flag (top-level community scope).

    Handles the following sub-cases:
       1) No existing annotation         and search        matches -> create inclusion annotation
       2) No existing annotation         and search does not match -> ignore (also not covered by query)
       3)    Existing annotation (True)  and search        matches -> ignore unless we want to superseed with another version
       4)    Existing annotation (False) and search        matches -> supersede with True annotation
       5)    Existing annotation (True)  and search does not match -> Supersede annotation with `TombstoneEnhancement`
       6)    Existing annotation (False) and search does not match -> Do nothing (or add `TombstoneEnhancement` if not already present)
    """

    NAME = "Climate and health (IC1) query robot"

    def __init__(self, name: str, mode: EnhancementRunnerMode) -> None:
        super().__init__(name=name)
        self.mode = mode
        with self.settings.search_query.open("r") as fp:
            self.query = fp.read()
            self.flat_query = re.sub(r"\s+", " ", self.query)

    def _automation_query(self) -> RobotAutomationIn:
        # Covers cases 1, 3, 4 (triggers on matching updated abstracts)
        match_query = {
            "bool": {
                "must": [
                    {
                        "nested": {
                            "path": "changeset.enhancements",
                            "query": {"term": {"changeset.enhancements.content.enhancement_type": "abstract"}},
                        }
                    },
                    {
                        "simple_query_string": {
                            "query": self.flat_query,
                            "fields": ["changeset.enhancements.content.text"],
                        }
                    },
                ]
            }
        }

        # Covers cases 5, 6 (triggers on any changing abstract that has our community scope label
        unmatch_query = {
            "bool": {
                "must": [
                    # Loop-breaker: only trigger when an abstract actually changed
                    {
                        "nested": {
                            "path": "changeset.enhancements",
                            "query": {"term": {"changeset.enhancements.content.enhancement_type": "abstract"}},
                        }
                    },
                    # Document currently has the annotation
                    {"term": {"enhancements.content.annotations.scheme": self.settings.annotation_scheme_query}},
                    {"term": {"enhancements.content.annotations.label": self.settings.annotation_label_query}},
                ],
                "must_not": [
                    # The updated abstract no longer matches the search query
                    {
                        "simple_query_string": {
                            "query": self.flat_query,
                            "fields": ["changeset.enhancements.content.text"],
                        }
                    },
                    # Optional Case 6 guard: exclude documents already have a TombstoneEnhancement for this rule
                    # Not including this, could be there is a superseded TombstoneEnhancement (i.e. resurrection)
                    # {"term": {"enhancements.content.enhancement_type": "EnhancementType.TOMBSTONE"}}
                ],
            }
        }

        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query=match_query if self.mode == EnhancementRunnerMode.match else unmatch_query,
        )

    async def _loop_task(self) -> bool:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        # Set up result handler
        results = ResultHandler(
            robot_name=self.NAME,
            robot_version=self.settings.robot_version,
            enhancement_visibility=self.settings.enhancement_visibility,
            max_batch_failure_rate=None,
            n_total=len(references),
            check_unique_reference_id=True,
            logger=self.loop_logger.getChild("results"),
        )

        # Prepare enhancements
        for reference in references:
            try:
                existing_enhancements = gather_dict(
                    find_enhancements(
                        reference,
                        {
                            # Find existing enhancements with query label
                            "incl": lambda e: (
                                e.source == self.NAME
                                and isinstance(e.content, AnnotationEnhancement)
                                and any(
                                    (
                                        isinstance(annotation, BooleanAnnotation)
                                        and annotation.scheme == self.settings.annotation_scheme_query
                                        and annotation.label == self.settings.annotation_label_query
                                        and annotation.value == True  # noqa: E712
                                        for annotation in e.content.annotations
                                    ),
                                )
                            ),
                            "excl": lambda e: (
                                e.source == self.NAME
                                and isinstance(e.content, AnnotationEnhancement)
                                and any(
                                    (
                                        isinstance(annotation, BooleanAnnotation)
                                        and annotation.scheme == self.settings.annotation_scheme_query
                                        and annotation.label == self.settings.annotation_label_query
                                        and annotation.value == False  # noqa: E712
                                        for annotation in e.content.annotations
                                    ),
                                )
                            ),
                        },
                        check_reference_id=True,
                        skip_superseded=True,
                    ),
                )

                enhancement = AnnotationEnhancement(
                    annotations=[
                        BooleanAnnotation(
                            scheme=self.settings.annotation_scheme_query,
                            label=self.settings.annotation_label_query,
                            value=True,
                        )
                    ],
                )

                # 1) No existing annotation and search matches -> create inclusion annotation
                if self.mode == EnhancementRunnerMode.match and not existing_enhancements.get("incl") and not existing_enhancements.get("excl"):
                    results.enhance(reference_id=reference.id, content=enhancement)

                # 2) No existing annotation and search does not match
                #   -> ignore: not covered by either query

                # 3) Existing annotation (True)  and search matches -> ignore unless we want to superseed with another version
                elif self.mode == EnhancementRunnerMode.match and len(existing_enhancements.get("incl", [])) == 1 and not existing_enhancements.get("excl"):
                    results.enhance(reference_id=reference.id, content=enhancement, supersedes=existing_enhancements["incl"][0].id)

                # 4) Existing annotation (False) and search matches -> supersede with True annotation
                elif self.mode == EnhancementRunnerMode.match and not existing_enhancements.get("incl") and len(existing_enhancements.get("excl", [])) == 1:
                    results.enhance(reference_id=reference.id, content=enhancement, supersedes=existing_enhancements["excl"][0].id)

                # 5) Existing annotation (True) and search does not match -> Supersede annotation with `TombstoneEnhancement`
                elif self.mode == EnhancementRunnerMode.unmatch and len(existing_enhancements.get("incl", [])) == 1 and not existing_enhancements.get("excl"):
                    results.retract(reference_id=reference.id, supersedes=cast("UUID", existing_enhancements["incl"][0].id))

                # 6) Existing annotation (False) and search does not match -> Supersede annotation with `TombstoneEnhancement`
                #    Note: we skipped already retired enhancement chains before, so this closes a query-include chain that remains open.
                elif self.mode == EnhancementRunnerMode.unmatch and not existing_enhancements.get("incl") and len(existing_enhancements.get("excl", [])) == 1:
                    results.retract(reference_id=reference.id, supersedes=cast("UUID", existing_enhancements["excl"][0].id))

                # Inconsistent data: We have multiple enhancement chains -> Error!
                else:
                    results.fail(reference_id=reference.id, reason="Found more than one existing enhancement chains!")

            except Exception as e:  # noqa: BLE001
                results.fail(reference_id=reference.id, reason=f"Failed to process reference: {e}")

        # Submit to repository
        enhancements = results.prepare_response()
        await self.repository.submit_enhancements(
            batch_info=batch_info,
            enhancements=enhancements,
        )

        self.loop_logger.info(f"Submitted {len(enhancements):,} enhancements.")
        return True
