"""API config parsing and model."""

import logging
import tomllib
from collections import OrderedDict
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from destiny_sdk.visibility import Visibility
from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def configure_logging(base_level: int | str = "INFO") -> None:
    """Configure logging for the application."""
    httpx_level = logging.DEBUG if base_level in {logging.DEBUG, "DEBUG"} else logging.WARNING
    logging.getLogger("httpx").setLevel(httpx_level)

    logging.basicConfig(
        level=base_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def get_logger(
    name: str,
    level: str | None = None,
    init_logging: bool = False,
    base_level: int | str = "INFO",
) -> logging.Logger:
    """Get an initialised logger."""
    if init_logging:
        configure_logging(base_level=base_level)
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger


class Environment(StrEnum):
    """
    Environment that therobot is running in.

    **Allowed values**:
    - `local`: The robot is running locally
    - `development`: The robot is running in development
    - `staging`: The robot is running in staging
    - `production`: The robot is running in production
    - `test`: The robot is running as a test fixture for the repository
    """

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


def read_toml_value(path_to_toml: str | Path, *path: str) -> str:
    """Read the information from the pyproject.toml."""
    with open(path_to_toml, "rb") as toml_file:  # noqa: PTH123
        current_node: Any | None = tomllib.load(toml_file)
        steps = ""
        for step in path:
            if type(current_node) is not dict:
                raise ValueError(f"Cannot follow `step` after `{steps}` in {path_to_toml}")

            steps += f".{step}"

            if not (current_node := current_node.get(step, None)):
                raise ValueError(f"`{steps}` not present in {path_to_toml}")

        if current_node is None or type(current_node) is not str:
            raise ValueError(
                f"{steps} did not lead to singular string value in {path_to_toml}",
            )

        return cast("str", current_node)  # type: ignore[redundant-cast]


class Settings(BaseSettings):
    """Settings model for polling robot."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.secret.shared", ".env.secret"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime settings
    loglevel: str | int = Field(default="INFO", description="Logging level")

    # Robot identification settings
    robot_id: UUID = Field(
        description="Client id needed for communicating with destiny repository.",
    )
    robot_version: str = Field(
        default=read_toml_value("pyproject.toml", "project", "version"),
        pattern="[0-9]+.[0-9]+.[0-9]+",
        description="Semantic version of the robot",
    )
    robot_name: str = Field(
        default=read_toml_value("pyproject.toml", "project", "name"),
        pattern="([a-z]+-)+",
        description="Name of the robot",
    )

    # Repository settings
    base_url: HttpUrl = Field(
        default=HttpUrl("https://api.staging.evidence-repository.org"),
        description="DESTinY repository API endpoint",
    )
    env: Environment = Field(
        default=Environment.STAGING,
        description="The environment this robot is deployed in.",
    )

    # Robot looping settings
    interval_seconds: int = Field(
        default=30,
        description="How long to sleep between each loop",
    )
    batch_size: int = Field(
        default=100,
        description="The number of references to include per batch",
    )

    # Robot identification and authentication settings
    robot_secret: SecretStr = Field(
        description="Secret needed for communicating with destiny repo.",
    )
    # Miscellaneous settings
    min_text_length: int = Field(
        default=200,
        description="Minimum length of title+abstract that we might consider for classification",
    )

    # Files for search query, pre-filter model, and LLM prompts
    search_query: Path = Field(default=Path(".configs/search-query.txt"), description="Path to file containing search query")
    model_prefilter: Path = Field(default=Path(".configs/models/high-recall-svm.sklearn"), description="Path to serialised sklearn model")
    prompt_high_recall: Path = Field(default=Path(".configs/prompts/high-recall.txt"), description="Path to prompt/model config for high-recall LLM")
    prompt_balanced: Path = Field(default=Path(".configs/prompts/balanced.txt"), description="Path to prompt/model config for balanced LLM")
    prompt_high_precision: Path = Field(default=Path(".configs/prompts/high-precision.txt"), description="Path to prompt/model config for high-precision LLM")

    # Pre-filter execution settings
    batch_size_prefilter: int = Field(
        default=10,
        description="Processing the full enhancement batch at once might consume too much RAM, so we will process the data in smaller batches of this size.",
    )

    # LLM provider settings
    llm_azure_api_key: str | None = Field(
        default=None,
        description="Azure OpenAI API key if using Azure provider.",
    )
    llm_azure_api_base: str | None = Field(default=None, description="Base URL for azure openAI.")
    llm_max_context_tokens: int = Field(default=3000, description="Maximum number of context tokens to include in a single request per document.")
    llm_timeout: float = Field(default=60.0, description="Per-request timeout in seconds.", gt=0.0)
    llm_num_retries: int = Field(default=3, description="Retries on transient errors (429, 5xx, timeouts).", ge=0)
    llm_max_concurrent_prompts: int = Field(default=100, description="Maximum number of prompts to run in parallel", ge=1)
    llm_prompts_per_minute: int = Field(default=1200, description="Number of prompts per minute for the API endpoint", ge=1)

    # Enhancement settings
    enhancement_visibility: Visibility = Field(default=Visibility.PUBLIC, description="Visibility level for Enhancements")
    set_unseen_false: bool = Field(
        default=True,
        description="If true, set BooleanAnnotation(value=False) for references that are not classified because of missing abstracts or chained prompts.",
    )
    annotation_scheme_query: str = Field(default="search", description="Defines the value to use for BooleanAnnotation.scheme for search queries")
    annotation_scheme_incl: str = Field(
        default="domain-inclusion",
        description="Defines the value to use for BooleanAnnotation.scheme for inclusion classification",
    )
    annotation_label_query: str = Field(default="destiny-ic1-inclusion", description="Defines the value to use for BooleanAnnotation.label for search queries")
    annotation_label_prefilter: str = Field(
        default="destiny-prefilter",
        description="Defines the value to use for BooleanAnnotation.label for pre-filtering",
    )
    annotation_label_recall: str = Field(
        default="destiny-high-recall",
        description="Defines the value to use for BooleanAnnotation.label for high-recall LLM decisions",
    )
    annotation_label_balanced: str = Field(
        default="destiny-balanced",
        description="Defines the value to use for BooleanAnnotation.label for balanced LLM decisions",
    )
    annotation_label_precision: str = Field(
        default="destiny-high-precision",
        description="Defines the value to use for BooleanAnnotation.label for high-precision LLM decisions",
    )

    @property
    def prompt_configs(self) -> OrderedDict[str, Path]:
        # OrderedDict not necessary for newer python versions, just making extra sure...
        return OrderedDict(
            [
                (self.annotation_label_recall, self.prompt_high_recall),
                (self.annotation_label_balanced, self.prompt_balanced),
                (self.annotation_label_precision, self.prompt_high_precision),
            ],
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get a cached settings object."""
    return Settings()
