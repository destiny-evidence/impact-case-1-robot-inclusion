
from typing import Annotated,TypeAlias, Union

from .robot_00_query import EnhancementRunner as QueryRobot
from .robot_01_prefilter import EnhancementRunner as PrefilterRobot
from .robot_02_llm import EnhancementRunner as LLMRobot
Robot: TypeAlias = Annotated[
    type[QueryRobot] | type[PrefilterRobot] | type[LLMRobot],
    "Any concrete EnhancementRunner subclass in this package",
]
__all__ = [
    "QueryRobot",
    "PrefilterRobot",
    "LLMRobot",
    "Robot",
]
