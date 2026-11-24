"""Task for single loop of the enhancement runner."""

from app.util.util import gather_dict
import re

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation
from destiny_sdk.robots import RobotAutomationIn

from app.util.enhancements import ResultHandler, find_enhancements
from app.util import Runner


class EnhancementRunner(Runner):
    """Runner for setting the climate and health query flag (top-level community scope).

    Handles the following cases:
        1) No existing annotation         and search        matches -> create inclusion annotation
        2) No existing annotation         and search does not match -> ignore (also not covered by query)
        3)    Existing annotation (True)  and search        matches -> ignore unless we want to superseed with another version
        4)    Existing annotation (False) and search        matches -> supersede with True annotation
        5)    Existing annotation (True)  and search does not match -> Supersede annotation with `TombstoneEnhancement`
        6)    Existing annotation (False) and search does not match -> Do nothing (or add `TombstoneEnhancement` if not already present)
    """

    NAME = "Climate and health (IC1) query robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        with self.settings.search_query.open("r") as fp:
            self.query = fp.read()
            self.flat_query = re.sub(r"\s+", " ", self.query)

    def _automation_query(self) -> RobotAutomationIn:
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "bool": {
                    "should": [
                        # Branch 1: Incoming changeset matches the search query (Cases 1, 3, 4)
                        {
                            "bool": {
                                "must": [
                                    {
                                        "nested": {
                                            "path": "changeset.enhancements",
                                            "query": {
                                                "term": {
                                                    "changeset.enhancements.content.enhancement_type": "abstract"
                                                }
                                            },
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
                        },
                        # Branch 2: Document already has the annotation (Cases 3, 4, 5, 6)
                        {
                            "bool": {
                                "must": [
                                    {
                                        "term": {
                                            "enhancements.content.annotations.scheme": self.settings.annotation_scheme_query
                                        }
                                    },
                                    {
                                        "term": {
                                            "enhancements.content.annotations.label": self.settings.annotation_label_query
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
            existing_enhancements = gather_dict(
                find_enhancements(
                    reference,
                    {
                        # Find previous enhancements by this robot (ignoring version)
                        "self": lambda e: e.source == self.NAME,
                    },
                    check_reference_id=True,
                    skip_superseded=True,
                ),
            )

            # TODO: handle all the cases based on `existing_enhancements`

            results.enhance(
                reference_id=reference.id,
                content=AnnotationEnhancement(
                    annotations=[
                        BooleanAnnotation(
                            scheme=self.settings.annotation_scheme_query,
                            label=self.settings.annotation_label_query,
                            value=True,
                        )
                    ],
                ),
            )

        enhancements = results.prepare_response()
        # Submit to repository
        await self.repository.submit_enhancements(
            batch_info=batch_info,
            enhancements=enhancements,
        )

        self.loop_logger.info(f"Submitted {len(enhancements):,} enhancements.")
        return True
