import pytest

from phantomwatts.core import scenario
from phantomwatts.core.scenario import Constant, Interval, Intervals, Jitter, Ramp, RiderInput


def test_constant() -> None:
    assert Constant(180, 92).at(0) == RiderInput(180, 92)
    assert Constant(180, 92).at(3600) == RiderInput(180, 92)


@pytest.mark.parametrize(
    ("t", "power"),
    [(0, 100), (150, 150), (300, 200), (600, 300), (900, 300)],
)
def test_ramp_climbs_then_holds(t: float, power: float) -> None:
    assert Ramp(100, 300, 600).at(t).power_w == pytest.approx(power)


def test_ramp_can_go_down() -> None:
    assert Ramp(300, 100, 100).at(25).power_w == pytest.approx(250)


WORKOUT = Intervals((Interval(150, 60, 85), Interval(280, 30, 100)))


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (0, RiderInput(150, 85)),
        (59.9, RiderInput(150, 85)),
        (60, RiderInput(280, 100)),
        (89.9, RiderInput(280, 100)),
        (90, RiderInput(150, 85)),
        (150, RiderInput(280, 100)),
    ],
)
def test_intervals_repeat(t: float, expected: RiderInput) -> None:
    assert WORKOUT.at(t) == expected


def test_intervals_that_do_not_repeat_hold_the_last_step() -> None:
    once = Intervals(WORKOUT.steps, repeat=False)

    assert once.at(90) == RiderInput(280, 100)
    assert once.at(10_000) == RiderInput(280, 100)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Intervals(()),
        lambda: Intervals((Interval(100, 0),)),
        lambda: Ramp(100, 200, 0),
    ],
)
def test_scenarios_without_duration_are_refused(build: object) -> None:
    assert callable(build)
    with pytest.raises(ValueError, match="positive duration"):
        build()


def test_jitter_is_the_same_ride_for_the_same_seed() -> None:
    times = [second + 0.5 for second in range(120)]

    first = [Jitter(Constant(200), seed=7).at(t) for t in times]
    again = [Jitter(Constant(200), seed=7).at(t) for t in times]
    other = [Jitter(Constant(200), seed=8).at(t) for t in times]

    assert first == again
    assert first != other
    assert len({sample.power_w for sample in first}) > 100


def test_jitter_stays_close_to_the_script() -> None:
    jitter = Jitter(Constant(200, 90), seed=3, power_fraction=0.04, cadence_rpm=2.0)

    samples = [jitter.at(t) for t in range(500)]

    assert all(192 <= sample.power_w <= 208 for sample in samples)
    assert all(88 <= sample.cadence_rpm <= 92 for sample in samples)


def test_jitter_holds_one_value_per_period() -> None:
    jitter = Jitter(Constant(200), seed=1, period_s=2.0)

    assert jitter.at(4.0) == jitter.at(5.9)
    assert jitter.at(4.0) != jitter.at(6.0)


def test_jitter_leaves_a_resting_rider_at_rest() -> None:
    assert Jitter(Constant(0, 0), seed=5).at(12) == RiderInput(0, 0)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("constant:180w", Constant(180, 90)),
        ("CONSTANT:180.5W", Constant(180.5, 90)),
        ("ramp:100w-300w/600s", Ramp(100, 300, 600, 90)),
        (
            "intervals:150w/60s,280w/30s",
            Intervals((Interval(150, 60, 90), Interval(280, 30, 90))),
        ),
        (
            "intervals: 150w/60s, 280w/30s ",
            Intervals((Interval(150, 60, 90), Interval(280, 30, 90))),
        ),
    ],
)
def test_parse(spec: str, expected: scenario.Scenario) -> None:
    assert scenario.parse(spec, cadence_rpm=90) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "constant",
        "constant:180",
        "ramp:100w-300w",
        "intervals:",
        "intervals:150w/60s,",
        "sprint:9w",
    ],
)
def test_parse_explains_what_it_expects(spec: str) -> None:
    with pytest.raises(ValueError, match="expected constant:180w, ramp:"):
        scenario.parse(spec)
