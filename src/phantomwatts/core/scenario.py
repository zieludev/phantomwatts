"""What the rider does over time: the scripted input to the trainer simulation."""

import random
import re
from dataclasses import dataclass
from typing import Protocol

DEFAULT_CADENCE_RPM = 85.0
_SEED_STRIDE = 1_000_003


@dataclass(frozen=True)
class RiderInput:
    """The power the rider would put out on a free-running trainer, and the cadence."""

    power_w: float
    cadence_rpm: float


class Scenario(Protocol):
    def at(self, t: float) -> RiderInput:
        """Rider input `t` seconds into the ride."""
        ...


@dataclass(frozen=True)
class Constant:
    power_w: float = 150.0
    cadence_rpm: float = DEFAULT_CADENCE_RPM

    def at(self, t: float) -> RiderInput:
        return RiderInput(self.power_w, self.cadence_rpm)


@dataclass(frozen=True)
class Ramp:
    """Power moves linearly from `start_w` to `end_w`, then stays there."""

    start_w: float
    end_w: float
    duration_s: float
    cadence_rpm: float = DEFAULT_CADENCE_RPM

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError("a ramp needs a positive duration")

    def at(self, t: float) -> RiderInput:
        progress = min(1.0, max(0.0, t / self.duration_s))
        return RiderInput(self.start_w + (self.end_w - self.start_w) * progress, self.cadence_rpm)


@dataclass(frozen=True)
class Interval:
    power_w: float
    duration_s: float
    cadence_rpm: float = DEFAULT_CADENCE_RPM


@dataclass(frozen=True)
class Intervals:
    """Steps of fixed power. They repeat by default; otherwise the last one is held."""

    steps: tuple[Interval, ...]
    repeat: bool = True

    def __post_init__(self) -> None:
        if not self.steps or any(step.duration_s <= 0 for step in self.steps):
            raise ValueError("intervals need at least one step, each with a positive duration")

    def at(self, t: float) -> RiderInput:
        total = sum(step.duration_s for step in self.steps)
        position = t % total if self.repeat else min(t, total)
        for step in self.steps:
            if position < step.duration_s:
                return RiderInput(step.power_w, step.cadence_rpm)
            position -= step.duration_s
        last = self.steps[-1]
        return RiderInput(last.power_w, last.cadence_rpm)


@dataclass(frozen=True)
class Jitter:
    """Repeatable noise on top of another scenario: the same seed gives the same ride.

    The noise is a function of the time alone, so it does not depend on the step size the
    simulation happens to run with.
    """

    base: Scenario
    seed: int = 0
    power_fraction: float = 0.04
    cadence_rpm: float = 2.0
    period_s: float = 1.0

    def at(self, t: float) -> RiderInput:
        rider = self.base.at(t)
        noise = random.Random(self.seed * _SEED_STRIDE + int(t // self.period_s))
        power = rider.power_w * (1 + self.power_fraction * _centred(noise))
        cadence = rider.cadence_rpm + self.cadence_rpm * _centred(noise)
        if rider.cadence_rpm <= 0:
            cadence = 0.0
        return RiderInput(max(0.0, power), max(0.0, cadence))


def _centred(noise: random.Random) -> float:
    return 2 * noise.random() - 1


_NUMBER = r"(\d+(?:\.\d+)?)"
_CONSTANT = re.compile(rf"{_NUMBER}w")
_RAMP = re.compile(rf"{_NUMBER}w-{_NUMBER}w/{_NUMBER}s")
_STEP = re.compile(rf"{_NUMBER}w/{_NUMBER}s")

SCENARIO_FORMS = "constant:180w, ramp:100w-300w/600s or intervals:150w/60s,280w/30s"


def parse(spec: str, cadence_rpm: float = DEFAULT_CADENCE_RPM) -> Scenario:
    """Build a scenario from its one-line form, for example `intervals:150w/60s,280w/30s`."""
    kind, _, body = spec.strip().lower().partition(":")
    if kind == "constant" and (match := _CONSTANT.fullmatch(body)):
        return Constant(float(match[1]), cadence_rpm)
    if kind == "ramp" and (match := _RAMP.fullmatch(body)):
        return Ramp(float(match[1]), float(match[2]), float(match[3]), cadence_rpm)
    if kind == "intervals":
        matches = [_STEP.fullmatch(part.strip()) for part in body.split(",")]
        if all(matches):
            return Intervals(
                tuple(Interval(float(m[1]), float(m[2]), cadence_rpm) for m in matches if m)
            )
    raise ValueError(f"cannot read scenario {spec!r}; expected {SCENARIO_FORMS}")
