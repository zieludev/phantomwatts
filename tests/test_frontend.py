"""The front end is exercised with bytes in and bytes out, the way an app sees it."""

import pytest

from phantomwatts import frontend
from phantomwatts.core.scenario import Constant
from phantomwatts.core.simulation import ControlMode, TrainerSimulation
from phantomwatts.frontend import CommandRecord, GattFrontEnd, Unrecognized
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


def h(text: str) -> bytes:
    return bytes.fromhex(text)


class ScriptedBackend:
    """A backend that reports fixed telemetry and agrees to everything."""

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry
        self.executed: list[Command] = []
        self.released = 0

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities()

    def execute(self, command: Command) -> Outcome:
        self.executed.append(command)
        return Outcome.SUCCESS

    def release_control(self) -> None:
        self.released += 1

    def telemetry(self) -> Telemetry:
        return self._telemetry


def controlled() -> tuple[GattFrontEnd, TrainerSimulation]:
    simulation = TrainerSimulation(Constant(150, 90))
    front_end = GattFrontEnd(simulation)
    assert front_end.control_point_written(h("00")).indication == h("80 00 01")
    return front_end, simulation


def test_static_characteristic_values() -> None:
    front_end = GattFrontEnd(TrainerSimulation())

    assert frontend.FITNESS_MACHINE_FEATURE.encode() == h("06 50 00 00 0c 20 00 00")
    assert front_end.supported_power_range() == h("00 00 d0 07 01 00")
    assert front_end.supported_resistance_level_range() == h("00 00 c8 00 01 00")
    assert frontend.CYCLING_POWER_FEATURE.encode() == h("08 00 10 00")
    assert frontend.SENSOR_LOCATION.encode() == h("0d")
    assert frontend.BODY_SENSOR_LOCATION.encode() == h("01")


def test_advertisement_fits_the_default_name_next_to_all_three_services() -> None:
    advertisement = frontend.advertisement("PhantomBike")

    assert advertisement.data == h(
        "02 01 06  07 03 26 18 18 18 0d 18  06 16 26 18 01 20 00  0c 09"
    ) + (b"PhantomBike")
    assert advertisement.scan_response == b""


def test_notifications_carry_the_telemetry() -> None:
    backend = ScriptedBackend(
        Telemetry(
            power_w=249.6,
            cadence_rpm=90.2,
            speed_mps=31.25 / 3.6,
            heart_rate_bpm=142.4,
            elapsed_s=61.9,
            distance_m=512.7,
            crank_revolutions=70000,
            last_crank_event_s=70.5,
        )
    )

    notifications = GattFrontEnd(backend).notifications()

    assert notifications.indoor_bike_data == h("54 08 35 0c b4 00 00 02 00 fa 00 3d 00")
    # 70000 turns and 70.5 s have both wrapped: 70000 - 65536 = 4464, 70.5 s - 64 s = 6.5 s.
    assert notifications.cycling_power_measurement == h("20 00 fa 00 70 11 00 1a")
    assert notifications.heart_rate_measurement == h("00 8e")


def test_notifications_saturate_instead_of_failing() -> None:
    backend = ScriptedBackend(
        Telemetry(
            power_w=1e6,
            cadence_rpm=1e6,
            speed_mps=1e6,
            heart_rate_bpm=400,
            elapsed_s=1e6,
            distance_m=1e9,
            crank_revolutions=10**9,
            last_crank_event_s=1e6,
        )
    )

    notifications = GattFrontEnd(backend).notifications()

    assert notifications.indoor_bike_data == h("54 08 ff ff ff ff ff ff ff ff 7f ff ff")
    assert notifications.cycling_power_measurement[:4] == h("20 00 ff 7f")
    assert notifications.heart_rate_measurement == h("00 ff")


def test_notifications_fit_the_default_att_mtu() -> None:
    notifications = GattFrontEnd(TrainerSimulation()).notifications()

    assert len(notifications.indoor_bike_data) <= 20
    assert len(notifications.cycling_power_measurement) <= 20


EXCHANGES = [
    pytest.param("05 c8 00", "80 05 01", "08 c8 00", id="target-power"),
    pytest.param("05 00 00", "80 05 01", "08 00 00", id="target-power-zero"),
    pytest.param("05 ff 7f", "80 05 03", None, id="target-power-out-of-range"),
    pytest.param("05 ff ff", "80 05 03", None, id="target-power-negative"),
    pytest.param("05 c8", "80 05 03", None, id="target-power-malformed"),
    pytest.param("04 32", "80 04 01", "07 32", id="resistance"),
    pytest.param("04 ff", "80 04 03", None, id="resistance-out-of-range"),
    pytest.param("11 00 00 5e 01 28 33", "80 11 01", "12 00 00 5e 01 28 33", id="simulation"),
    pytest.param(
        "11 24 fa 1f ff 21 3c", "80 11 01", "12 24 fa 1f ff 21 3c", id="simulation-signed"
    ),
    pytest.param("11 00 00 5e 01 28", "80 11 03", None, id="simulation-malformed"),
    pytest.param("07", "80 07 01", "04", id="start"),
    pytest.param("08 01", "80 08 04", None, id="stop-when-already-stopped"),
    pytest.param("08 03", "80 08 03", None, id="stop-or-pause-reserved-value"),
    pytest.param("01", "80 01 01", "01", id="reset"),
    pytest.param("00", "80 00 01", None, id="request-control-again"),
    pytest.param("02 10 27", "80 02 02", None, id="target-speed-is-for-treadmills"),
    pytest.param("12 08 52", "80 12 02", None, id="wheel-circumference-not-supported"),
    pytest.param("13 01", "80 13 02", None, id="spin-down-not-supported"),
    pytest.param("7f", "80 7f 02", None, id="reserved-op-code"),
    pytest.param("80 05 01", "80 80 02", None, id="response-code-is-not-a-request"),
]


