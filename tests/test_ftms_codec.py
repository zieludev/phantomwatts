"""Expected bytes are worked out by hand from the tables of the FTMS 1.0 specification."""

import pytest

from phantomwatts.protocol import ftms
from phantomwatts.protocol.ftms import (
    FitnessMachineFeatures,
    IndoorBikeData,
    ResultCode,
    SimulationParameters,
    StopOrPauseAction,
    TargetSettingFeatures,
)


def h(text: str) -> bytes:
    return bytes.fromhex(text)


INDOOR_BIKE_DATA = [
    pytest.param(
        IndoorBikeData(
            instantaneous_speed_kmh=31.25, instantaneous_cadence_rpm=90.5, instantaneous_power_w=250
        ),
        "44 00 35 0c b5 00 fa 00",
        id="speed-cadence-power",
    ),
    pytest.param(
        IndoorBikeData(
            instantaneous_speed_kmh=25.0,
            average_speed_kmh=24.5,
            instantaneous_cadence_rpm=80,
            average_cadence_rpm=79.5,
            total_distance_m=70000,
            resistance_level=-3,
            instantaneous_power_w=300,
            average_power_w=285,
            expended_energy=ftms.ExpendedEnergy(512, 700, 12),
            heart_rate_bpm=151,
            metabolic_equivalent=8.5,
            elapsed_time_s=3600,
            remaining_time_s=1800,
        ),
        "fe 1f c4 09 92 09 a0 00 9f 00 70 11 01 fd ff 2c 01 1d 01 00 02 bc 02 0c 97 55 10 0e 08 07",
        id="every-field",
    ),
    pytest.param(IndoorBikeData(elapsed_time_s=10), "01 08 0a 00", id="more-data-without-speed"),
    pytest.param(
        IndoorBikeData(
            instantaneous_speed_kmh=0, expended_energy=ftms.ExpendedEnergy(None, None, None)
        ),
        "00 01 00 00 ff ff ff ff ff",
        id="energy-not-available",
    ),
    pytest.param(
        IndoorBikeData(instantaneous_speed_kmh=0, instantaneous_power_w=-20),
        "40 00 00 00 ec ff",
        id="negative-power",
    ),
    pytest.param(
        IndoorBikeData(instantaneous_speed_kmh=655.35, total_distance_m=0xFFFFFF),
        "10 00 ff ff ff ff ff",
        id="field-maxima",
    ),
]


@pytest.mark.parametrize(("message", "wire"), INDOOR_BIKE_DATA)
def test_indoor_bike_data(message: IndoorBikeData, wire: str) -> None:
    assert message.encode() == h(wire)
    assert IndoorBikeData.decode(h(wire)) == message


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param("", id="empty"),
        pytest.param("44", id="half-the-flags"),
        pytest.param("44 00 35 0c b5 00 fa", id="truncated-power"),
        pytest.param("44 00 35 0c b5 00 fa 00 00", id="trailing-byte"),
    ],
)
def test_indoor_bike_data_rejects_malformed_input(wire: str) -> None:
    with pytest.raises(ValueError, match=r"truncated|trailing"):
        IndoorBikeData.decode(h(wire))


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(IndoorBikeData(instantaneous_speed_kmh=655.36), id="speed-above-uint16"),
        pytest.param(IndoorBikeData(instantaneous_speed_kmh=-0.01), id="negative-speed"),
        pytest.param(IndoorBikeData(instantaneous_power_w=32768), id="power-above-sint16"),
        pytest.param(IndoorBikeData(heart_rate_bpm=256), id="heart-rate-above-uint8"),
        pytest.param(
            IndoorBikeData(expended_energy=ftms.ExpendedEnergy(0xFFFF, 1, 1)),
            id="energy-equal-to-the-not-available-marker",
        ),
    ],
)
def test_indoor_bike_data_refuses_values_the_wire_cannot_carry(message: IndoorBikeData) -> None:
    with pytest.raises(ValueError, match=r"does not fit|Not Available"):
        message.encode()


def test_feature() -> None:
    feature = ftms.Feature(
        machine=FitnessMachineFeatures.CADENCE
        | FitnessMachineFeatures.TOTAL_DISTANCE
        | FitnessMachineFeatures.ELAPSED_TIME
        | FitnessMachineFeatures.POWER_MEASUREMENT,
        target_settings=TargetSettingFeatures.RESISTANCE
        | TargetSettingFeatures.POWER
        | TargetSettingFeatures.INDOOR_BIKE_SIMULATION,
    )
    wire = h("06 50 00 00 0c 20 00 00")
    assert feature.encode() == wire
    assert ftms.Feature.decode(wire) == feature


