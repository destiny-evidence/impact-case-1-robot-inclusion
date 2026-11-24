from abc import abstractmethod
from abc import ABC
from pathlib import Path

import numpy as np


class AbstractClassifier(ABC):
    def __init__(self, path: Path) -> None:
        self.model_path = path

    @abstractmethod
    def predict_proba(self, X: list[str]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def predict(self, X: list[str]) -> np.ndarray:
        raise NotImplementedError
