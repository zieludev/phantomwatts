"""Heart Rate Service codecs.

Section numbers refer to the Heart Rate Service specification v1.0.
"""

import enum
from dataclasses import dataclass
from typing import Self

from phantomwatts.protocol._wire import Reader, to_wire, uint

_VALUE_IS_UINT16 = 1 << 0
_SENSOR_CONTACT_DETECTED = 1 << 1
_SENSOR_CONTACT_SUPPORTED = 1 << 2
_ENERGY_EXPENDED = 1 << 3
_RR_INTERVALS = 1 << 4
_RR_TICKS_PER_SECOND = 1024


@dataclass(frozen=True)
class HeartRateMeasurement:
    """Heart Rate Measurement characteristic (3.1).

    `sensor_contact` is None when the sensor has no contact detection, otherwise whether skin
    contact is detected. RR intervals are oldest first.
    """

    heart_rate_bpm: int
    sensor_contact: bool | None = None
    energy_expended_kj: int | None = None
    rr_intervals_s: tuple[float, ...] = ()

    def encode(self) -> bytes:
        flags = 0
        # A value that fits is sent as uint8; uint16 is for rates above 255 bpm (3.1.1.2).
        wide = self.heart_rate_bpm > 0xFF
        if wide:
            flags |= _VALUE_IS_UINT16
        body = uint(self.heart_rate_bpm, 2 if wide else 1, "heart rate")
        if self.sensor_contact is not None:
            flags |= _SENSOR_CONTACT_SUPPORTED
            if self.sensor_contact:
                flags |= _SENSOR_CONTACT_DETECTED
        if self.energy_expended_kj is not None:
            flags |= _ENERGY_EXPENDED
            body += uint(self.energy_expended_kj, 2, "energy expended")
        if self.rr_intervals_s:
            flags |= _RR_INTERVALS
            for interval in self.rr_intervals_s:
                body += uint(to_wire(interval, _RR_TICKS_PER_SECOND), 2, "RR-interval")
        return bytes([flags]) + body

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        flags = reader.uint(1)
        heart_rate = reader.uint(2 if flags & _VALUE_IS_UINT16 else 1)
        contact = (
            bool(flags & _SENSOR_CONTACT_DETECTED) if flags & _SENSOR_CONTACT_SUPPORTED else None
        )
        energy = reader.uint(2) if flags & _ENERGY_EXPENDED else None
        intervals: list[float] = []
        if flags & _RR_INTERVALS:
            if reader.remaining == 0 or reader.remaining % 2:
                raise ValueError("RR-interval flag set without whole RR-interval values")
            while reader.remaining:
                intervals.append(reader.uint(2) / _RR_TICKS_PER_SECOND)
        reader.finish()
        return cls(heart_rate, contact, energy, tuple(intervals))


class BodySensorLocation(enum.IntEnum):
    """Body Sensor Location characteristic (3.2)."""

    OTHER = 0
    CHEST = 1
    WRIST = 2
    FINGER = 3
    HAND = 4
    EAR_LOBE = 5
    FOOT = 6

    def encode(self) -> bytes:
        return bytes([self])

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        location = cls(reader.uint(1))
        reader.finish()
        return location
