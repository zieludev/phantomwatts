"""Expected bytes are worked out by hand from the Heart Rate Service 1.0 specification."""

import pytest

from phantomwatts.protocol.hrs import BodySensorLocation, HeartRateMeasurement


def h(text: str) -> bytes:
    return bytes.fromhex(text)


MEASUREMENTS = [
    pytest.param(HeartRateMeasurement(72), "00 48", id="no-contact-detection"),
    pytest.param(HeartRateMeasurement(180, sensor_contact=True), "06 b4", id="contact-detected"),
    pytest.param(HeartRateMeasurement(0, sensor_contact=False), "04 00", id="contact-lost"),
    pytest.param(HeartRateMeasurement(255), "00 ff", id="largest-uint8"),
    pytest.param(HeartRateMeasurement(300), "01 2c 01", id="uint16-above-255"),
    pytest.param(
        HeartRateMeasurement(120, energy_expended_kj=1000, rr_intervals_s=(0.5, 0.75)),
        "18 78 e8 03 00 02 00 03",
        id="energy-and-rr-intervals",
    ),
]


@pytest.mark.parametrize(("measurement", "wire"), MEASUREMENTS)
def test_heart_rate_measurement(measurement: HeartRateMeasurement, wire: str) -> None:
    assert measurement.encode() == h(wire)
    assert HeartRateMeasurement.decode(h(wire)) == measurement


def test_a_small_rate_sent_as_uint16_still_decodes() -> None:
    assert HeartRateMeasurement.decode(h("01 48 00")) == HeartRateMeasurement(72)


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param("", id="empty"),
        pytest.param("00", id="no-value"),
        pytest.param("01 2c", id="half-a-uint16"),
        pytest.param("00 48 00", id="trailing-byte"),
        pytest.param("10 48", id="rr-flag-without-intervals"),
        pytest.param("10 48 00 02 00", id="half-an-rr-interval"),
    ],
)
def test_heart_rate_measurement_rejects_malformed_input(wire: str) -> None:
    with pytest.raises(ValueError, match=r"truncated|trailing|RR-interval"):
        HeartRateMeasurement.decode(h(wire))


def test_body_sensor_location() -> None:
    assert BodySensorLocation.CHEST.encode() == h("01")
    assert BodySensorLocation.decode(h("01")) is BodySensorLocation.CHEST
    assert [location.value for location in BodySensorLocation] == list(range(7))
