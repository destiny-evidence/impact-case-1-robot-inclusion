"""Main robot entry point."""

import asyncio
from enum import Enum
from typing import Any

from .robots import EnhancementRunnerMode, LLMRobot, PrefilterRobot, QueryRobot, Robot


class RunnerTask(str, Enum):  # noqa: UP042
    """Enum for types of runners."""

    query_match = "query-match"
    query_unmatch = "query-unmatch"
    prefilter = "prefilter"
    llm = "llm"


def main(
    task: RunnerTask,
) -> None:
    """Start runner for selected `task`."""
    RobotRunner: Robot
    kwargs: dict[str, Any] = {}
    if task == RunnerTask.query_match:
        RobotRunner = QueryRobot
        kwargs = {"mode": EnhancementRunnerMode.match}
    elif task == RunnerTask.query_unmatch:
        RobotRunner = QueryRobot
        kwargs = {"mode": EnhancementRunnerMode.unmatch}
    elif task == RunnerTask.prefilter:
        RobotRunner = PrefilterRobot
    elif task == RunnerTask.llm:
        RobotRunner = LLMRobot
    else:
        raise ValueError(f"Unknown runner type: {task}")

    async def _main() -> None:
        runner = RobotRunner(name=task.value, **kwargs)
        await runner.start()

    asyncio.run(_main())


def run() -> None:
    import typer

    typer.run(main)


__all__ = ["LLMRobot", "PrefilterRobot", "QueryRobot", "Robot", "RunnerTask", "main", "run"]
