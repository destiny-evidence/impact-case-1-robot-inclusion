import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import joblib
import numpy as np

if TYPE_CHECKING:
    from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)
NOALPH = re.compile(r"[^A-Za-z]+")
DEFAULT_THRESHOLD = 0.5


def preprocess_text(texts: list[str]) -> list[str]:
    return [NOALPH.sub(" ", text.lower()) for text in texts]


class SklearnClassifier:
    def __init__(self, path: Path) -> None:
        logger.info(f"Loading trained model from {path}")

        info = joblib.load(path)
        info.pop("classes_", None)  # Remove in case it's set, so the remaining info is "clean" to use in `pipeline(**info)`
        model = info.pop("model_")
        threshold = info.pop("threshold_", DEFAULT_THRESHOLD)

        self.model_: Pipeline = model
        self.threshold_: float = threshold
        self.params_: dict[str, Any] = info

    def predict_proba(self, X: list[str]) -> np.ndarray:  # noqa: N803
        y_pred: np.ndarray
        if hasattr(self.model_, "predict_proba"):
            y_pred = self.model_.predict_proba(preprocess_text(X))[:, 1]
        else:
            y_pred = self.model_.predict(preprocess_text(X))
            if len(y_pred.shape) > 1:
                y_pred = y_pred[:, 1]

        logger.debug(f"  > Predictions include {(y_pred > self.threshold_).sum():,} records at threshold >{self.threshold_}")
        return y_pred

    def predict(self, X: list[str]) -> np.ndarray:  # noqa: N803
        return self.predict_proba(X) >= self.threshold_