@pytest.mark.parametrize(
    ("flag", "bit"),
    [
        (FitnessMachineFeatures.AVERAGE_SPEED, 0),
        (FitnessMachineFeatures.RESISTANCE_LEVEL, 7),
        (FitnessMachineFeatures.HEART_RATE_MEASUREMENT, 10),
        (FitnessMachineFeatures.POWER_MEASUREMENT, 14),
        (FitnessMachineFeatures.USER_DATA_RETENTION, 16),
        (TargetSettingFeatures.SPEED, 0),
        (TargetSettingFeatures.POWER, 3),
        (TargetSettingFeatures.INDOOR_BIKE_SIMULATION, 13),
        (TargetSettingFeatures.SPIN_DOWN_CONTROL, 15),
        (TargetSettingFeatures.TARGETED_CADENCE, 16),
    ],
)
def test_feature_bit_positions(flag: int, bit: int) -> None:
    assert flag == 1 << bit


def test_feature_keeps_reserved_bits_it_does_not_know() -> None:
    wire = h("00 00 00 80 00 00 00 80")
    assert ftms.Feature.decode(wire).encode() == wire


def test_service_data() -> None:
    service_data = ftms.ServiceData(available=True, machine_types=ftms.MachineType.INDOOR_BIKE)
    assert service_data.encode() == h("01 20 00")
    assert ftms.ServiceData.decode(h("01 20 00")) == service_data


@pytest.mark.parametrize(
    ("power_range", "wire"),
    [
        (ftms.SupportedPowerRange(0, 2000, 1), "00 00 d0 07 01 00"),
        (ftms.SupportedPowerRange(-100, 1500, 5), "9c ff dc 05 05 00"),
    ],
)
def test_supported_power_range(power_range: ftms.SupportedPowerRange, wire: str) -> None:
    assert power_range.encode() == h(wire)
    assert ftms.SupportedPowerRange.decode(h(wire)) == power_range


@pytest.mark.parametrize(
    ("resistance_range", "wire"),
    [
        (ftms.SupportedResistanceLevelRange(0.0, 20.0, 0.1), "00 00 c8 00 01 00"),
        (ftms.SupportedResistanceLevelRange(-5.0, 100.0, 0.5), "ce ff e8 03 05 00"),
    ],
)
def test_supported_resistance_level_range(
    resistance_range: ftms.SupportedResistanceLevelRange, wire: str
) -> None:
    assert resistance_range.encode() == h(wire)
    assert ftms.SupportedResistanceLevelRange.decode(h(wire)) == resistance_range


REQUESTS = [
    pytest.param(ftms.RequestControl(), "00", id="request-control"),
    pytest.param(ftms.Reset(), "01", id="reset"),
    pytest.param(ftms.SetTargetResistanceLevel(12.5), "04 7d", id="resistance"),
    pytest.param(ftms.SetTargetResistanceLevel(25.5), "04 ff", id="resistance-maximum"),
    pytest.param(ftms.SetTargetPower(200), "05 c8 00", id="power"),
    pytest.param(ftms.SetTargetPower(-10), "05 f6 ff", id="negative-power"),
    pytest.param(ftms.StartOrResume(), "07", id="start"),
    pytest.param(ftms.StopOrPause(StopOrPauseAction.STOP), "08 01", id="stop"),
    pytest.param(ftms.StopOrPause(StopOrPauseAction.PAUSE), "08 02", id="pause"),
    pytest.param(
        ftms.SetIndoorBikeSimulationParameters(SimulationParameters(-1.5, 3.5, 0.004, 0.51)),
        "11 24 fa 5e 01 28 33",
        id="simulation-uphill-tailwind",
    ),
    pytest.param(
        ftms.SetIndoorBikeSimulationParameters(SimulationParameters(0.0, -2.25, 0.0033, 0.6)),
        "11 00 00 1f ff 21 3c",
        id="simulation-downhill",
    ),
    pytest.param(ftms.UnmodelledRequest(0x12, h("08 52")), "12 08 52", id="wheel-circumference"),
    pytest.param(ftms.UnmodelledRequest(0x7F), "7f", id="reserved-op-code"),
]


@pytest.mark.parametrize(("request_", "wire"), REQUESTS)
def test_control_point_request(request_: ftms.Request, wire: str) -> None:
    assert request_.encode() == h(wire)
    assert ftms.decode_request(h(wire)) == request_


@pytest.mark.parametrize(
    ("wire", "opcode"),
    [
        pytest.param("00 01", 0x00, id="parameter-on-request-control"),
        pytest.param("04", 0x04, id="resistance-without-level"),
        pytest.param("05 c8", 0x05, id="power-one-byte-short"),
        pytest.param("05 c8 00 00", 0x05, id="power-one-byte-long"),
        pytest.param("08", 0x08, id="stop-or-pause-without-action"),
        pytest.param("08 00", 0x08, id="stop-or-pause-reserved-zero"),
        pytest.param("08 03", 0x08, id="stop-or-pause-reserved-three"),
        pytest.param("11 00 00 5e 01 28", 0x11, id="simulation-one-byte-short"),
    ],
)
def test_malformed_request_names_the_op_code(wire: str, opcode: int) -> None:
    with pytest.raises(ftms.InvalidParameterError) as error:
        ftms.decode_request(h(wire))
    assert error.value.opcode == opcode


