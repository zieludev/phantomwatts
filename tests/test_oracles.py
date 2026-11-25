"""Cross-checks against parsers written by other people.

pycycling (MIT) talks to real trainers; Bumble (Apache-2.0) ships a Heart Rate profile. Where a
third-party parser disagrees with the specification the comparison is narrowed and says why.
"""
# pyright: basic

from uuid import UUID

import pytest
from bumble import core as bumble_core
from bumble import gatt
from bumble.profiles.heart_rate_service import HeartRateService
from pycycling import cycling_power_service, fitness_machine_service, heart_rate_service
from pycycling.ftms_parsers import (
    FitnessMachineStatus,
    FTMSControlPointOpCode,
    FTMSControlPointResponseResultCode,
    form_ftms_control_command,
    parse_all_features,
    parse_control_point_response,
    parse_fitness_machine_status,
    parse_indoor_bike_data,
)

from phantomwatts.protocol import cps, ftms, hrs, uuids
from phantomwatts.protocol.ftms import SimulationParameters, StopOrPauseAction

BumbleMeasurement = HeartRateService.HeartRateMeasurement


def test_indoor_bike_data_as_a_trainer_sends_it() -> None:
    message = ftms.IndoorBikeData(
        instantaneous_speed_kmh=31.25,
        instantaneous_cadence_rpm=90.5,
        total_distance_m=12345,
        instantaneous_power_w=250,
        elapsed_time_s=600,
    )

    parsed = parse_indoor_bike_data(message.encode())

    assert parsed.instant_speed == 31.25
    assert parsed.instant_cadence == 90.5
    assert parsed.total_distance == 12345
    assert parsed.instant_power == 250
    assert parsed.elapsed_time == 600
    assert parsed.average_speed is None
    assert parsed.resistance_level is None
    assert parsed.heart_rate is None


def test_indoor_bike_data_with_every_field() -> None:
    message = ftms.IndoorBikeData(
        instantaneous_speed_kmh=25.0,
        average_speed_kmh=24.5,
        instantaneous_cadence_rpm=80,
        average_cadence_rpm=79.5,
        total_distance_m=70000,
        resistance_level=-3,
        instantaneous_power_w=-20,
        average_power_w=285,
        expended_energy=ftms.ExpendedEnergy(512, 700, 12),
        heart_rate_bpm=151,
        metabolic_equivalent=8.5,
        elapsed_time_s=3600,
        remaining_time_s=1800,
    )

    assert tuple(parse_indoor_bike_data(message.encode())) == (
        25.0,
        24.5,
        80,
        79.5,
        70000,
        -3,
        -20,
        285,
        512,
        700,
        12,
        151,
        8.5,
        3600,
        1800,
    )


@pytest.mark.parametrize(
    ("opcode", "parameter", "expected"),
    [
        (FTMSControlPointOpCode.REQUEST_CONTROL, 0, ftms.RequestControl()),
        (FTMSControlPointOpCode.RESET, 0, ftms.Reset()),
        (
            FTMSControlPointOpCode.SET_TARGET_RESISTANCE_LEVEL,
            125,
            ftms.SetTargetResistanceLevel(12.5),
        ),
        (FTMSControlPointOpCode.SET_TARGET_POWER, 200, ftms.SetTargetPower(200)),
        (FTMSControlPointOpCode.SET_TARGET_POWER, -10, ftms.SetTargetPower(-10)),
        (FTMSControlPointOpCode.START_OR_RESUME, 0, ftms.StartOrResume()),
        (FTMSControlPointOpCode.STOP_OR_PAUSE, 1, ftms.StopOrPause(StopOrPauseAction.STOP)),
        (FTMSControlPointOpCode.STOP_OR_PAUSE, 2, ftms.StopOrPause(StopOrPauseAction.PAUSE)),
        (
            FTMSControlPointOpCode.SET_INDOOR_BIKE_SIMULATION_PARAMETERS,
            [-1500, 350, 40, 51],
            ftms.SetIndoorBikeSimulationParameters(SimulationParameters(-1.5, 3.5, 0.004, 0.51)),
        ),
    ],
)
def test_requests_as_a_client_library_forms_them(
    opcode: FTMSControlPointOpCode, parameter: int | list[int], expected: ftms.Request
) -> None:
    # pycycling annotates the parameter as int but takes a list for multi-field requests.
    formed = form_ftms_control_command(opcode, parameter)  # pyright: ignore[reportArgumentType]
    assert ftms.decode_request(formed) == expected


@pytest.mark.parametrize("result", list(ftms.ResultCode))
def test_control_point_response(result: ftms.ResultCode) -> None:
    response = ftms.ControlPointResponse(ftms.OpCode.SET_TARGET_POWER, result)

    parsed = parse_control_point_response(bytearray(response.encode()))

    assert parsed.request_code_enum is FTMSControlPointOpCode.SET_TARGET_POWER
    assert parsed.result_code_enum is FTMSControlPointResponseResultCode(result.value)


