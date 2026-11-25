"""The trainer simulation: control modes, control ownership, the training session, the rider."""

import enum
import math
from dataclasses import dataclass

from phantomwatts.core.physics import Road, steady_speed
from phantomwatts.core.scenario import Constant, Scenario
from phantomwatts.port import (
    Capabilities,
    Command,
    Outcome,
    Pause,
    RequestControl,
    Reset,
    SetSimulation,
    SetTargetPower,
    SetTargetResistance,
    Start,
    Stop,
    Telemetry,
)

_SPEED_TIME_CONSTANT_S = 3.0
_HEART_RATE_TIME_CONSTANT_S = 30.0
_FLAT_ROAD = Road()


class ControlMode(enum.Enum):
    FREE_RIDE = "free ride"
    ERG = "erg"
    SIMULATION = "simulation"
    RESISTANCE = "resistance"


class Session(enum.Enum):
    STOPPED = "stopped"
    STARTED = "started"
    PAUSED = "paused"


@dataclass(frozen=True)
class Rider:
    mass_kg: float = 75.0
    bike_mass_kg: float = 9.0
    resting_heart_rate_bpm: float = 60.0
    max_heart_rate_bpm: float = 185.0
    threshold_power_w: float = 250.0

    @property
    def total_mass_kg(self) -> float:
        return self.mass_kg + self.bike_mass_kg

    def heart_rate_at(self, power_w: float) -> float:
        """Heart rate the rider settles at: about 86 % of maximum at threshold power."""
        effort = min(1.0, max(0.0, power_w / (1.25 * self.threshold_power_w)))
        reserve = self.max_heart_rate_bpm - self.resting_heart_rate_bpm
        return self.resting_heart_rate_bpm + reserve * effort


