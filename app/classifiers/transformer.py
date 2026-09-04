"""A list of transformer Classifiers and parameter spaces to validate."""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from datasets import Dataset


class HuggingfaceClassifier:
    def __init__(self, path: Path) -> None:

        from sklearn.exceptions import UndefinedMetricWarning  # noqa: PLC0415
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

        self.logger = logging.getLogger("classify.inout.transformer")
        warnings.filterwarnings("ignore", category=UndefinedMetricWarning)

        source = str(path.resolve())
        self.logger.info(f"Loading trained model from {source}")
        with Path.open(path / "model_info.json") as fp:
            self.info: dict[str, Any] = json.load(fp)
        self.model_name: str = self.info["model_name"]
        self.model_max_length: int = self.info["model_max_length"]
        self.threshold_: float = self.info["threshold_"]
        self.classes_ = np.asarray(self.info.pop("classes_"))
        self.model_ = AutoModelForSequenceClassification.from_pretrained(source)
        self.tokenizer_ = AutoTokenizer.from_pretrained(self.model_name, model_max_length=self.model_max_length, cache_dir=path / "tokenizers")

    def tokenize(self, texts: list[str], labels: np.ndarray | None) -> Dataset:
        from datasets import Dataset  # noqa: PLC0415
        from torch import long, tensor  # noqa: PLC0415

        params: dict[str, Any] = {"text": texts}
        if labels is not None:
            params["labels"] = tensor(labels, dtype=long)
        dataset = Dataset.from_dict(params)

        dataset = dataset.map(lambda x: self.tokenizer_(x["text"], padding="max_length", truncation=True), batched=True)  # type: ignore[misc]
        dataset.set_format("torch")

        return dataset.remove_columns("text")

    def predict_proba(self, X: list[str]) -> np.ndarray:
        import torch  # noqa: PLC0415

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
