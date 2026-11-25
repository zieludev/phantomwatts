import math

import pytest

from phantomwatts.core.physics import Road, steady_speed
from phantomwatts.core.scenario import Constant, Jitter
from phantomwatts.core.simulation import ControlMode, Rider, Session, TrainerSimulation
from phantomwatts.port import (
    Capabilities,
    Command,
    Outcome,
    Pause,
    Range,
    RequestControl,
    Reset,
    SetSimulation,
    SetTargetPower,
    SetTargetResistance,
    Start,
    Stop,
)


def ride(simulation: TrainerSimulation, seconds: float, dt: float = 1.0) -> None:
    for _ in range(round(seconds / dt)):
        simulation.step(dt)


def controlled(scenario: Constant | None = None) -> TrainerSimulation:
    simulation = TrainerSimulation(scenario or Constant(150, 90))
    assert simulation.execute(RequestControl()) is Outcome.SUCCESS
    return simulation


def test_free_ride_reports_what_the_rider_does() -> None:
    simulation = TrainerSimulation(Constant(180, 90))

    ride(simulation, 60)
    telemetry = simulation.telemetry()

    assert simulation.mode is ControlMode.FREE_RIDE
    assert telemetry.power_w == 180
    assert telemetry.cadence_rpm == 90
    assert telemetry.speed_mps == pytest.approx(steady_speed(180, Road(), 84.0), abs=1e-6)


def test_cranks_are_counted_with_the_time_of_the_last_turn() -> None:
    simulation = TrainerSimulation(Constant(180, 90))

    ride(simulation, 10)

    assert simulation.telemetry().crank_revolutions == 15
    assert simulation.telemetry().last_crank_event_s == 10.0


def test_the_last_crank_event_lies_between_steps() -> None:
    simulation = TrainerSimulation(Constant(180, 80))

    simulation.step(1.0)

    assert simulation.telemetry().crank_revolutions == 1
    assert simulation.telemetry().last_crank_event_s == pytest.approx(0.75)


@pytest.mark.parametrize("dt", [0.25, 0.5, 2.0])
def test_the_ride_does_not_depend_on_the_step_size(dt: float) -> None:
    coarse, fine = controlled(), controlled()
    for simulation in (coarse, fine):
        simulation.execute(SetTargetPower(260))
        simulation.execute(Start())

    ride(coarse, 20, dt=1.0)
    ride(fine, 20, dt=dt)

    assert fine.telemetry().power_w == pytest.approx(coarse.telemetry().power_w)
    assert fine.telemetry().crank_revolutions == coarse.telemetry().crank_revolutions
    assert fine.telemetry().elapsed_s == pytest.approx(coarse.telemetry().elapsed_s)
    # Speed and heart rate chase a power that is itself moving, so they only agree roughly.
    assert fine.telemetry().speed_mps == pytest.approx(coarse.telemetry().speed_mps, rel=0.05)
    assert fine.telemetry().heart_rate_bpm == pytest.approx(
        coarse.telemetry().heart_rate_bpm, abs=2
    )


@pytest.mark.parametrize(
    "command",
    [
        Reset(),
        SetTargetPower(200),
        SetTargetResistance(5),
        SetSimulation(2.0),
        Start(),
        Stop(),
        Pause(),
    ],
)
def test_nothing_is_permitted_before_control_is_requested(command: Command) -> None:
    simulation = TrainerSimulation()

    assert simulation.execute(command) is Outcome.CONTROL_NOT_PERMITTED
    assert simulation.mode is ControlMode.FREE_RIDE
    assert simulation.session is Session.STOPPED


def test_requesting_control_twice_is_fine() -> None:
    simulation = controlled()

    assert simulation.execute(RequestControl()) is Outcome.SUCCESS
    assert simulation.has_control


def test_erg_pulls_the_power_to_the_target() -> None:
    simulation = controlled(Constant(150, 90))
    ride(simulation, 5)

    assert simulation.execute(SetTargetPower(250)) is Outcome.SUCCESS
    assert simulation.mode is ControlMode.ERG
    assert simulation.target_power_w == 250

    seen = [simulation.step(1.0).power_w for _ in range(10)]

    assert seen == sorted(seen)
    assert seen[1] == pytest.approx(250 - 100 * math.exp(-1))
    assert seen[-1] == pytest.approx(250, abs=1.0)


def test_erg_time_constant_is_configurable() -> None:
    simulation = TrainerSimulation(Constant(150, 90), erg_time_constant_s=0.5)
    simulation.execute(RequestControl())
    simulation.step(1.0)
    simulation.execute(SetTargetPower(250))

    assert simulation.step(0.5).power_w == pytest.approx(250 - 100 * math.exp(-1))


def test_erg_cannot_make_power_when_nobody_pedals() -> None:
    simulation = controlled(Constant(150, 0))
    simulation.execute(SetTargetPower(250))

    ride(simulation, 30)

    assert simulation.telemetry().power_w == 0
    assert simulation.telemetry().cadence_rpm == 0
    assert simulation.telemetry().crank_revolutions == 0


@pytest.mark.parametrize("watts", [-1, 2001, 32767])
def test_a_power_target_outside_the_supported_range_is_refused(watts: int) -> None:
    simulation = controlled()
    simulation.execute(SetTargetPower(200))

    assert simulation.execute(SetTargetPower(watts)) is Outcome.INVALID_PARAMETER
    assert simulation.target_power_w == 200


def test_the_supported_range_is_a_capability() -> None:
    simulation = TrainerSimulation(capabilities=Capabilities(power_w=Range(50, 400, 5)))
    simulation.execute(RequestControl())

    assert simulation.execute(SetTargetPower(450)) is Outcome.INVALID_PARAMETER
    assert simulation.execute(SetTargetPower(400)) is Outcome.SUCCESS


