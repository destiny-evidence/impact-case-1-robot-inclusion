import copy
import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import yaml
from destiny_sdk.enhancements import BooleanAnnotation
from litellm import completion as prompt_llm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse
from pydantic import BaseModel, ConfigDict, Field

from app.util import get_settings, measure_runtime

settings = get_settings()


class ResponseAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning: Annotated[str, Field(..., description="Reasoning or explanation for the annotation decision")]
    decision: Annotated[bool, Field(..., description="The LLM's annotation for this attribute.")]


class ResponseSchemaDEET(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute_0: ResponseAttribute


class ResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning: str = Field(..., description="Step-by-step assessment against the criterion.")
    decision: bool = Field(..., description="Final annotation.")


class CommunicationFormat(str, Enum):
    deet = "deet"
    optimized = "optimized"


class SystemPrompt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    system_prompt: str = Field(description="System prompt that defines the task and role")
    prompt: str = Field(description="Prompt for the inclusion rule")


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scheme: str = Field(
        description="An identifier for the scheme of annotation",
        examples=["openalex:topic", "pubmed:mesh"],
        pattern=r"^[^/]+$",  # No slashes allowed
    )
    label: str = Field(description="A high level label for this annotation like the name of the topic")

    model: str = Field(default="gpt-4o-mini", description="LLM model identifier used for completions.")
    prompt_config: SystemPrompt = Field(description="Prompt configuration")

    temperature: float = Field(
        default=0.1,
        description="Sampling temperature for the LLM.",
        ge=0.0,
    )
    seed: int | None = Field(default=None, description="Random seed to anchor response generation variability; None means no seed")
    max_tokens: int | None = Field(
        default=None,
        description="Maximum number of tokens to generate (Leave blank for provider default).",
    )
    max_context_tokens: int = Field(
        default=settings.max_context_tokens,
        description="Maximum input context length in tokens (system + prompt + attributes + document).",
    )
    communication_format: CommunicationFormat = Field(
        default=CommunicationFormat.deet,
        description="Response format to follow DEET standard or format optimised for single boolean decisions",
    )

    @classmethod
    def from_file(cls, path: Path) -> "PromptConfig":
        with Path.open(path) as fp:
            splits = fp.read().split(50 * "-", maxsplit=1)
            if len(splits) != 2:  # noqa: PLR2004
                raise RuntimeError("Looks like the prompt config is not split into two parts by a line with 50 dashes")
            conf, prompt = splits
            data = yaml.safe_load(conf.strip())
            data.setdefault("prompt_config", {})["prompt"] = prompt.strip()
        return cls(**data)


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """
    Estimate the number of tokens required for a transaction.

    Heuristic approach informed by DEET (`deet.utils.tokenisation.count_tokens()`).
    """
    num = 0
    for message in messages:
        num += len(message["content"])
    return int(num / 4)


class LLM:
    def __init__(self, config: PromptConfig) -> None:
        self.config = config
        self.system_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": f"{self.config.prompt_config.system_prompt}\n\n## Inclusion criteria\n{self.config.prompt_config.prompt}",
            }
        ]

        # OpenAI and Azure OpenAI cache automatically; Anthropic-family models need a breakpoint.
        if config.model.startswith(("anthropic/", "bedrock/", "vertex_ai/claude")):
            last = self.system_messages[-1]
            last["content"] = [
                {
                    "type": "text",
                    "text": last["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]

    def _call_llm(self, text: str, seed_offset: int = 0) -> tuple[str, list[dict[str, Any]], int, int, int, float]:
        messages, schema = self._prepare_messages(text=text)
        est_num_tokens = estimate_prompt_tokens(messages)
        if est_num_tokens > self.config.max_context_tokens:
            raise RuntimeError(f"This request likely exceeds the maximum prompt length: {est_num_tokens:,} > {self.config.max_context_tokens:,}")

        with measure_runtime() as process_seconds:
            response = prompt_llm(
                model=self.config.model,
                api_key=settings.azure_api_key,
                api_base=settings.azure_api_base,
                messages=messages,
                seed=self.config.seed + seed_offset if self.config.seed is not None else None,
                temperature=self.config.temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "llm_annotation_response",
                        "schema": schema.model_json_schema(),
                        "strict": True,
                    },
                },
                max_tokens=self.config.max_tokens,
                timeout=settings.timeout,
                num_retries=settings.num_retries,
            )
        response_content, num_input_tokens, num_output_tokens, num_cached_tokens = self._parse_response(response, process_seconds=process_seconds)
        return response_content, messages, num_input_tokens, num_output_tokens, num_cached_tokens, process_seconds

    def _prepare_messages(self, text: str) -> tuple[list[dict[str, str]], type[ResponseSchemaDEET | ResponseSchema]]:
        messages: list[dict[str, str]] = copy.deepcopy(self.system_messages)

        if self.config.communication_format == CommunicationFormat.deet:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {"context": text, "attributes": [{"attribute_id": 0, "output_data_type": "boolean"}]},
                        ensure_ascii=False,
                    ),
                },
            )
            return messages, ResponseSchemaDEET

        if self.config.communication_format == CommunicationFormat.optimized:
            messages.append({"role": "user", "content": json.dumps({"record": text}, ensure_ascii=False)})
            return messages, ResponseSchema

        raise ValueError(f"Undefined communication format: {self.config.communication_format}")

    def _parse_response(self, response: ModelResponse | CustomStreamWrapper, process_seconds: float) -> tuple[str, int, int, int]:
        num_input_tokens: int = -1
        num_output_tokens: int = -1
        num_cached_tokens: int = -1
        if response.usage is not None:
            if hasattr(response.usage, "prompt_tokens"):
                num_input_tokens = response.usage.prompt_tokens or 0
            if hasattr(response.usage, "completion_tokens"):
                num_output_tokens = response.usage.completion_tokens or 0
            if hasattr(response.usage, "prompt_tokens_details"):
                num_cached_tokens = response.usage.prompt_tokens_details or 0

        choice = response.choices[0]
        msg = choice.message
        finish_reason = getattr(choice, "finish_reason", None)

        usage_note = f"{num_input_tokens:,} input tokens, {num_output_tokens:,} output tokens, {process_seconds} seconds"

        refusal = getattr(msg, "refusal", None)
        if refusal:
            raise RuntimeError(f"Model refused to answer ({usage_note}): {refusal}")

        if finish_reason == "length":
            raise RuntimeError(
                f"Response truncated at the token limit "
                f"(max_tokens={self.config.max_tokens}); {usage_note}. "
                f"Raise max_tokens or shorten the requested reasoning.",
            )

        if finish_reason == "content_filter":
            raise RuntimeError(f"Response blocked by the content filter ({usage_note}).")

        response_content: str
        if getattr(msg, "content", None) is not None:
            response_content = msg.content
        elif getattr(msg, "tool_calls", None):
            response_content = msg.tool_calls[0].function.arguments
        else:
            raise RuntimeError(f"Unclear response! {usage_note}\n{msg}")
        return response_content, num_input_tokens, num_output_tokens, num_cached_tokens

    def annotate(self, text: str) -> BooleanAnnotation:
        response_content, messages, num_input_tokens, num_output_tokens, num_cached_tokens, process_seconds = self._call_llm(text)

        if self.config.communication_format == CommunicationFormat.deet:
            deet_response = ResponseSchemaDEET.model_validate_json(response_content)
            return BooleanAnnotation(
                scheme=self.config.scheme,
                label=self.config.label,
                value=deet_response.attribute_0.decision,
                score=None,
                data={"reasoning": deet_response.attribute_0.reasoning},
            )

        if self.config.communication_format == CommunicationFormat.optimized:
            response = ResponseSchema.model_validate_json(response_content)
            return BooleanAnnotation(
                scheme=self.config.scheme,
                label=self.config.label,
                value=response.decision,
                score=None,
                data={"reasoning": response.reasoning},
            )

        raise RuntimeError(f"Invalid communication format: {self.config.communication_format}")
