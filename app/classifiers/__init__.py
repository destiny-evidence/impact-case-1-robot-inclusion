from pathlib import Path

from .llm import LLMClassifier, PromptConfig, estimate_prompt_tokens
from .sklearn import SklearnClassifier
from .transformer import HuggingfaceClassifier

type Classifier = SklearnClassifier | HuggingfaceClassifier


def load_classifier(path: Path) -> Classifier:
    if path.suffix == ".sklearn":
        return SklearnClassifier(path)
    if path.suffix == "":
        return HuggingfaceClassifier(path)
    raise NotImplementedError(f"Unknown model type with path suffix '{path.suffix}'")


__all__ = [
    "Classifier",
    "LLMClassifier",
    "PromptConfig",
    "SklearnClassifier",
    "estimate_prompt_tokens",
    "load_classifier",
]