class TrainerSimulation:
    """A smart trainer with a scripted rider on it.

    The rider pedals as the scenario says whatever the app does. Commands change what the
    trainer does with that: in ERG mode it pulls the power to the target, in simulation and
    resistance mode it changes the speed the same power gives. Starting, stopping and pausing
    the training session only gates the session counters, as on a real trainer.
    """

    def __init__(
        self,
        scenario: Scenario | None = None,
        rider: Rider | None = None,
        capabilities: Capabilities | None = None,
        *,
        erg_time_constant_s: float = 2.0,
    ) -> None:
        self._scenario = scenario or Constant()
        self._rider = rider or Rider()
        self._capabilities = capabilities or Capabilities()
        self._erg_time_constant_s = erg_time_constant_s

        self._has_control = False
        self._mode = ControlMode.FREE_RIDE
        self._session = Session.STOPPED
        self._target_power_w: int | None = None
        self._resistance_level: float | None = None
        self._road = _FLAT_ROAD

        self._clock_s = 0.0
        self._power_w = 0.0
        self._cadence_rpm = 0.0
        self._speed_mps = 0.0
        self._heart_rate_bpm = self._rider.resting_heart_rate_bpm
        self._elapsed_s = 0.0
        self._distance_m = 0.0
        self._crank_turns = 0.0
        self._last_crank_event_s = 0.0

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    @property
    def has_control(self) -> bool:
        """Whether an app holds the control permission it asked for."""
        return self._has_control

    @property
    def mode(self) -> ControlMode:
        return self._mode

    @property
    def session(self) -> Session:
        return self._session

    @property
    def target_power_w(self) -> int | None:
        return self._target_power_w

    @property
    def resistance_level(self) -> float | None:
        return self._resistance_level

    @property
    def road(self) -> Road:
        """The road the speed is computed for in the current mode."""
        return self._road

    def execute(self, command: Command) -> Outcome:
        if isinstance(command, RequestControl):
            self._has_control = True
            return Outcome.SUCCESS
        if not self._has_control:
            return Outcome.CONTROL_NOT_PERMITTED
        match command:
            case Reset():
                self._reset()
            case SetTargetPower(watts):
                if watts not in self._capabilities.power_w:
                    return Outcome.INVALID_PARAMETER
                self._enter(ControlMode.ERG, target_power_w=watts)
            case SetTargetResistance(level):
                if level not in self._capabilities.resistance_level:
                    return Outcome.INVALID_PARAMETER
                # A resistance level has no physical unit, so it is modelled as a hill: one
                # level is one percent of grade.
                self._enter(
                    ControlMode.RESISTANCE, resistance_level=level, road=Road(grade_percent=level)
                )
            case SetSimulation(grade_percent, wind_speed_mps, crr, cw_kg_per_m):
                self._enter(
                    ControlMode.SIMULATION,
                    road=Road(grade_percent, wind_speed_mps, crr, cw_kg_per_m),
                )
            case Start():
                return self._move_session({Session.STOPPED, Session.PAUSED}, Session.STARTED)
            case Stop():
                return self._move_session({Session.STARTED, Session.PAUSED}, Session.STOPPED)
            case Pause():
                return self._move_session({Session.STARTED}, Session.PAUSED)
        return Outcome.SUCCESS

    def release_control(self) -> None:
        self._has_control = False
        self._enter(ControlMode.FREE_RIDE)

    def telemetry(self) -> Telemetry:
        return Telemetry(
            power_w=self._power_w,
            cadence_rpm=self._cadence_rpm,
            speed_mps=self._speed_mps,
            heart_rate_bpm=self._heart_rate_bpm,
            elapsed_s=self._elapsed_s,
            distance_m=self._distance_m,
            crank_revolutions=int(self._crank_turns),
            last_crank_event_s=self._last_crank_event_s,
        )

    def step(self, dt: float) -> Telemetry:
        """Advance the ride by `dt` seconds."""
        if dt < 0:
            raise ValueError("time does not run backwards")
        if dt == 0:
            return self.telemetry()
        self._clock_s += dt
        rider = self._scenario.at(self._clock_s)
        self._cadence_rpm = max(0.0, rider.cadence_rpm)
        pedalling = self._cadence_rpm > 0

        if self._mode is ControlMode.ERG and self._target_power_w is not None:
            target = float(self._target_power_w) if pedalling else 0.0
            self._power_w = _approach(self._power_w, target, dt, self._erg_time_constant_s)
        else:
            self._power_w = max(0.0, rider.power_w) if pedalling else 0.0

        settled_speed = steady_speed(self._power_w, self._road, self._rider.total_mass_kg)
        self._speed_mps = _approach(self._speed_mps, settled_speed, dt, _SPEED_TIME_CONSTANT_S)
        self._heart_rate_bpm = _approach(
            self._heart_rate_bpm,
            self._rider.heart_rate_at(self._power_w),
            dt,
            _HEART_RATE_TIME_CONSTANT_S,
        )
        self._turn_cranks(dt)
        if self._session is Session.STARTED:
            self._elapsed_s += dt
            self._distance_m += self._speed_mps * dt
        return self.telemetry()

    def _enter(
        self,
        mode: ControlMode,
        *,
        target_power_w: int | None = None,
        resistance_level: float | None = None,
        road: Road = _FLAT_ROAD,
    ) -> None:
        self._mode = mode
        self._target_power_w = target_power_w
        self._resistance_level = resistance_level
        self._road = road

    def _move_session(self, allowed_from: set[Session], to: Session) -> Outcome:
        if self._session not in allowed_from:
            return Outcome.FAILED
        self._session = to
        return Outcome.SUCCESS

    def _reset(self) -> None:
        # Reset also ends the control permission (FTMS 4.16.2.1): the app has to ask again.
        self._has_control = False
        self._enter(ControlMode.FREE_RIDE)
        self._session = Session.STOPPED
        self._elapsed_s = 0.0
        self._distance_m = 0.0

    def _turn_cranks(self, dt: float) -> None:
        turns_per_second = self._cadence_rpm / 60
        before = int(self._crank_turns)
        self._crank_turns += turns_per_second * dt
        if int(self._crank_turns) > before:
            since_last_turn = (self._crank_turns - int(self._crank_turns)) / turns_per_second
            self._last_crank_event_s = self._clock_s - since_last_turn


def _approach(current: float, target: float, dt: float, time_constant_s: float) -> float:
    """First-order lag. For a target that holds still the result does not depend on `dt`."""
    return target + (current - target) * math.exp(-dt / time_constant_s)
