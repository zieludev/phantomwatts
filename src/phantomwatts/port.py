"""The seam between the GATT front end and whatever produces telemetry and takes commands.

The front end knows a trainer only as a `TrainerBackend`. The simulation in
`phantomwatts.core` is the one implementation today. Nothing here knows about Bluetooth.
"""

import enum
from dataclasses import dataclass
from typing import Protocol, TypeAlias


@dataclass(frozen=True)
class RequestControl:
    pass


@dataclass(frozen=True)
class Reset:
    pass


@dataclass(frozen=True)
class SetTargetPower:
    """ERG mode: hold this power whatever the cadence."""

    watts: int


@dataclass(frozen=True)
class SetTargetResistance:
    level: float


@dataclass(frozen=True)
class SetSimulation:
    """Simulation mode: resist like this road. The defaults are a typical road bike."""

    grade_percent: float
    wind_speed_mps: float = 0.0
    crr: float = 0.004
    cw_kg_per_m: float = 0.51


@dataclass(frozen=True)
class Start:
    pass


@dataclass(frozen=True)
class Stop:
    pass


@dataclass(frozen=True)
class Pause:
    pass


Command: TypeAlias = (
    RequestControl
    | Reset
    | SetTargetPower
    | SetTargetResistance
    | SetSimulation
    | Start
    | Stop
    | Pause
)


class Outcome(enum.Enum):
    SUCCESS = "success"
    NOT_SUPPORTED = "not supported"
    INVALID_PARAMETER = "invalid parameter"
    FAILED = "failed"
    CONTROL_NOT_PERMITTED = "control not permitted"


@dataclass(frozen=True)
class Range:
    minimum: float
    maximum: float
    step: float

    def __contains__(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class Capabilities:
    power_w: Range = Range(0, 2000, 1)
    resistance_level: Range = Range(0, 20, 0.1)


@dataclass(frozen=True)
class Telemetry:
    """One sample of what the trainer measures.

    `elapsed_s` and `distance_m` belong to the training session: they only grow while it is
    started. The crank counter and its event time run for as long as the rider pedals.
    """

    power_w: float
    cadence_rpm: float
    speed_mps: float
    heart_rate_bpm: float
    elapsed_s: float
    distance_m: float
    crank_revolutions: int
    last_crank_event_s: float


class TrainerBackend(Protocol):
    @property
    def capabilities(self) -> Capabilities: ...

    def execute(self, command: Command) -> Outcome: ...

    def release_control(self) -> None:
        """The controlling app is gone, for instance because it disconnected."""
        ...

    def telemetry(self) -> Telemetry: ...
