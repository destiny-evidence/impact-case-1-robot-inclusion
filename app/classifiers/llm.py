import asyncio
import copy
import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, cast

import yaml
from destiny_sdk.enhancements import BooleanAnnotation
from litellm import completion as prompt_llm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse
from pydantic import BaseModel, ConfigDict, Field

from app.util import get_settings, measure_runtime
from app.util.util import RateLimiter

settings = get_settings()
CONFIG_DIVISION = 50 * "!"


class ResponseAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Annotated[bool, Field(..., description="The LLM's annotation for this attribute.")]
    reasoning: Annotated[str, Field(..., description="Reasoning or explanation for the annotation decision")]


class ResponseSchemaDEET(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute_0: ResponseAttribute


class ResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: bool = Field(..., description="Final annotation.")
    reasoning: str = Field(..., description="Step-by-step assessment against the criterion.")


class CommunicationFormat(str, Enum):  # noqa: UP042
    deet = "deet"
    optimized = "optimized"


class SystemPrompt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    system_prompt: str = Field(description="System prompt that defines the task and role")
    prompt: str = Field(description="Prompt for the inclusion rule")


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
        default=settings.llm_max_context_tokens,
        description="Maximum input context length in tokens (system + prompt + attributes + document).",
    )
    communication_format: CommunicationFormat = Field(
        default=CommunicationFormat.deet,
        description="Response format to follow DEET standard or format optimised for single boolean decisions",
    )
    votes: int = Field(
        default=1,
        description="When >1, will repeatedly run prompt and return majority decision.",
    )

    @classmethod
    def from_file(cls, path: Path) -> "PromptConfig":
        with Path.open(path) as fp:
            splits = fp.read().split(CONFIG_DIVISION, maxsplit=1)
            if len(splits) != 2:  # noqa: PLR2004
                raise RuntimeError("Looks like the prompt config is not split into two parts by the correct division")
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


# Semaphore to limit the number of parallel prompts
_prompting_semaphore = asyncio.Semaphore(settings.llm_max_concurrent_prompts)
# Rate limiter to make sure we are not exceeding the API limits
_rate_limiter = RateLimiter(rate=settings.llm_prompts_per_minute, period=60.0)


class LLMClassifier:
    """
    LiteLLM wrapper to imitate the flow of DEET.

    Entrypoint for DEET usually is `deet.extractors.llm_data_extractor.LLMDataExtractor.extract_from_document()`:
    https://github.com/destiny-evidence/data-extraction-evaluation-toolkit/blob/main/deet/extractors/llm_data_extractor.py#L300

    The two communication_format modes allow to switch between a version that is optimised for a single boolean decision
    or an exact replication of the DEET protocol.
    """

    def __init__(self, config: PromptConfig, scheme: str, label: str) -> None:
        self.config = config
        self.scheme = scheme
        self.label = label
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

    async def _call_llm(self, text: str, seed_offset: int = 0) -> tuple[str, list[dict[str, Any]], int, int, int, float]:
        messages, schema = self._prepare_messages(text=text)
        est_num_tokens = estimate_prompt_tokens(messages)
        if est_num_tokens > self.config.max_context_tokens:
            raise RuntimeError(f"This request likely exceeds the maximum prompt length: {est_num_tokens:,} > {self.config.max_context_tokens:,}")

        def _run() -> tuple[float, ModelResponse | CustomStreamWrapper]:
            with measure_runtime() as process_seconds_:
                response_ = prompt_llm(
                    model=self.config.model,
                    api_key=settings.llm_azure_api_key,
                    api_base=settings.llm_azure_api_base,
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
                    timeout=settings.llm_timeout,
                    num_retries=settings.llm_num_retries,
                )
            return process_seconds_, response_

        # Wait to the prompt until we have enough capacity (not too many parallel threads/prompts)
        async with _prompting_semaphore:
            await _rate_limiter.acquire()
            process_seconds, response = await asyncio.to_thread(_run)

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
            messages.append({"role": "user", "content": text})
            return messages, ResponseSchema

        raise ValueError(f"Undefined communication format: {self.config.communication_format}")

    def _parse_response(self, response: ModelResponse | CustomStreamWrapper, process_seconds: float) -> tuple[str, int, int, int]:
        num_input_tokens: int = -1
        num_output_tokens: int = -1
        num_cached_tokens: int = -1
        if hasattr(response, "usage") and response.usage is not None:
            if hasattr(response.usage, "prompt_tokens"):
                num_input_tokens = response.usage.prompt_tokens or 0
            if hasattr(response.usage, "completion_tokens"):
                num_output_tokens = response.usage.completion_tokens or 0
            if hasattr(response.usage, "prompt_tokens_details"):
                num_cached_tokens = response.usage.prompt_tokens_details or 0

        choice = response.choices[0]  # type: ignore[union-attr]
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
            response_content = cast("str", msg.content)
        elif getattr(msg, "tool_calls", None):
            response_content = msg.tool_calls[0].function.arguments  # type: ignore[index, union-attr]
        else:
            raise RuntimeError(f"Unclear response! {usage_note}\n{msg}")
        return response_content, num_input_tokens, num_output_tokens, num_cached_tokens

    def _convert_response(self, response_content: str) -> BooleanAnnotation:
        if self.config.communication_format == CommunicationFormat.deet:
            deet_response = ResponseSchemaDEET.model_validate_json(response_content)
            return BooleanAnnotation(
                scheme=self.scheme,
                label=self.label,
                value=deet_response.attribute_0.decision,
                score=None,
                data={"reasoning": deet_response.attribute_0.reasoning},
            )

        if self.config.communication_format == CommunicationFormat.optimized:
            response = ResponseSchema.model_validate_json(response_content)
            return BooleanAnnotation(
                scheme=self.scheme,
                label=self.label,
                value=response.decision,
                score=None,
                data={"reasoning": response.reasoning},
            )

        raise RuntimeError(f"Invalid communication format: {self.config.communication_format}")

    async def annotate(self, text: str) -> BooleanAnnotation:
        num_majority = self.config.votes // 2 + 1

        annotations: list[BooleanAnnotation] = []
        num_incl = 0
        num_excl = 0
        for vote_num in range(self.config.votes):
            response_content, _messages, _num_input_tokens, _num_output_tokens, _num_cached_tokens, _process_seconds = await self._call_llm(
                text,
                seed_offset=vote_num,
            )
            annotation = self._convert_response(response_content)
            annotations.append(annotation)
            num_incl += int(annotation.value)
            num_excl += int(not annotation.value)

            # Check if we already found a majority so we can stop early
            if num_incl >= num_majority or num_excl >= num_majority:
                break

        return BooleanAnnotation(
            scheme=self.scheme,
            label=self.label,
            value=num_incl >= num_majority,
            score=num_incl / self.config.votes,
            data={
                "votes": [{"value": annotation.value, "reasoning": annotation.data.get("reasoning")} for annotation in annotations],
            },
        )
