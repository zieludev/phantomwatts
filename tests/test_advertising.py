import pytest

from phantomwatts.protocol import advertising, ftms, uuids

SERVICES = (uuids.FITNESS_MACHINE_SERVICE, uuids.CYCLING_POWER_SERVICE, uuids.HEART_RATE_SERVICE)
INDOOR_BIKE = ftms.ServiceData(available=True, machine_types=ftms.MachineType.INDOOR_BIKE).encode()


def advertise(name: str) -> advertising.Advertisement:
    return advertising.trainer_advertisement(
        name, SERVICES, uuids.FITNESS_MACHINE_SERVICE, INDOOR_BIKE
    )


def test_a_name_that_fits_is_advertised_complete() -> None:
    advertisement = advertise("PhantomBike")

    assert advertisement.data == b"".join(
        [
            bytes.fromhex("02 01 06"),
            bytes.fromhex("07 03 26 18 18 18 0d 18"),
            bytes.fromhex("06 16 26 18 01 20 00"),
            bytes.fromhex("0c 09") + b"PhantomBike",
        ]
    )
    assert len(advertisement.data) == advertising.MAX_LEGACY_PAYLOAD
    assert advertisement.scan_response == b""


def test_a_longer_name_is_shortened_and_completed_in_the_scan_response() -> None:
    advertisement = advertise("PhantomWatts")

    structures = dict(advertising.parse(advertisement.data))
    assert structures[advertising.SHORTENED_LOCAL_NAME] == b"PhantomWatt"
    assert advertising.COMPLETE_LOCAL_NAME not in structures
    assert len(advertisement.data) == advertising.MAX_LEGACY_PAYLOAD
    assert advertising.parse(advertisement.scan_response) == [
        (advertising.COMPLETE_LOCAL_NAME, b"PhantomWatts")
    ]


def test_shortening_never_splits_a_character() -> None:
    advertisement = advertise("Rower dla żółwi")

    shortened = dict(advertising.parse(advertisement.data))[advertising.SHORTENED_LOCAL_NAME]
    assert shortened.decode() == "Rower dla "
    assert "Rower dla żółwi".startswith(shortened.decode())


def test_what_scanners_filter_on_is_in_the_advertising_data() -> None:
    structures = dict(advertising.parse(advertise("PhantomBike").data))

    assert structures[advertising.FLAGS] == bytes(
        [advertising.LE_GENERAL_DISCOVERABLE_MODE | advertising.BR_EDR_NOT_SUPPORTED]
    )
    assert structures[advertising.COMPLETE_LIST_OF_16_BIT_SERVICE_UUIDS] == bytes.fromhex(
        "26 18 18 18 0d 18"
    )
    service_data = structures[advertising.SERVICE_DATA_16_BIT_UUID]
    assert service_data[:2] == bytes.fromhex("26 18")
    assert ftms.ServiceData.decode(service_data[2:]).machine_types == ftms.MachineType.INDOOR_BIKE


def test_a_name_too_long_for_a_scan_response_is_refused() -> None:
    with pytest.raises(ValueError, match="longer than a scan response"):
        advertise("x" * 30)


def test_parse_rejects_a_structure_that_runs_past_the_end() -> None:
    with pytest.raises(ValueError, match="ends inside a structure"):
        advertising.parse(bytes.fromhex("02 01 06 05 09 41"))


def test_parse_stops_at_zero_padding() -> None:
    assert advertising.parse(bytes.fromhex("02 01 06 00 00 00")) == [(advertising.FLAGS, b"\x06")]
