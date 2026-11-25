import math

import pytest

from phantomwatts.core.physics import GRAVITY, Road, resisting_force, steady_speed

MASS_KG = 84.0


def test_rolling_resistance_is_all_there_is_at_a_standstill() -> None:
    assert resisting_force(0.0, Road(), MASS_KG) == pytest.approx(MASS_KG * GRAVITY * 0.004)


@pytest.mark.parametrize(
    ("road", "speed_mps"),
    [
        pytest.param(Road(), 3.0, id="flat-slow"),
        pytest.param(Road(), 14.0, id="flat-fast"),
        pytest.param(Road(grade_percent=8.0), 3.0, id="climb"),
        pytest.param(Road(grade_percent=-3.0), 12.0, id="descent-faster-than-coasting"),
        pytest.param(Road(wind_speed_mps=4.0), 8.0, id="headwind"),
        pytest.param(Road(wind_speed_mps=-4.0), 8.0, id="tailwind"),
        pytest.param(Road(grade_percent=2.0, crr=0.008, cw_kg_per_m=0.3), 8.0, id="gravel"),
    ],
)
def test_steady_speed_inverts_the_road_load(road: Road, speed_mps: float) -> None:
    power = speed_mps * resisting_force(speed_mps, road, MASS_KG)
    assert power > 0

    assert steady_speed(power, road, MASS_KG) == pytest.approx(speed_mps, abs=1e-6)


def test_no_power_on_the_flat_means_no_speed() -> None:
    assert steady_speed(0.0, Road(), MASS_KG) == pytest.approx(0.0, abs=1e-6)


def test_a_descent_is_coasted_at_terminal_speed() -> None:
    road = Road(grade_percent=-5.0)
    slope = math.atan(-0.05)
    downhill_pull = -MASS_KG * GRAVITY * (math.sin(slope) + road.crr * math.cos(slope))
    terminal = math.sqrt(downhill_pull / (road.cw_kg_per_m / 2))

    assert steady_speed(0.0, road, MASS_KG) == pytest.approx(terminal, abs=1e-6)


def test_speeds_are_plausible_for_a_road_bike() -> None:
    assert steady_speed(200.0, Road(), MASS_KG) * 3.6 == pytest.approx(31.5, abs=0.5)
    assert steady_speed(200.0, Road(grade_percent=5.0), MASS_KG) * 3.6 == pytest.approx(
        14.8, abs=0.5
    )


def test_harder_roads_are_slower() -> None:
    flat = steady_speed(200.0, Road(), MASS_KG)

    assert steady_speed(250.0, Road(), MASS_KG) > flat
    assert steady_speed(200.0, Road(grade_percent=3.0), MASS_KG) < flat
    assert steady_speed(200.0, Road(wind_speed_mps=5.0), MASS_KG) < flat
    assert steady_speed(200.0, Road(wind_speed_mps=-5.0), MASS_KG) > flat
    assert steady_speed(200.0, Road(crr=0.01), MASS_KG) < flat


def test_absurd_power_is_capped() -> None:
    assert steady_speed(1e9, Road(), MASS_KG) == 50.0
