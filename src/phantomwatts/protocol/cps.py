"""Cycling Power Service codecs.

Section numbers refer to the Cycling Power Service specification v1.1; field formats come from
the GATT Specification Supplement.
"""

import enum
from dataclasses import dataclass
from typing import Self

from phantomwatts.protocol._wire import Reader, sint, to_wire, uint


class CyclingPowerFeature(enum.IntFlag):
    """Cycling Power Feature characteristic (3.1)."""

    PEDAL_POWER_BALANCE = 1 << 0
    ACCUMULATED_TORQUE = 1 << 1
    WHEEL_REVOLUTION_DATA = 1 << 2
    CRANK_REVOLUTION_DATA = 1 << 3
    EXTREME_MAGNITUDES = 1 << 4
    EXTREME_ANGLES = 1 << 5
    TOP_AND_BOTTOM_DEAD_SPOT_ANGLES = 1 << 6
    ACCUMULATED_ENERGY = 1 << 7
    OFFSET_COMPENSATION_INDICATOR = 1 << 8
    OFFSET_COMPENSATION = 1 << 9
    MEASUREMENT_CONTENT_MASKING = 1 << 10
    MULTIPLE_SENSOR_LOCATIONS = 1 << 11
    CRANK_LENGTH_ADJUSTMENT = 1 << 12
    CHAIN_LENGTH_ADJUSTMENT = 1 << 13
    CHAIN_WEIGHT_ADJUSTMENT = 1 << 14
    SPAN_LENGTH_ADJUSTMENT = 1 << 15
    TORQUE_BASED_MEASUREMENT = 1 << 16
    INSTANTANEOUS_MEASUREMENT_DIRECTION = 1 << 17
    FACTORY_CALIBRATION_DATE = 1 << 18
    ENHANCED_OFFSET_COMPENSATION = 1 << 19
    # Bits 20-21 are one two-bit field, Distributed System Support.
    NOT_FOR_DISTRIBUTED_SYSTEM = 0b01 << 20
    FOR_DISTRIBUTED_SYSTEM = 0b10 << 20

    def encode(self) -> bytes:
        return uint(self, 4, "cycling power feature")

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        feature = cls(reader.uint(4))
        reader.finish()
        return feature


class SensorLocation(enum.IntEnum):
    """Sensor Location characteristic (3.3)."""

    OTHER = 0
    TOP_OF_SHOE = 1
    IN_SHOE = 2
    HIP = 3
    FRONT_WHEEL = 4
    LEFT_CRANK = 5
    RIGHT_CRANK = 6
    LEFT_PEDAL = 7
    RIGHT_PEDAL = 8
    FRONT_HUB = 9
    REAR_DROPOUT = 10
    CHAINSTAY = 11
    REAR_WHEEL = 12
    REAR_HUB = 13
    CHEST = 14
    SPIDER = 15
    CHAIN_RING = 16

    def encode(self) -> bytes:
        return bytes([self])

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        location = cls(reader.uint(1))
        reader.finish()
        return location


_WHEEL_TICKS_PER_SECOND = 2048
_CRANK_TICKS_PER_SECOND = 1024
_TORQUE_PER_NEWTON_METRE = 32


@dataclass(frozen=True)
class WheelRevolutions:
    """Wheel Revolution Data (3.2.1.5). The event time wraps every 32 seconds."""

    cumulative: int
    last_event_time_s: float

    def encode(self) -> bytes:
        return uint(self.cumulative, 4, "cumulative wheel revolutions") + uint(
            to_wire(self.last_event_time_s, _WHEEL_TICKS_PER_SECOND), 2, "last wheel event time"
        )

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(reader.uint(4), reader.uint(2) / _WHEEL_TICKS_PER_SECOND)


