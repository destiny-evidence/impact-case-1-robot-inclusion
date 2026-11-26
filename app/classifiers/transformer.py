"""A list of transformer Classifiers and parameter spaces to validate."""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .util import AbstractClassifier

if TYPE_CHECKING:
    from datasets import Dataset


class HuggingfaceClassifier(AbstractClassifier):
    def __init__(self, path: Path | str) -> None:
        import torch
        from sklearn.exceptions import UndefinedMetricWarning
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model_path = Path(path)
        source = str(model_path.resolve())
        super().__init__(path=model_path)

        info_path = model_path / "model_info.json"
        if not info_path.exists():
            raise FileNotFoundError(f"Missing model_info.json in {source}")

        with info_path.open("r", encoding="utf-8") as fp:
            self.info: dict[str, Any] = json.load(fp)

        self.model_name: str = self.info["model_name"]
        self.model_max_length: int = int(self.info.get("model_max_length", 512))
        self.model_params: dict[str, Any] = self.info.get("model_params", {})
        raw_classes = self.info.get("classes_")
        self.classes_: np.ndarray | None = np.asarray(raw_classes) if raw_classes is not None else None
        self.threshold_: float = self.info.get("threshold") or 0.5

        self.logger = logging.getLogger("classify.inout.transformer")
        warnings.filterwarnings("ignore", category=UndefinedMetricWarning)
        self.logger.info(f"Loading trained model from {source}")

        self.model_ = AutoModelForSequenceClassification.from_pretrained(
            source,
            dtype=torch.float32,
        )
        self.model_.eval()

        tokenizer_source = source if (model_path / "tokenizer_config.json").exists() else self.model_name
        self.tokenizer_ = AutoTokenizer.from_pretrained(
            tokenizer_source,
            model_max_length=self.model_max_length,
        )

    def tokenize(self, texts: list[str], labels: np.ndarray | None) -> Dataset:
        from datasets import Dataset
        from torch import long, tensor

        params: dict[str, Any] = {"text": texts}
        if labels is not None:
            params["labels"] = tensor(labels, dtype=long)
        dataset = Dataset.from_dict(params)

        dataset = dataset.map(lambda x: self.tokenizer_(x["text"], padding="max_length", truncation=True), batched=True)  # type: ignore[misc]
        dataset.set_format("torch")

        return dataset.remove_columns("text")

    def predict_proba(self, X: list[str]) -> np.ndarray:
        import torch

        if self.model_ is None:
            raise RuntimeError("Model must be trained before predicting!")

        self.logger.debug(f"Tokenising {len(X):,} texts")

        dataset = self.tokenize(texts=X, labels=None)
        self.logger.debug("Predicting on texts")
        with torch.no_grad():
            y_pred = self.model_.predict_proba(dataset)
            if type(y_pred) is torch.Tensor:
                y_pred = y_pred.numpy()
            if len(y_pred.shape) > 1:
                y_pred = y_pred[:, 1]
        self.logger.debug(f"  > Predictions include {(y_pred > self.threshold_).sum():,} records at threshold > {self.threshold_}")
        return y_pred

    def predict(self, X: list[str]) -> np.ndarray:
        if self.classes_ is None:
            raise RuntimeError("Model must be trained before predicting!")
        return self.predict_proba(X) > self.threshold_