def test_an_empty_request_is_not_a_request() -> None:
    with pytest.raises(ValueError, match="empty") as error:
        ftms.decode_request(b"")
    assert not isinstance(error.value, ftms.InvalidParameterError)


@pytest.mark.parametrize(
    ("response", "wire"),
    [
        (ftms.ControlPointResponse(0x05, ResultCode.SUCCESS), "80 05 01"),
        (ftms.ControlPointResponse(0x13, ResultCode.OP_CODE_NOT_SUPPORTED), "80 13 02"),
        (ftms.ControlPointResponse(0x11, ResultCode.INVALID_PARAMETER), "80 11 03"),
        (ftms.ControlPointResponse(0x07, ResultCode.OPERATION_FAILED), "80 07 04"),
        (ftms.ControlPointResponse(0x05, ResultCode.CONTROL_NOT_PERMITTED), "80 05 05"),
        (
            ftms.ControlPointResponse(0x13, ResultCode.SUCCESS, h("e8 03 d0 07")),
            "80 13 01 e8 03 d0 07",
        ),
    ],
)
def test_control_point_response(response: ftms.ControlPointResponse, wire: str) -> None:
    assert response.encode() == h(wire)
    assert ftms.ControlPointResponse.decode(h(wire)) == response


@pytest.mark.parametrize("wire", ["", "80", "80 05", "05 80 01", "80 05 00", "80 05 06"])
def test_control_point_response_rejects_malformed_input(wire: str) -> None:
    with pytest.raises(ValueError, match=r"truncated|not a Response Code|ResultCode"):
        ftms.ControlPointResponse.decode(h(wire))


STATUSES = [
    pytest.param(ftms.MachineReset(), "01", id="reset"),
    pytest.param(ftms.StoppedOrPausedByUser(StopOrPauseAction.STOP), "02 01", id="stopped"),
    pytest.param(ftms.StoppedOrPausedByUser(StopOrPauseAction.PAUSE), "02 02", id="paused"),
    pytest.param(ftms.StartedOrResumedByUser(), "04", id="started"),
    pytest.param(ftms.TargetResistanceLevelChanged(7.5), "07 4b", id="resistance"),
    pytest.param(ftms.TargetPowerChanged(250), "08 fa 00", id="power"),
    pytest.param(
        ftms.IndoorBikeSimulationParametersChanged(SimulationParameters(0.0, 1.0, 0.004, 0.51)),
        "12 00 00 64 00 28 33",
        id="simulation",
    ),
    pytest.param(ftms.ControlPermissionLost(), "ff", id="control-permission-lost"),
    pytest.param(ftms.UnmodelledStatus(0x14, h("02")), "14 02", id="spin-down-status"),
]


@pytest.mark.parametrize(("status", "wire"), STATUSES)
def test_status(status: ftms.Status, wire: str) -> None:
    assert status.encode() == h(wire)
    assert ftms.decode_status(h(wire)) == status


@pytest.mark.parametrize("wire", ["", "08", "08 fa", "08 fa 00 00", "02 07", "01 00"])
def test_status_rejects_malformed_input(wire: str) -> None:
    with pytest.raises(ValueError, match=r"empty|truncated|trailing|StopOrPauseAction"):
        ftms.decode_status(h(wire))


@pytest.mark.parametrize(
    ("code", "value"),
    [
        (ftms.OpCode.REQUEST_CONTROL, 0x00),
        (ftms.OpCode.SET_TARGET_POWER, 0x05),
        (ftms.OpCode.SET_INDOOR_BIKE_SIMULATION_PARAMETERS, 0x11),
        (ftms.OpCode.SET_TARGETED_CADENCE, 0x14),
        (ftms.OpCode.RESPONSE_CODE, 0x80),
        (ftms.StatusOpCode.TARGET_POWER_CHANGED, 0x08),
        (ftms.StatusOpCode.INDOOR_BIKE_SIMULATION_PARAMETERS_CHANGED, 0x12),
        (ftms.StatusOpCode.TARGETED_CADENCE_CHANGED, 0x15),
        (ftms.StatusOpCode.CONTROL_PERMISSION_LOST, 0xFF),
        (ResultCode.SUCCESS, 0x01),
        (ResultCode.CONTROL_NOT_PERMITTED, 0x05),
    ],
)
def test_assigned_values(code: int, value: int) -> None:
    assert code == value


def test_values_on_the_wire_grid_survive_a_round_trip_exactly() -> None:
    for raw in range(-32768, 32768, 257):
        parameters = SimulationParameters(raw / 1000, raw / 100, (raw % 256) / 10000, 0.07)
        request = ftms.SetIndoorBikeSimulationParameters(parameters)
        assert ftms.decode_request(request.encode()) == request
