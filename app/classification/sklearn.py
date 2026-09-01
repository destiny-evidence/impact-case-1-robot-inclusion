import re
import logging
from pathlib import Path
from typing import Callable, Any, TYPE_CHECKING

import joblib
import numpy as np

if TYPE_CHECKING:
    from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)
NOALPH = re.compile(r'[^A-Za-z]+')
DEFAULT_THRESHOLD = 0.5


def preprocess_text(texts: list[str]) -> list[str]:
    return [NOALPH.sub(' ', text.lower()) for text in texts]


class SklearnClassifier:
    def __init__(self, path: Path):
        source = str((path / 'model.sklearn').resolve())
        logger.info(f'Loading trained model from {source}')

        info = joblib.load(source)
        info.pop('classes_', None)  # Remove in case it's set, so the remaining info is "clean" to use in `pipeline(**info)`
        model = info.pop('model_')
        pipeline = info.pop('pipeline_')
        threshold = info.pop('threshold_', DEFAULT_THRESHOLD)

        self.model_: 'Pipeline' = model
        self.threshold_: float = threshold
        self.pipeline_: Callable[..., 'Pipeline'] = pipeline
        self.params_: dict[str, Any] = info

    def predict_proba(self, X: list[str]) -> np.ndarray:
        y_pred: np.ndarray
        if hasattr(self.model_, 'predict_proba'):
            y_pred = self.model_.predict_proba(preprocess_text(X))[:, 1]
        else:
            y_pred = self.model_.predict(preprocess_text(X))
            if len(y_pred.shape) > 1:
                y_pred = y_pred[:, 1]

        logger.debug(f'  > Predictions include {(y_pred > self.threshold_).sum():,} records at threshold >{self.threshold_}')
        return y_pred

    def predict(self, X: list[str]) -> np.ndarray:
        return self.predict_proba(X) >= self.threshold_
