import copy
import json
from pathlib import Path
from typing import Any
import yaml
from litellm import completion as prompt_llm
from pydantic import BaseModel, Field, ConfigDict
from destiny_sdk.enhancements import BooleanAnnotation
from app import get_settings
from app.util import measure_runtime

settings = get_settings()


class ResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning: str = Field(..., description="Step-by-step assessment against the criterion.")
    decision: bool = Field(..., description="Final annotation.")


class SystemPrompt(BaseModel):
    model_config = ConfigDict(extra='ignore')
    system_prompt: str = Field(description="System prompt that defines the task and role")
    prompt: str = Field(description="Prompt for the inclusion rule")


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')

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

    @classmethod
    def from_file(cls, path: Path) -> 'PromptConfig':
        with open(path, 'r') as fp:
            splits = fp.read().split(50 * '-', maxsplit=1)
            if len(splits) != 2:
                raise RuntimeError(f'Looks like the prompt config is not split into two parts by a line with 50 dashes')
            conf, prompt = splits
            data = yaml.safe_load(conf.strip())
            data.setdefault("prompt_config", {})["prompt"] = prompt.strip()
        return cls(**data)


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate the number of tokens required for a transaction.

    Heuristic approach informed by DEET (`deet.utils.tokenisation.count_tokens()`).
    """
    num = 0
    for message in messages:
        num += len(message['content'])
    return int(num / 4)


class LLM:
    def __init__(self, config: PromptConfig):
        self.config = config
        self.system_messages: list[dict[str, Any]] = [{
            "role": "system",
            "content": f'{self.config.prompt_config.system_prompt}\n\n## Inclusion criteria\n{self.config.prompt_config.prompt}',
        }]

        # OpenAI and Azure OpenAI cache automatically; Anthropic-family models need a breakpoint.
        if config.model.startswith(("anthropic/", "bedrock/", "vertex_ai/claude")):
            last = self.system_messages[-1]
            last["content"] = [{
                "type": "text",
                "text": last["content"],
                "cache_control": {"type": "ephemeral"},
            }]

    def _call_llm(self, text: str, seed_offset: int = 0) -> tuple[str, list[dict[str, Any]], int, int, int, float]:
        messages: list[dict[str, str]] = [
            *copy.deepcopy(self.system_messages),
            {"role": "user", "content": json.dumps({"record": text}, ensure_ascii=False)}
        ]
        est_num_tokens = estimate_prompt_tokens(messages)
        if est_num_tokens > self.config.max_context_tokens:
            raise RuntimeError(f'This request likely exceeds the maximum prompt length: {est_num_tokens:,} > {self.config.max_context_tokens:,}')

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
                        "schema": ResponseSchema.model_json_schema(),
                        "strict": True,
                    },
                },
                max_tokens=self.config.max_tokens,
                timeout=settings.timeout,
                num_retries=settings.num_retries,
            )

        num_input_tokens: int = 0
        num_output_tokens: int = 0
        num_cached_tokens: int = 0
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

        usage_note = (
            f'{num_input_tokens:,} input tokens, {num_output_tokens:,} output tokens, '
            f'{process_seconds} seconds'
        )

        refusal = getattr(msg, "refusal", None)
        if refusal:
            raise RuntimeError(f'Model refused to answer ({usage_note}): {refusal}')

        if finish_reason == "length":
            raise RuntimeError(
                f'Response truncated at the token limit '
                f'(max_tokens={self.config.max_tokens}); {usage_note}. '
                f'Raise max_tokens or shorten the requested reasoning.',
            )

        if finish_reason == "content_filter":
            raise RuntimeError(f'Response blocked by the content filter ({usage_note}).')

        response_content: str
        if getattr(msg, "content", None) is not None:
            response_content = msg.content
        elif getattr(msg, "tool_calls", None):
            response_content = msg.tool_calls[0].function.arguments
        else:
            raise RuntimeError(f'Unclear response! {usage_note}\n{msg}')

        return response_content, messages, num_input_tokens, num_output_tokens, num_cached_tokens, process_seconds

    def annotate(self, text: str) -> BooleanAnnotation:
        response_content, messages, num_input_tokens, num_output_tokens, num_cached_tokens, process_seconds = self._call_llm(text)
        response = ResponseSchema.model_validate_json(response_content)

        return BooleanAnnotation(
            scheme=self.config.scheme,
            label=self.config.label,
            value=response.decision,
            score=None,
            data={'reasoning': response.reasoning},
        )
