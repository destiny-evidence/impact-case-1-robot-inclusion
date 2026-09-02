"""API config parsing and model."""

import logging
import tomllib
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from uuid import UUID
from pathlib import Path


def configure_logging(base_level: int | str = "INFO") -> None:
    """Configure logging for the application."""

    httpx_level = logging.DEBUG if (base_level == logging.DEBUG or base_level == "DEBUG") else logging.WARNING
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
    Environment that the toy robot is running in.

    As this robot is for demo purposes only, we do not accept `production` as a value

    **Allowed values**:
    - `local`: The robot is running locally
    - `development`: The robot is running in development
    - `staging`: The robot is running in staging
    - `test`: The robot is running as a test fixture for the repository
    """

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
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

        if type(current_node) is not str:
            raise ValueError(
                f"{steps} did not lead to singular string value in {path_to_toml}",
            )

        return current_node


class Settings(BaseSettings):
    """Settings model for polling robot."""

    model_config = SettingsConfigDict(
        env_file=(".env.secret", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    robot_secret: SecretStr = Field(
        description="Secret needed for communicating with destiny repo.",
    )
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

    base_url: HttpUrl = Field(
        default=HttpUrl("https://api.staging.evidence-repository.org"),
        description="DESTinY repository API endpoint",
    )

    env: Environment = Field(
        default=Environment.STAGING,
        description="The environment the toy robot is deployed in.",
    )

    llm_interval_seconds: int = Field(
        default=30,
        description=("How long to sleep between match requests (seconds)"),
    )
    classify_interval_seconds_interval_seconds: int = Field(
        default=30,
        description=("How long to sleep between classification batches (seconds)"),
    )
    batch_size: int = Field(
        default=10,
        description=("The number of references to include per batch"),
    )

    keycloak_id: str | None = Field(default=None, description="keycloak client id")
    keycloak_secret: SecretStr | None = Field(
        default=None,
        description="keycloak client secret",
    )
    keycloak_url: str = Field(
        default="https://auth.evidence-repository.org/",
        description="keycloak authentication url",
    )
    keycloak_realm: str = Field(default="destiny", description="keycloak realm ")

    min_abstract_length: int = Field(
        default=200,
        description=("Minimum length of abstract that we might consider for classification"),
    )

    # Provider credentials / settings (secrets redacted)
    azure_api_key: str | None = Field(
        default=None,
        description="Azure OpenAI API key if using Azure provider.",
    )
    azure_api_base: str | None = Field(default=None, description="Base URL for azure openAI.")
    max_context_tokens: int = Field(default=3000, description="Maximum number of context tokens to include in a single request per document.")
    timeout: float = Field(default=60.0, description="Per-request timeout in seconds.", gt=0.0)
    num_retries: int = Field(default=3, description="Retries on transient errors (429, 5xx, timeouts).", ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get a cached settings object."""
    return Settings()
