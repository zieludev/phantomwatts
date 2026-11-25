"""Advertising data for the trainer (Core Specification Supplement, Part A)."""

from collections.abc import Sequence
from dataclasses import dataclass

from phantomwatts.protocol._wire import uint

FLAGS = 0x01
COMPLETE_LIST_OF_16_BIT_SERVICE_UUIDS = 0x03
SHORTENED_LOCAL_NAME = 0x08
COMPLETE_LOCAL_NAME = 0x09
SERVICE_DATA_16_BIT_UUID = 0x16

LE_GENERAL_DISCOVERABLE_MODE = 0x02
BR_EDR_NOT_SUPPORTED = 0x04

MAX_LEGACY_PAYLOAD = 31
_STRUCTURE_HEADER = 2


@dataclass(frozen=True)
class Advertisement:
    data: bytes
    scan_response: bytes


def structure(ad_type: int, payload: bytes) -> bytes:
    return bytes([len(payload) + 1, ad_type]) + payload


def parse(data: bytes) -> list[tuple[int, bytes]]:
    """Split advertising data into (AD type, payload) pairs."""
    structures: list[tuple[int, bytes]] = []
    position = 0
    while position < len(data):
        length = data[position]
        if length == 0:
            break
        end = position + 1 + length
        if end > len(data):
            raise ValueError("advertising data ends inside a structure")
        structures.append((data[position + 1], bytes(data[position + 2 : end])))
        position = end
    return structures


def trainer_advertisement(
    name: str, service_uuids: Sequence[int], service_data_uuid: int, service_data: bytes
) -> Advertisement:
    """Build legacy advertising data that scanners filtering on service UUIDs will match.

    Everything a scanner filters on goes in the advertising data itself, never in the scan
    response, and so does the name: a passive scanner does not ask for a scan response, and
    Bumble's simulated link does not carry one. A name that does not fit is shortened there and
    sent complete in the scan response.
    """
    fixed = (
        structure(FLAGS, bytes([LE_GENERAL_DISCOVERABLE_MODE | BR_EDR_NOT_SUPPORTED]))
        + structure(
            COMPLETE_LIST_OF_16_BIT_SERVICE_UUIDS,
            b"".join(uint(uuid, 2, "service UUID") for uuid in service_uuids),
        )
        + structure(
            SERVICE_DATA_16_BIT_UUID, uint(service_data_uuid, 2, "service UUID") + service_data
        )
    )
    name_room = MAX_LEGACY_PAYLOAD - len(fixed) - _STRUCTURE_HEADER
    if name_room < 1:
        raise ValueError("the service UUIDs and service data leave no room for a name")
    encoded_name = name.encode()
    complete_name = structure(COMPLETE_LOCAL_NAME, encoded_name)
    if len(complete_name) > MAX_LEGACY_PAYLOAD:
        raise ValueError(f"device name {name!r} is longer than a scan response can carry")
    if len(encoded_name) <= name_room:
        return Advertisement(data=fixed + complete_name, scan_response=b"")
    # errors="ignore" drops a multi-byte character the cut would otherwise split.
    prefix = encoded_name[:name_room].decode(errors="ignore").encode()
    return Advertisement(
        data=fixed + structure(SHORTENED_LOCAL_NAME, prefix), scan_response=complete_name
    )