@dataclass(frozen=True)
class CrankRevolutions:
    """Crank Revolution Data (3.2.1.6). Both fields wrap: the count at 65536, the time at 64 s."""

    cumulative: int
    last_event_time_s: float

    def encode(self) -> bytes:
        return uint(self.cumulative, 2, "cumulative crank revolutions") + uint(
            to_wire(self.last_event_time_s, _CRANK_TICKS_PER_SECOND), 2, "last crank event time"
        )

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(reader.uint(2), reader.uint(2) / _CRANK_TICKS_PER_SECOND)


@dataclass(frozen=True)
class Extremes:
    """A maximum and minimum pair, as carried by the extreme magnitude and angle fields."""

    maximum: float
    minimum: float


_PEDAL_POWER_BALANCE = 1 << 0
_PEDAL_POWER_BALANCE_REFERENCE_LEFT = 1 << 1
_ACCUMULATED_TORQUE = 1 << 2
_ACCUMULATED_TORQUE_CRANK_BASED = 1 << 3
_WHEEL_REVOLUTIONS = 1 << 4
_CRANK_REVOLUTIONS = 1 << 5
_EXTREME_FORCES = 1 << 6
_EXTREME_TORQUES = 1 << 7
_EXTREME_ANGLES = 1 << 8
_TOP_DEAD_SPOT_ANGLE = 1 << 9
_BOTTOM_DEAD_SPOT_ANGLE = 1 << 10
_ACCUMULATED_ENERGY = 1 << 11
_OFFSET_COMPENSATION_INDICATOR = 1 << 12


@dataclass(frozen=True)
class CyclingPowerMeasurement:
    """Cycling Power Measurement characteristic (3.2). A field that is None is left out."""

    instantaneous_power_w: int
    pedal_power_balance_percent: float | None = None
    pedal_power_balance_reference_left: bool = False
    accumulated_torque_nm: float | None = None
    accumulated_torque_crank_based: bool = False
    wheel_revolutions: WheelRevolutions | None = None
    crank_revolutions: CrankRevolutions | None = None
    extreme_forces_n: Extremes | None = None
    extreme_torques_nm: Extremes | None = None
    extreme_angles_deg: Extremes | None = None
    top_dead_spot_angle_deg: int | None = None
    bottom_dead_spot_angle_deg: int | None = None
    accumulated_energy_kj: int | None = None
    offset_compensation_indicator: bool = False

    def encode(self) -> bytes:
        flags = 0
        if self.pedal_power_balance_reference_left:
            flags |= _PEDAL_POWER_BALANCE_REFERENCE_LEFT
        if self.accumulated_torque_crank_based:
            flags |= _ACCUMULATED_TORQUE_CRANK_BASED
        if self.offset_compensation_indicator:
            flags |= _OFFSET_COMPENSATION_INDICATOR
        body = sint(self.instantaneous_power_w, 2, "instantaneous power")
        if self.pedal_power_balance_percent is not None:
            flags |= _PEDAL_POWER_BALANCE
            body += uint(to_wire(self.pedal_power_balance_percent, 2), 1, "pedal power balance")
        if self.accumulated_torque_nm is not None:
            flags |= _ACCUMULATED_TORQUE
            body += uint(
                to_wire(self.accumulated_torque_nm, _TORQUE_PER_NEWTON_METRE),
                2,
                "accumulated torque",
            )
        if self.wheel_revolutions is not None:
            flags |= _WHEEL_REVOLUTIONS
            body += self.wheel_revolutions.encode()
        if self.crank_revolutions is not None:
            flags |= _CRANK_REVOLUTIONS
            body += self.crank_revolutions.encode()
        if self.extreme_forces_n is not None:
            flags |= _EXTREME_FORCES
            body += _encode_magnitudes(self.extreme_forces_n, 1, "extreme force")
        if self.extreme_torques_nm is not None:
            flags |= _EXTREME_TORQUES
            body += _encode_magnitudes(
                self.extreme_torques_nm, _TORQUE_PER_NEWTON_METRE, "extreme torque"
            )
        if self.extreme_angles_deg is not None:
            flags |= _EXTREME_ANGLES
            body += _encode_angles(self.extreme_angles_deg)
        if self.top_dead_spot_angle_deg is not None:
            flags |= _TOP_DEAD_SPOT_ANGLE
            body += uint(self.top_dead_spot_angle_deg, 2, "top dead spot angle")
        if self.bottom_dead_spot_angle_deg is not None:
            flags |= _BOTTOM_DEAD_SPOT_ANGLE
            body += uint(self.bottom_dead_spot_angle_deg, 2, "bottom dead spot angle")
        if self.accumulated_energy_kj is not None:
            flags |= _ACCUMULATED_ENERGY
            body += uint(self.accumulated_energy_kj, 2, "accumulated energy")
        return uint(flags, 2, "flags") + body

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        flags = reader.uint(2)
        power = reader.sint(2)

        def present(flag: int) -> bool:
            return bool(flags & flag)

        measurement = cls(
            instantaneous_power_w=power,
            pedal_power_balance_percent=(
                reader.uint(1) / 2 if present(_PEDAL_POWER_BALANCE) else None
            ),
            pedal_power_balance_reference_left=present(_PEDAL_POWER_BALANCE_REFERENCE_LEFT),
            accumulated_torque_nm=(
                reader.uint(2) / _TORQUE_PER_NEWTON_METRE if present(_ACCUMULATED_TORQUE) else None
            ),
            accumulated_torque_crank_based=present(_ACCUMULATED_TORQUE_CRANK_BASED),
            wheel_revolutions=(
                WheelRevolutions.read(reader) if present(_WHEEL_REVOLUTIONS) else None
            ),
            crank_revolutions=(
                CrankRevolutions.read(reader) if present(_CRANK_REVOLUTIONS) else None
            ),
            extreme_forces_n=_read_magnitudes(reader, 1) if present(_EXTREME_FORCES) else None,
            extreme_torques_nm=(
                _read_magnitudes(reader, _TORQUE_PER_NEWTON_METRE)
                if present(_EXTREME_TORQUES)
                else None
            ),
            extreme_angles_deg=_read_angles(reader) if present(_EXTREME_ANGLES) else None,
            top_dead_spot_angle_deg=reader.uint(2) if present(_TOP_DEAD_SPOT_ANGLE) else None,
            bottom_dead_spot_angle_deg=(
                reader.uint(2) if present(_BOTTOM_DEAD_SPOT_ANGLE) else None
            ),
            accumulated_energy_kj=reader.uint(2) if present(_ACCUMULATED_ENERGY) else None,
            offset_compensation_indicator=present(_OFFSET_COMPENSATION_INDICATOR),
        )
        reader.finish()
        return measurement


