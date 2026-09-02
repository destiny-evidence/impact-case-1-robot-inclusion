"""Task for single loop of the enhancement runner."""

import re

from destiny_sdk.enhancements import Enhancement, AnnotationEnhancement, BooleanAnnotation
from destiny_sdk.robots import RobotAutomationIn

from app.util import Runner


class EnhancementRunner(Runner):
    """Runner for setting the climate and health query flag."""

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
                    "must": [
                        {
                            "nested": {
                                "path": "changeset.enhancements",
                                "query": {"term": {"changeset.enhancements.content.enhancement_type": "abstract"}},
                            }
                        },
                        {"simple_query_string": {"query": self.flat_query, "fields": ["changeset.enhancements.content.text"]}},
                    ],
                }
            },
        )

    async def _loop_task(self) -> None:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return

        # Prepare enhancements
        enhancements = [
            Enhancement(
                reference_id=reference.id,
                source=self.NAME,
                visibility=self.settings.enhancement_visibility,
                robot_version=self.settings.robot_version,
                content=AnnotationEnhancement(
                    annotations=[
                        BooleanAnnotation(
                            scheme=self.settings.annotation_scheme_query,
                            label=self.settings.annotation_label_query,
                            value=True,
                            score=1.0,
                        )
                    ],
                ),
            )
            for reference in references
        ]

        # Submit to repository
        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=enhancements)

        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(enhancements):,} enhancements.",
        )