def test_simulation_mode_slows_the_rider_on_a_climb() -> None:
    flat, climb = controlled(), controlled()

    assert climb.execute(SetSimulation(6.0, wind_speed_mps=2.0)) is Outcome.SUCCESS
    ride(flat, 60)
    ride(climb, 60)

    assert climb.mode is ControlMode.SIMULATION
    assert climb.road == Road(6.0, 2.0, 0.004, 0.51)
    assert climb.telemetry().power_w == flat.telemetry().power_w == 150
    assert climb.telemetry().speed_mps < flat.telemetry().speed_mps / 2


def test_resistance_mode_acts_like_a_hill() -> None:
    easy, hard = controlled(), controlled()

    assert easy.execute(SetTargetResistance(1.0)) is Outcome.SUCCESS
    assert hard.execute(SetTargetResistance(10.0)) is Outcome.SUCCESS
    ride(easy, 60)
    ride(hard, 60)

    assert hard.mode is ControlMode.RESISTANCE
    assert hard.resistance_level == 10.0
    assert hard.telemetry().speed_mps < easy.telemetry().speed_mps


@pytest.mark.parametrize("level", [-0.1, 20.1])
def test_a_resistance_level_outside_the_supported_range_is_refused(level: float) -> None:
    simulation = controlled()

    assert simulation.execute(SetTargetResistance(level)) is Outcome.INVALID_PARAMETER
    assert simulation.mode is ControlMode.FREE_RIDE


def test_a_new_mode_replaces_the_old_one() -> None:
    simulation = controlled()
    simulation.execute(SetTargetPower(250))

    simulation.execute(SetSimulation(4.0))

    assert simulation.mode is ControlMode.SIMULATION
    assert simulation.target_power_w is None


def test_session_counters_only_run_while_started() -> None:
    simulation = controlled()

    ride(simulation, 30)
    assert simulation.telemetry().elapsed_s == 0
    assert simulation.telemetry().distance_m == 0
    assert simulation.telemetry().speed_mps > 0

    assert simulation.execute(Start()) is Outcome.SUCCESS
    ride(simulation, 30)
    assert simulation.telemetry().elapsed_s == 30
    distance = simulation.telemetry().distance_m
    assert distance == pytest.approx(30 * simulation.telemetry().speed_mps, rel=0.01)

    assert simulation.execute(Pause()) is Outcome.SUCCESS
    ride(simulation, 30)
    assert simulation.telemetry().elapsed_s == 30
    assert simulation.telemetry().distance_m == distance

    assert simulation.execute(Start()) is Outcome.SUCCESS
    ride(simulation, 10)
    assert simulation.telemetry().elapsed_s == 40

    assert simulation.execute(Stop()) is Outcome.SUCCESS
    ride(simulation, 10)
    assert simulation.telemetry().elapsed_s == 40


@pytest.mark.parametrize(
    ("before", "command"),
    [
        pytest.param([], Stop(), id="stop-when-stopped"),
        pytest.param([], Pause(), id="pause-when-stopped"),
        pytest.param([Start()], Start(), id="start-when-started"),
        pytest.param([Start(), Pause()], Pause(), id="pause-when-paused"),
    ],
)
def test_a_session_change_that_changes_nothing_fails(
    before: list[Command], command: Command
) -> None:
    simulation = controlled()
    for earlier in before:
        assert simulation.execute(earlier) is Outcome.SUCCESS
    session = simulation.session

    assert simulation.execute(command) is Outcome.FAILED
    assert simulation.session is session


def test_reset_restores_defaults_and_ends_the_control_permission() -> None:
    simulation = controlled()
    simulation.execute(SetTargetPower(250))
    simulation.execute(Start())
    ride(simulation, 20)

    assert simulation.execute(Reset()) is Outcome.SUCCESS

    assert simulation.mode is ControlMode.FREE_RIDE
    assert simulation.session is Session.STOPPED
    assert simulation.target_power_w is None
    assert simulation.telemetry().elapsed_s == 0
    assert simulation.telemetry().distance_m == 0
    assert not simulation.has_control
    assert simulation.execute(SetTargetPower(250)) is Outcome.CONTROL_NOT_PERMITTED


def test_losing_the_app_ends_control_and_frees_the_trainer() -> None:
    simulation = controlled()
    simulation.execute(SetSimulation(8.0))

    simulation.release_control()

    assert not simulation.has_control
    assert simulation.mode is ControlMode.FREE_RIDE
    assert simulation.road == Road()


def test_heart_rate_drifts_towards_the_effort() -> None:
    rider = Rider()
    simulation = TrainerSimulation(Constant(250, 90), rider)

    early = simulation.step(10).heart_rate_bpm
    ride(simulation, 600)
    settled = simulation.telemetry().heart_rate_bpm

    assert rider.resting_heart_rate_bpm < early < settled
    assert settled == pytest.approx(rider.heart_rate_at(250), abs=0.1)
    assert settled == pytest.approx(160)


def test_time_only_moves_forward() -> None:
    simulation = TrainerSimulation()
    before = simulation.telemetry()

    assert simulation.step(0) == before
    with pytest.raises(ValueError, match="backwards"):
        simulation.step(-1)


def test_a_jittered_ride_is_reproducible() -> None:
    def powers(seed: int) -> list[float]:
        simulation = TrainerSimulation(Jitter(Constant(200), seed=seed))
        return [simulation.step(1.0).power_w for _ in range(60)]

    assert powers(11) == powers(11)
    assert powers(11) != powers(12)