def _encode_magnitudes(extremes: Extremes, per_unit: int, field: str) -> bytes:
    return sint(to_wire(extremes.maximum, per_unit), 2, f"maximum {field}") + sint(
        to_wire(extremes.minimum, per_unit), 2, f"minimum {field}"
    )


def _read_magnitudes(reader: Reader, per_unit: int) -> Extremes:
    maximum, minimum = reader.sint(2), reader.sint(2)
    if per_unit == 1:
        return Extremes(maximum, minimum)
    return Extremes(maximum / per_unit, minimum / per_unit)


_ANGLE_BITS = 12
_ANGLE_MASK = (1 << _ANGLE_BITS) - 1


def _encode_angles(extremes: Extremes) -> bytes:
    maximum, minimum = round(extremes.maximum), round(extremes.minimum)
    for name, angle in (("maximum", maximum), ("minimum", minimum)):
        if not 0 <= angle <= _ANGLE_MASK:
            raise ValueError(f"{name} extreme angle {angle} does not fit in 12 bits")
    # Two 12-bit values share three bytes, the maximum in the low bits (3.2.1.9).
    return uint(minimum << _ANGLE_BITS | maximum, 3, "extreme angles")


def _read_angles(reader: Reader) -> Extremes:
    packed = reader.uint(3)
    return Extremes(maximum=packed & _ANGLE_MASK, minimum=packed >> _ANGLE_BITS)
