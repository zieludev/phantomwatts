"""How fast a rider goes for a given power: a steady-state road load model."""

import math
from dataclasses import dataclass

GRAVITY = 9.80665
_MAX_SPEED_MPS = 50.0
_BISECTION_STEPS = 50


@dataclass(frozen=True)
class Road:
    """What the rider pushes against, in the terms of the FTMS simulation parameters.

    FTMS names the parameters but not the formula. Here a positive wind speed is a headwind and
    the drag force is cw / 2 * airspeed squared, the reading under which the 0.51 kg/m that apps
    usually send gives a realistic 31 km/h at 200 W on the flat.
    """

    grade_percent: float = 0.0
    wind_speed_mps: float = 0.0
    crr: float = 0.004
    cw_kg_per_m: float = 0.51


def resisting_force(speed_mps: float, road: Road, mass_kg: float) -> float:
    slope = math.atan(road.grade_percent / 100)
    air_speed = speed_mps + road.wind_speed_mps
    weight = mass_kg * GRAVITY
    return (
        weight * math.sin(slope)
        + weight * road.crr * math.cos(slope)
        + road.cw_kg_per_m / 2 * air_speed * abs(air_speed)
    )


def steady_speed(power_w: float, road: Road, mass_kg: float) -> float:
    """Speed at which the road takes exactly the power the rider gives."""

    def surplus(speed_mps: float) -> float:
        return power_w - speed_mps * resisting_force(speed_mps, road, mass_kg)

    low, high = 0.0, _MAX_SPEED_MPS
    if surplus(high) >= 0:
        return high
    # Downhill the surplus rises before it falls, but it changes sign only once, from
    # positive to negative, so a strict "> 0" keeps the root between low and high and finds
    # the coasting speed rather than standing still.
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2
        if surplus(middle) > 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2
