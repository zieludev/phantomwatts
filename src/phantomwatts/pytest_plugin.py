"""pytest plugin: the `phantomwatts_trainer` fixture.

Loaded by pytest through the `pytest11` entry point wherever phantomwatts is installed, so it
imports nothing heavy until a test asks for the fixture.
"""

from collections.abc import Iterator
from typing import Protocol

import pytest

from phantomwatts import defaults
from phantomwatts.core import scenario as scenarios
from phantomwatts.core.scenario import Scenario
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.testing import BackgroundTrainer

LOOPBACK_ANY_PORT = "tcp-server:127.0.0.1:0"


class TrainerFactory(Protocol):
    def __call__(
        self,
        scenario: Scenario | str = "constant:150w",
        *,
        expose: str | None = LOOPBACK_ANY_PORT,
        windows: bool = False,
        name: str = defaults.TRAINER_NAME,
        interval_s: float = 1.0,
        time_scale: float = 1.0,
    ) -> BackgroundTrainer: ...


@pytest.fixture
def phantomwatts_trainer() -> Iterator[TrainerFactory]:
    """Start virtual trainers; every one of them is stopped when the test ends.

        def test_my_app_starts_an_erg_workout(phantomwatts_trainer):
            trainer = phantomwatts_trainer("constant:180w")
            launch_my_app(hci_port=trainer.tcp_port)
            wanted = CommandRecord(SetTargetPower(200), Outcome.SUCCESS)
            trainer.wait_for(lambda: wanted in trainer.commands, timeout_s=60)

    By default the trainer's virtual controller listens on a free loopback port, to be read
    from `tcp_port`. Pass `expose` for a fixed one, such as the 6402 a Windows guest expects.
    """
    from phantomwatts.bumble_adapter.radio import VirtualRadio

    running: list[BackgroundTrainer] = []

    def start(
        scenario: Scenario | str = "constant:150w",
        *,
        expose: str | None = LOOPBACK_ANY_PORT,
        windows: bool = False,
        name: str = defaults.TRAINER_NAME,
        interval_s: float = 1.0,
        time_scale: float = 1.0,
    ) -> BackgroundTrainer:
        rider = scenarios.parse(scenario) if isinstance(scenario, str) else scenario
        trainer = BackgroundTrainer(
            TrainerSimulation(rider),
            VirtualRadio(expose=expose, windows=windows),
            name=name,
            interval_s=interval_s,
            time_scale=time_scale,
        )
        trainer.start()
        running.append(trainer)
        return trainer

    yield start
    for trainer in reversed(running):
        trainer.stop()
