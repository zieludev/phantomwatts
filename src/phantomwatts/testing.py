"""A trainer for tests that are not async: it runs on a thread of its own.

The app under test usually lives outside the test (another process, a virtual machine), so the
test only starts a trainer, lets the app do its thing, and asserts on what it commanded.
"""

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.frontend import CommandRecord

if TYPE_CHECKING:
    from phantomwatts.bumble_adapter.radio import Radio
    from phantomwatts.bumble_adapter.service import RunningTrainer

_START_TIMEOUT_S = 15.0
_STOP_TIMEOUT_S = 15.0


class BackgroundTrainer:
    """A running trainer with a blocking interface.

    Read the simulation's state inside a `wait_for` predicate: those run on the trainer's own
    thread, between its steps.
    """

    def __init__(
        self,
        simulation: TrainerSimulation,
        radio: "Radio",
        *,
        name: str,
        interval_s: float = 1.0,
        time_scale: float = 1.0,
    ) -> None:
        self.simulation = simulation
        self._radio = radio
        self._name = name
        self._interval_s = interval_s
        self._time_scale = time_scale
        self._started: concurrent.futures.Future[RunningTrainer] = concurrent.futures.Future()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._thread_main, name="phantomwatts", daemon=True)

    @property
    def commands(self) -> list[CommandRecord]:
        return self._running.commands

    @property
    def connected(self) -> bool:
        return self._running.connected

    @property
    def tcp_port(self) -> int | None:
        return self._running.tcp_port

    def start(self) -> None:
        """Return once the trainer is advertising."""
        self._thread.start()
        self._started.result(_START_TIMEOUT_S)

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread.is_alive():
            self._thread.join(_STOP_TIMEOUT_S)

    def wait_for(self, predicate: Callable[[], bool], *, timeout_s: float = 10.0) -> None:
        """Block until `predicate()` holds, or raise TimeoutError."""
        if self._loop is None:
            raise RuntimeError("the trainer is not running")
        waiting = asyncio.run_coroutine_threadsafe(
            self._running.wait_for(predicate, timeout_s=timeout_s), self._loop
        )
        waiting.result()

    @property
    def _running(self) -> "RunningTrainer":
        return self._started.result(0)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            if not self._started.done():
                self._started.set_exception(error)
            else:
                raise

    async def _serve(self) -> None:
        from phantomwatts.bumble_adapter.service import serve

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with serve(self.simulation, radio=self._radio, name=self._name) as trainer:
            ride = asyncio.create_task(trainer.run(self._interval_s, self._time_scale))
            self._started.set_result(trainer)
            await self._stop.wait()
            ride.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ride