@pytest.mark.parametrize(
    ("status", "expected", "value"),
    [
        (ftms.MachineReset(), FitnessMachineStatus.RESET, None),
        (
            ftms.StoppedOrPausedByUser(StopOrPauseAction.STOP),
            FitnessMachineStatus.STOPPED_BY_USER,
            None,
        ),
        (
            ftms.StoppedOrPausedByUser(StopOrPauseAction.PAUSE),
            FitnessMachineStatus.PAUSED_BY_USER,
            None,
        ),
        (ftms.StartedOrResumedByUser(), FitnessMachineStatus.STARTED_BY_USER, None),
        (ftms.TargetPowerChanged(-25), FitnessMachineStatus.NEW_POWER, -25),
        (ftms.ControlPermissionLost(), FitnessMachineStatus.CONTROL_PERMISSION_LOST, None),
    ],
)
def test_status(status: ftms.Status, expected: FitnessMachineStatus, value: int | None) -> None:
    parsed = parse_fitness_machine_status(bytearray(status.encode()))

    assert parsed.status is expected
    assert parsed.value == value


def test_simulation_parameters_status() -> None:
    status = ftms.IndoorBikeSimulationParametersChanged(SimulationParameters(2.5, 3.5, 0.004, 0.51))

    parsed = parse_fitness_machine_status(bytearray(status.encode()))

    assert parsed.status is FitnessMachineStatus.NEW_INDOOR_BIKE_SIMULATION_PARAMETERS
    assert parsed.value.wind_speed == 2.5
    assert parsed.value.grade == 3.5
    assert parsed.value.wind_resistance_coefficient == 0.51
    # pycycling divides Crr by 1000 where FTMS Table 4.20 gives a resolution of 0.0001, and it
    # reads wind and grade as unsigned, so this compares the raw byte and uses positive values.
    assert round(parsed.value.coefficient_of_rolling_resistance * 1000) == 40


def test_feature() -> None:
    feature = ftms.Feature(
        machine=ftms.FitnessMachineFeatures.CADENCE
        | ftms.FitnessMachineFeatures.TOTAL_DISTANCE
        | ftms.FitnessMachineFeatures.ELAPSED_TIME
        | ftms.FitnessMachineFeatures.POWER_MEASUREMENT,
        target_settings=ftms.TargetSettingFeatures.RESISTANCE
        | ftms.TargetSettingFeatures.POWER
        | ftms.TargetSettingFeatures.INDOOR_BIKE_SIMULATION,
    )

    machine, target_settings = parse_all_features(bytearray(feature.encode()))

    assert tuple(machine) == tuple(bool(feature.machine >> bit & 1) for bit in range(17))
    assert tuple(target_settings) == tuple(
        bool(feature.target_settings >> bit & 1) for bit in range(17)
    )
    assert machine.power_measurement_supported
    assert target_settings.indoor_bike_simulation_parameters_supported


def test_supported_ranges() -> None:
    power = fitness_machine_service._parse_supported_power_range(
        bytearray(ftms.SupportedPowerRange(0, 2000, 1).encode())
    )
    resistance = fitness_machine_service._parse_supported_resistance_level_range(
        bytearray(ftms.SupportedResistanceLevelRange(0.0, 20.0, 0.1).encode())
    )

    assert tuple(power) == (0, 2000, 1)
    assert tuple(resistance) == (0, 200, 1)


def test_cycling_power_measurement() -> None:
    measurement = cps.CyclingPowerMeasurement(
        250,
        pedal_power_balance_percent=48.5,
        accumulated_torque_nm=100.25,
        wheel_revolutions=cps.WheelRevolutions(100000, 2.5),
        crank_revolutions=cps.CrankRevolutions(1234, 1.5),
        extreme_forces_n=cps.Extremes(500, 20),
        extreme_torques_nm=cps.Extremes(60.5, 1.5),
        extreme_angles_deg=cps.Extremes(270, 90),
        top_dead_spot_angle_deg=10,
        bottom_dead_spot_angle_deg=190,
        accumulated_energy_kj=1500,
    )

    parsed = cycling_power_service._parse_cycling_power_measurement(measurement.encode())

    # pycycling returns raw wire integers and reads them all as unsigned.
    assert parsed.instantaneous_power == 250
    assert parsed.pedal_power_balance == 97
    assert parsed.accumulated_torque == 3208
    assert (parsed.cumulative_wheel_revs, parsed.last_wheel_event_time) == (100000, 5120)
    assert (parsed.cumulative_crank_revs, parsed.last_crank_event_time) == (1234, 1536)
    assert (parsed.maximum_force_magnitude, parsed.minimum_force_magnitude) == (500, 20)
    assert (parsed.maximum_torque_magnitude, parsed.minimum_torque_magnitude) == (1936, 48)
    assert (parsed.top_dead_spot_angle, parsed.bottom_dead_spot_angle) == (10, 190)
    assert parsed.accumulated_energy == 1500


