"""
A list of transformer Classifiers and parameter spaces to validate.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Any
from pathlib import Path
import json
import numpy as np

if TYPE_CHECKING:
    from datasets import Dataset
    from transformers import TokenizersBackend
    from transformers.trainer_utils import PredictionOutput


class HuggingfaceClassifier:
    def __init__(self, path: Path) -> None:

        from sklearn.exceptions import UndefinedMetricWarning
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.logger = logging.getLogger('classify.inout.transformer')
        warnings.filterwarnings('ignore', category=UndefinedMetricWarning)

        source = str(path.resolve())
        self.logger.info(f'Loading trained model from {source}')
        with open(path / 'model_info.json', 'r') as fp:
            self.info = json.load(fp)
        self.model_name = self.info['model_name']
        self.model_max_length = self.info['model_max_length']
        self.classes_ = np.asarray(self.info.pop('classes_'))
        self.model_ = AutoModelForSequenceClassification.from_pretrained(source)
        self.tokenizer_ = AutoTokenizer.from_pretrained(self.model_name, model_max_length=self.model_max_length, cache_dir=path / "tokenizers")

    def tokenize(self, texts: list[str], labels: np.ndarray | None) -> Dataset:
        from datasets import Dataset
        from torch import tensor, long

        params: dict[str, Any] = {'text': texts}
        if labels is not None:
            params['labels'] = tensor(labels, dtype=long)
        dataset = Dataset.from_dict(params)

        dataset = dataset.map(lambda x: self.tokenizer_(x['text'], padding='max_length', truncation=True), batched=True)  # type: ignore[misc]
        dataset.set_format('torch')

        return dataset.remove_columns('text')

    def predict_proba(self, X: list[str]) -> np.ndarray:
        import torch

        if self.model_ is None:
            raise RuntimeError('Model must be trained before predicting!')

        self.logger.debug(f'Tokenising {len(X):,} texts')

        dataset = self.tokenize(texts=X, labels=None)
        self.logger.debug('Predicting on texts')
        # self.model_.eval()
        with torch.no_grad():
            y_pred = self.model_.predict_proba(dataset)
            if type(y_pred) is torch.Tensor:
                y_pred = y_pred.numpy()
            if len(y_pred.shape) > 1:
                y_pred = y_pred[:, 1]
        self.logger.debug(f'  > Predictions include {(y_pred > 0.5).sum():,} records at threshold >0.5')
        return y_pred

    def predict(self, X: list[str]) -> np.ndarray:
        if self.classes_ is None:
            raise RuntimeError('Model must be trained before predicting!')
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]
