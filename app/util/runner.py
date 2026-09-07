"""Abstract runner class with main loop."""

import asyncio
import contextlib
import signal
import sys
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from opentelemetry import trace

from .config import OTelConfig, get_logger, get_settings
from .repository import Repository
from .telemetry import configure_telemetry, instrument, shutdown_telemetry

if TYPE_CHECKING:
    from types import FrameType

    from destiny_sdk.robots import RobotAutomationIn


class Runner(ABC):
    """Abstract runner class with main loop."""

    def __init__(self, name: str) -> None:
        """Initialise runner."""
        self.settings = get_settings()
        self.name = name

        logger = get_logger("inclusion-robot", init_logging=True, base_level=self.settings.loglevel)
        self.logger = logger.getChild(name)
        self.loop_logger = self.logger.getChild("loop")
        self.total_entries_processed = 0

        if self.settings.otel_enabled:
            configure_telemetry(
                self.settings.otel_config or OTelConfig(),
                task=name,
                environment=self.settings.env.value,
                version=self.settings.robot_version,
            )
            instrument(capture_llm_content=self.settings.otel_capture_llm_content)
        self.tracer = trace.get_tracer(__name__)

        self.repository = Repository(settings=self.settings, logger=logger.getChild("repository"))
        self.shutdown_event = asyncio.Event()

    @abstractmethod
    def _automation_query(self) -> "RobotAutomationIn":
        """
        Robot automation query this runner is listening for.

        Check the documentation for more information:
        https://destiny-evidence.github.io/destiny-repository/procedures/robot-automation.html#query
        """
        raise NotImplementedError

    @abstractmethod
    async def _loop_task(self) -> bool:
        """Perform iteration of runner loop. Returns True if a batch was processed."""
        raise NotImplementedError

    async def _traced_loop_task(self) -> bool:
        """Run one iteration of `_loop_task` as the root span of its own trace."""
        with self.tracer.start_as_current_span("robot.loop") as span:
            span.set_attribute("robot.task", self.name)
            try:
                return await self._loop_task()
            except Exception as e:
                span.set_status(trace.StatusCode.ERROR, str(e))
                raise

    async def _main_loop(self) -> None:
        """Run main loop."""
        loop_logger = self.logger.getChild("loop")

        while True:
            try:
                did_work = await self._traced_loop_task()
            except Exception as e:
                loop_logger.error(f"Encountered an error: {e}")
                loop_logger.exception(e)
                did_work = False

            # Only idle when there was nothing to do, so a backlog is drained at full speed
            if not did_work and self.settings.interval_seconds > 0:
                await asyncio.sleep(self.settings.interval_seconds)

    async def stop(self) -> None:
        """Initiate graceful halting procedure."""
        self.logger.info("Was asked to stop, initiating graceful shutdown...")
        self.shutdown_event.set()

    async def start(self) -> None:
        """Robot's core working method."""
        self.logger.info(
            f"Initialising main loop for {self.settings.robot_name} with a {self.settings.interval_seconds}s polling interval "
            f"and default batch size {self.settings.batch_size:,}.",
        )

        def shutdown_handler(signum: int, _frame: "FrameType | None") -> None:
            self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.shutdown_event.set()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        try:
            main_loop_task = asyncio.create_task(self._main_loop())
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())

            # Wait for either the polling task to complete or shutdown signal
            _done, pending = await asyncio.wait(
                [main_loop_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            self.logger.info("Shutdown complete")

        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt, shutting down...")
            sys.exit(0)
        except Exception as e:
            self.logger.error(f"Fatal error occurred: {e}")
            self.logger.exception(e)
            sys.exit(1)
        finally:
            # Flush buffered spans on every exit path, including sys.exit().
            shutdown_telemetry()
