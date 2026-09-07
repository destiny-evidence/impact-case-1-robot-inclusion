"""Main robot entry point."""

import asyncio
from enum import Enum

from .robots import LLMRobot, PrefilterRobot, QueryRobot, Robot


class RunnerTask(str, Enum):  # noqa: UP042
    """Enum for types of runners."""

    query = "query"
    prefilter = "prefilter"
    llm = "llm"


def main(
    task: RunnerTask,
) -> None:
    """Start runner for selected `task`."""
    RobotRunner: Robot
    if task == RunnerTask.query:
        RobotRunner = QueryRobot
    elif task == RunnerTask.prefilter:
        RobotRunner = PrefilterRobot
    elif task == RunnerTask.llm:
        RobotRunner = LLMRobot
    else:
        raise ValueError(f"Unknown runner type: {task}")

    async def _main() -> None:
        runner = RobotRunner(name=task.value)
        await runner.start()

    asyncio.run(_main())


def run() -> None:
    import typer  # noqa: PLC0415

    typer.run(main)


__all__ = ["LLMRobot", "PrefilterRobot", "QueryRobot", "Robot", "RunnerTask", "main", "run"]