def test_cycling_power_feature_low_bits() -> None:
    feature = (
        cps.CyclingPowerFeature.WHEEL_REVOLUTION_DATA
        | cps.CyclingPowerFeature.CRANK_REVOLUTION_DATA
    )

    parsed = cycling_power_service._parse_cycling_power_feature(feature.encode())

    # Only bits 0-4 are compared: from bit 5 on pycycling's table skips two features that the
    # GATT Specification Supplement lists (extreme angles, offset compensation indicator).
    assert not parsed.pedal_power_balance_supported
    assert not parsed.accumulated_torque_supported
    assert parsed.wheel_rev_supported
    assert parsed.crank_rev_supported
    assert not parsed.extreme_magnitudes_supported


@pytest.mark.parametrize("location", list(cps.SensorLocation))
def test_sensor_location(location: cps.SensorLocation) -> None:
    parsed = cycling_power_service._parse_sensor_location(location.encode())

    assert parsed is not None
    assert parsed.name == location.name.lower()


HEART_RATES = [
    hrs.HeartRateMeasurement(72),
    hrs.HeartRateMeasurement(180, sensor_contact=True),
    hrs.HeartRateMeasurement(64, sensor_contact=False),
    hrs.HeartRateMeasurement(300),
    hrs.HeartRateMeasurement(120, energy_expended_kj=1000, rr_intervals_s=(0.5, 0.75)),
]


@pytest.mark.parametrize("measurement", HEART_RATES)
def test_heart_rate_measurement_against_bumble(measurement: hrs.HeartRateMeasurement) -> None:
    theirs = BumbleMeasurement(
        heart_rate=measurement.heart_rate_bpm,
        sensor_contact_detected=measurement.sensor_contact,
        energy_expended=measurement.energy_expended_kj,
        rr_intervals=measurement.rr_intervals_s or None,
    )

    assert measurement.encode() == bytes(theirs)
    assert BumbleMeasurement.from_bytes(measurement.encode()) == theirs
    assert hrs.HeartRateMeasurement.decode(bytes(theirs)) == measurement


@pytest.mark.parametrize("measurement", HEART_RATES)
def test_heart_rate_measurement_against_pycycling(measurement: hrs.HeartRateMeasurement) -> None:
    parsed = heart_rate_service._parse_hr_measurement(measurement.encode())

    assert parsed.bpm == measurement.heart_rate_bpm
    assert parsed.energy_expended == measurement.energy_expended_kj
    assert parsed.rr_interval == [round(seconds * 1024) for seconds in measurement.rr_intervals_s]


def test_body_sensor_location_against_bumble() -> None:
    assert {location.name: location.value for location in hrs.BodySensorLocation} == {
        location.name: location.value for location in HeartRateService.BodySensorLocation
    }


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (uuids.FITNESS_MACHINE_SERVICE, gatt.GATT_FITNESS_MACHINE_SERVICE),
        (uuids.CYCLING_POWER_SERVICE, gatt.GATT_CYCLING_POWER_SERVICE),
        (uuids.HEART_RATE_SERVICE, gatt.GATT_HEART_RATE_SERVICE),
        (uuids.DEVICE_INFORMATION_SERVICE, gatt.GATT_DEVICE_INFORMATION_SERVICE),
        (uuids.HEART_RATE_MEASUREMENT, gatt.GATT_HEART_RATE_MEASUREMENT_CHARACTERISTIC),
        (uuids.BODY_SENSOR_LOCATION, gatt.GATT_BODY_SENSOR_LOCATION_CHARACTERISTIC),
    ],
)
def test_uuids_against_bumble(ours: int, theirs: bumble_core.UUID) -> None:
    assert bumble_core.UUID.from_16_bits(ours) == theirs


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (
            uuids.FITNESS_MACHINE_FEATURE,
            fitness_machine_service.ftms_fitness_machine_feature_characteristic_id,
        ),
        (uuids.INDOOR_BIKE_DATA, fitness_machine_service.ftms_indoor_bike_data_characteristic_id),
        (
            uuids.SUPPORTED_RESISTANCE_LEVEL_RANGE,
            fitness_machine_service.ftms_supported_resistance_level_range_characteristic_id,
        ),
        (
            uuids.SUPPORTED_POWER_RANGE,
            fitness_machine_service.ftms_supported_power_range_characteristic_id,
        ),
        (
            uuids.FITNESS_MACHINE_CONTROL_POINT,
            fitness_machine_service.ftms_fitness_machine_control_point_characteristic_id,
        ),
        (
            uuids.FITNESS_MACHINE_STATUS,
            fitness_machine_service.ftms_fitness_machine_status_characteristic_id,
        ),
        (uuids.CYCLING_POWER_MEASUREMENT, cycling_power_service.cycling_power_measurement_tx_id),
        (uuids.CYCLING_POWER_FEATURE, cycling_power_service.cycling_power_feature_tx_id),
        (uuids.SENSOR_LOCATION, cycling_power_service.sensor_location_tx_id),
        (
            uuids.HEART_RATE_MEASUREMENT,
            heart_rate_service.heart_rate_measurement_characteristic_id,
        ),
    ],
)
def test_uuids_against_pycycling(ours: int, theirs: str) -> None:
    bluetooth_base = UUID("00000000-0000-1000-8000-00805f9b34fb")
    assert UUID(int=bluetooth_base.int | ours << 96) == UUID(theirs)
