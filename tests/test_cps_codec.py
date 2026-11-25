"""Expected bytes are worked out by hand from the GATT Specification Supplement tables."""

import pytest

from phantomwatts.protocol.cps import (
    CrankRevolutions,
    CyclingPowerFeature,
    CyclingPowerMeasurement,
    Extremes,
    SensorLocation,
    WheelRevolutions,
)


def h(text: str) -> bytes:
    return bytes.fromhex(text)


MEASUREMENTS = [
    pytest.param(CyclingPowerMeasurement(180), "00 00 b4 00", id="power-only"),
    pytest.param(
        CyclingPowerMeasurement(250, crank_revolutions=CrankRevolutions(1234, 1.5)),
        "20 00 fa 00 d2 04 00 06",
        id="power-and-cranks",
    ),
    pytest.param(
        CyclingPowerMeasurement(
            300,
            wheel_revolutions=WheelRevolutions(100000, 2.5),
            crank_revolutions=CrankRevolutions(65535, 65535 / 1024),
        ),
        "30 00 2c 01 a0 86 01 00 00 14 ff ff ff ff",
        id="wheel-and-cranks-at-the-rollover",
    ),
    pytest.param(
        CyclingPowerMeasurement(
            -12,
            pedal_power_balance_percent=48.5,
            pedal_power_balance_reference_left=True,
            accumulated_torque_nm=100.25,
            accumulated_torque_crank_based=True,
            wheel_revolutions=WheelRevolutions(1, 0.5),
            crank_revolutions=CrankRevolutions(2, 0.25),
            extreme_forces_n=Extremes(500, -20),
            extreme_torques_nm=Extremes(60.5, -1.5),
            # The specification's own example: maximum 0xABC, minimum 0x123, sent as 0x123ABC.
            extreme_angles_deg=Extremes(0xABC, 0x123),
            top_dead_spot_angle_deg=10,
            bottom_dead_spot_angle_deg=190,
            accumulated_energy_kj=1500,
            offset_compensation_indicator=True,
        ),
        "ff 1f f4 ff 61 88 0c 01 00 00 00 00 04 02 00 00 01 "
        "f4 01 ec ff 90 07 d0 ff bc 3a 12 0a 00 be 00 dc 05",
        id="every-field",
    ),
    pytest.param(
        CyclingPowerMeasurement(0, accumulated_torque_nm=1.0),
        "04 00 00 00 20 00",
        id="wheel-based-torque",
    ),
]


@pytest.mark.parametrize(("measurement", "wire"), MEASUREMENTS)
def test_cycling_power_measurement(measurement: CyclingPowerMeasurement, wire: str) -> None:
    assert measurement.encode() == h(wire)
    assert CyclingPowerMeasurement.decode(h(wire)) == measurement


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param("", id="empty"),
        pytest.param("00 00 b4", id="truncated-power"),
        pytest.param("20 00 fa 00 d2 04 00", id="truncated-crank-data"),
        pytest.param("00 00 b4 00 00", id="trailing-byte"),
    ],
)
def test_cycling_power_measurement_rejects_malformed_input(wire: str) -> None:
    with pytest.raises(ValueError, match=r"truncated|trailing"):
        CyclingPowerMeasurement.decode(h(wire))


@pytest.mark.parametrize(
    "measurement",
    [
        pytest.param(CyclingPowerMeasurement(40000), id="power-above-sint16"),
        pytest.param(
            CyclingPowerMeasurement(0, crank_revolutions=CrankRevolutions(65536, 0)),
            id="crank-count-not-wrapped",
        ),
        pytest.param(
            CyclingPowerMeasurement(0, crank_revolutions=CrankRevolutions(0, 64.0)),
            id="crank-time-not-wrapped",
        ),
        pytest.param(
            CyclingPowerMeasurement(0, extreme_angles_deg=Extremes(4096, 0)),
            id="angle-above-12-bits",
        ),
    ],
)
def test_cycling_power_measurement_refuses_values_the_wire_cannot_carry(
    measurement: CyclingPowerMeasurement,
) -> None:
    with pytest.raises(ValueError, match="does not fit"):
        measurement.encode()


def test_cycling_power_feature() -> None:
    feature = (
        CyclingPowerFeature.CRANK_REVOLUTION_DATA | CyclingPowerFeature.NOT_FOR_DISTRIBUTED_SYSTEM
    )
    assert feature.encode() == h("08 00 10 00")
    assert CyclingPowerFeature.decode(h("08 00 10 00")) == feature


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        (CyclingPowerFeature.PEDAL_POWER_BALANCE, 1 << 0),
        (CyclingPowerFeature.WHEEL_REVOLUTION_DATA, 1 << 2),
        (CyclingPowerFeature.CRANK_REVOLUTION_DATA, 1 << 3),
        (CyclingPowerFeature.ACCUMULATED_ENERGY, 1 << 7),
        (CyclingPowerFeature.TORQUE_BASED_MEASUREMENT, 1 << 16),
        (CyclingPowerFeature.ENHANCED_OFFSET_COMPENSATION, 1 << 19),
        (CyclingPowerFeature.NOT_FOR_DISTRIBUTED_SYSTEM, 1 << 20),
        (CyclingPowerFeature.FOR_DISTRIBUTED_SYSTEM, 1 << 21),
    ],
)
def test_cycling_power_feature_bit_positions(flag: int, value: int) -> None:
    assert flag == value


@pytest.mark.parametrize(
    ("location", "value"),
    [
        (SensorLocation.OTHER, 0),
        (SensorLocation.LEFT_CRANK, 5),
        (SensorLocation.REAR_WHEEL, 12),
        (SensorLocation.REAR_HUB, 13),
        (SensorLocation.CHAIN_RING, 16),
    ],
)
def test_sensor_location(location: SensorLocation, value: int) -> None:
    assert location.encode() == bytes([value])
    assert SensorLocation.decode(bytes([value])) is location


def test_sensor_location_rejects_reserved_values() -> None:
    with pytest.raises(ValueError, match="SensorLocation"):
        SensorLocation.decode(h("11"))
