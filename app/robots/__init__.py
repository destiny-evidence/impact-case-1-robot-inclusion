from typing import Annotated

from .robot_00_query import EnhancementRunner as QueryRobot
from .robot_01_prefilter import EnhancementRunner as PrefilterRobot
from .robot_02_llm import EnhancementRunner as LLMRobot

type Robot = Annotated[
    type[QueryRobot | PrefilterRobot | LLMRobot],
    "Any concrete EnhancementRunner subclass in this package",
]
__all__ = [
    "LLMRobot",
    "PrefilterRobot",
    "QueryRobot",
    "Robot",
]
