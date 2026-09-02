"""Utility class for interacting with the repository."""

import base64
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from destiny_sdk.client import KeycloakOAuthMiddleware, OAuthClient, RobotClient
from destiny_sdk.enhancements import (
    AbstractContentEnhancement,
    BibliographicMetadataEnhancement,
    Enhancement,
)
from destiny_sdk.references import Reference
from destiny_sdk.robots import (
    EnhancementRequestIn,
    RobotEnhancementBatch,
    RobotEnhancementBatchResult,
)

if TYPE_CHECKING:
    from logging import Logger

    from .config import Settings

HTTP_TIMEOUT_SECONDS = 600


class BatchedResultWriter:
    def __init__(self, batch_info: RobotEnhancementBatch, client: httpx.AsyncClient, finalise_callback: Callable[[UUID], None]) -> None:
        self.batch_info = batch_info
        self.target_url = str(batch_info.result_storage_url)
        self.block_ids: list[str] = []
        self.client = client
        self._finalize_callback = finalise_callback
        self.num_enhancements = 0

    @property
    def num_batches(self) -> int:
        return len(self.block_ids)

    async def submit_batch(self, enhancements: list[Enhancement]) -> int:
        index = self.num_batches
        block_id = base64.b64encode(f"{index:08d}".encode("ascii")).decode("ascii")
        self.block_ids.append(block_id)
        num_enhancements = 0
        file_content = b""
        for enhancement in enhancements:
            file_content += (enhancement.to_jsonl() + "\n").encode("utf-8")
            num_enhancements += 1

        url = httpx.URL(self.target_url).copy_merge_params({"comp": "block", "blockid": block_id})
        response = await self.client.put(url, content=file_content, headers={"Content-Length": str(len(file_content))})
        response.raise_for_status()
        self.num_enhancements += num_enhancements
        return num_enhancements

    async def finalise(self) -> None:
        """Commit the staged blocks, in order, as the blob contents."""
        latest = "".join(f"<Latest>{block_id}</Latest>" for block_id in self.block_ids)
        body = ('<?xml version="1.0" encoding="utf-8"?>' f"<BlockList>{latest}</BlockList>").encode()

        url = httpx.URL(self.target_url).copy_merge_params({"comp": "blocklist"})
        response = await self.client.put(
            url,
            content=body,
            headers={
                "Content-Type": "application/xml",
                "x-ms-blob-content-type": "application/jsonl",
                "Content-Length": str(len(body)),
            },
        )
        response.raise_for_status()

        self._finalize_callback(self.batch_info.id)


class Repository:
    """Utility class for interacting with the repository."""

    def __init__(self, settings: "Settings", logger: "Logger") -> None:
        """Initialise the repository utils."""
        if settings.robot_secret is None or settings.keycloak_id is None or settings.keycloak_secret is None:
            raise ValueError

        self.logger = logger
        self.settings = settings

        self.robot_client = RobotClient(
            settings.base_url,
            settings.robot_secret.get_secret_value(),
            settings.robot_id,
        )
        self.robot_client.session.timeout = httpx.Timeout(HTTP_TIMEOUT_SECONDS)

        self.blob_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)

        self.repo_client = OAuthClient(
            settings.base_url,
            KeycloakOAuthMiddleware(
                settings.keycloak_url,
                settings.keycloak_realm,
                settings.keycloak_id,
                settings.keycloak_secret,
            ),
            timeout=HTTP_TIMEOUT_SECONDS,
        )

    def request_to_enhance(self, destiny_ids: list[UUID]) -> None:
        """Ask repository if we can provide enhancements for these IDs."""
        response = self.repo_client.get_client().post(
            "/enhancement-requests/",
            json=EnhancementRequestIn(
                robot_id=self.settings.robot_id,
                reference_ids=destiny_ids,
                # source=
            ).model_dump(mode="json"),
        )
        response.raise_for_status()

    async def get_next_batch(
        self,
        batch_size: int | None = None,
    ) -> tuple[RobotEnhancementBatch | None, list[Reference] | None]:
        """Ask repository which references it wants enhancements for."""
        batch_info = self.robot_client.poll_robot_enhancement_batch(
            robot_id=self.settings.robot_id,
            limit=self.settings.batch_size if batch_size is None else batch_size,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if batch_info is None:
            return None, None

        response = await self.blob_client.get(str(batch_info.reference_storage_url))
        response.raise_for_status()
        references = [Reference.from_jsonl(line) for line in response.text.splitlines() if line.strip()]

        if len(references) == 0:
            return None, None

        return batch_info, references

    def get_batched_enhancement_writer(self, batch_info: RobotEnhancementBatch) -> BatchedResultWriter:
        return BatchedResultWriter(
            batch_info=batch_info,
            client=self.blob_client,
            finalise_callback=self._finalise_enhancement_batch,
        )

    async def submit_enhancements(self, batch_info: RobotEnhancementBatch, enhancements: list[Enhancement]) -> None:
        """Submit enhancements to repository."""
        file_content = b""
        for enhancement in enhancements:
            file_content += (enhancement.to_jsonl() + "\n").encode("utf-8")

        await self._upload_enhancements(
            target_url=str(batch_info.result_storage_url),
            jsonl_enhancements=file_content,
        )
        self._finalise_enhancement_batch(batch_info.id)

    async def _upload_enhancements(self, target_url: str, jsonl_enhancements: bytes) -> None:
        response = await self.blob_client.put(
            target_url,
            content=jsonl_enhancements,
            headers={
                "Content-Type": "application/jsonl",
                "x-ms-blob-type": "BlockBlob",
                "Content-Length": str(len(jsonl_enhancements)),
            },
        )
        response.raise_for_status()

    def _finalise_enhancement_batch(self, batch_id: UUID) -> None:
        self.robot_client.send_robot_enhancement_batch_result(
            RobotEnhancementBatchResult(
                request_id=batch_id,
                error=None,
            ),
        )


def get_title_abstract_from_reference(reference: Reference) -> tuple[str | None, str | None]:
    if reference.enhancements is None:
        return None, None
    title: str | None = None
    abstract: str | None = None
    for enhancement in reference.enhancements:
        content = enhancement.content

        if isinstance(content, BibliographicMetadataEnhancement):
            title = content.title

        elif isinstance(content, AbstractContentEnhancement):
            abstract = content.abstract

    return title, abstract
