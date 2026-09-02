"""Task for single loop of the enhancement runner."""

from .util import Runner


class EnhancementRunner(Runner):
    """Runner for writing abstract enhancements to repository.."""

    async def _register_listener(self) -> None:
        pass  # TODO

    async def _loop_task(self) -> None:
        """Task for single loop of the enhancement runner."""
        # Poll for approved requests for enhancements
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return

        # TODO: apply SVM
        # TODO: apply high recall prompt
        # TODO: apply balanced prompt
        # TODO: apply high precision prompt

        # Submit to repository
        await self.repository.submit_enhancements(batch_info=batch_info, cache_entries=cache_entries)

        # Remember we submitted these
        await self.store.log_submission(cache_entries=cache_entries)

        self.total_entries_processed += len(cache_entries)
        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(cache_entries):,} enhancements.",
        )


#  Enhancement(
#                 reference_id=record.destiny_id,
#                 source=f"{self.settings.repository_provenance} ({record.source or 'OTHER'})",
#                 visibility=Visibility.RESTRICTED,
#                 robot_version=self.settings.robot_version,
#                 content=AbstractContentEnhancement(
#                     abstract=record.abstract,
#                     process=AbstractProcessType.OTHER,
#                 ),
#             )