@pytest.mark.parametrize(("write", "indication", "status"), EXCHANGES)
def test_control_point_exchange(write: str, indication: str, status: str | None) -> None:
    front_end, _ = controlled()

    reply = front_end.control_point_written(h(write))

    assert reply.indication == h(indication)
    assert reply.status == (None if status is None else h(status))


@pytest.mark.parametrize(
    "write", ["01", "04 32", "05 c8 00", "07", "08 01", "08 02", "11 00 00 5e 01 28 33"]
)
def test_control_must_be_requested_first(write: str) -> None:
    front_end = GattFrontEnd(TrainerSimulation())

    reply = front_end.control_point_written(h(write))

    assert reply.indication == bytes([0x80, h(write)[0], 0x05])
    assert reply.status is None


def test_an_unsupported_op_code_is_refused_even_without_control() -> None:
    front_end = GattFrontEnd(TrainerSimulation())

    assert front_end.control_point_written(h("13 01")).indication == h("80 13 02")


def test_a_training_session_from_the_app_side() -> None:
    front_end, _ = controlled()

    exchanges = [
        (reply.indication.hex(" "), None if reply.status is None else reply.status.hex(" "))
        for reply in (
            front_end.control_point_written(h(write))
            for write in ("07", "07", "08 02", "08 02", "07", "08 01")
        )
    ]

    assert exchanges == [
        ("80 07 01", "04"),
        ("80 07 04", None),
        ("80 08 01", "02 02"),
        ("80 08 04", None),
        ("80 07 01", "04"),
        ("80 08 01", "02 01"),
    ]


def test_reset_ends_the_control_permission() -> None:
    front_end, _ = controlled()

    assert front_end.control_point_written(h("01")).indication == h("80 01 01")
    assert front_end.control_point_written(h("05 c8 00")).indication == h("80 05 05")


def test_commands_reach_the_trainer() -> None:
    front_end, simulation = controlled()

    front_end.control_point_written(h("11 00 00 5e 01 28 33"))

    assert simulation.mode is ControlMode.SIMULATION
    assert simulation.road.grade_percent == 3.5


def test_the_log_shows_what_the_app_asked_for_and_what_came_of_it() -> None:
    seen: list[CommandRecord] = []
    simulation = TrainerSimulation()
    front_end = GattFrontEnd(simulation, on_command=seen.append)

    for write in (
        "05 c8 00",
        "00",
        "05 c8 00",
        "04 32",
        "11 00 00 5e 01 28 33",
        "07",
        "08 02",
        "08 01",
        "13 01",
        "05 c8",
        "01",
    ):
        front_end.control_point_written(h(write))

    assert front_end.commands == [
        CommandRecord(SetTargetPower(200), Outcome.CONTROL_NOT_PERMITTED),
        CommandRecord(RequestControl(), Outcome.SUCCESS),
        CommandRecord(SetTargetPower(200), Outcome.SUCCESS),
        CommandRecord(SetTargetResistance(5.0), Outcome.SUCCESS),
        CommandRecord(SetSimulation(3.5, 0.0, 0.004, 0.51), Outcome.SUCCESS),
        CommandRecord(Start(), Outcome.SUCCESS),
        CommandRecord(Pause(), Outcome.SUCCESS),
        CommandRecord(Stop(), Outcome.SUCCESS),
        CommandRecord(Unrecognized(0x13, h("01")), Outcome.NOT_SUPPORTED),
        CommandRecord(Unrecognized(0x05, h("c8")), Outcome.INVALID_PARAMETER),
        CommandRecord(Reset(), Outcome.SUCCESS),
    ]
    assert seen == front_end.commands


def test_what_the_trainer_does_not_recognise_never_reaches_the_backend() -> None:
    backend = ScriptedBackend(TrainerSimulation().telemetry())
    front_end = GattFrontEnd(backend)

    front_end.control_point_written(h("13 01"))
    front_end.control_point_written(h("05 c8"))
    front_end.control_point_written(h("05 c8 00"))

    assert backend.executed == [SetTargetPower(200)]


def test_an_empty_write_has_no_op_code_to_answer() -> None:
    front_end = GattFrontEnd(TrainerSimulation())

    with pytest.raises(ValueError, match="empty"):
        front_end.control_point_written(b"")
    assert front_end.commands == []


def test_a_departing_client_gives_up_control() -> None:
    front_end, simulation = controlled()
    front_end.control_point_written(h("05 c8 00"))

    front_end.client_gone()

    assert not simulation.has_control
    assert front_end.control_point_written(h("05 c8 00")).indication == h("80 05 05")
