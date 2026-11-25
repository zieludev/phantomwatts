"""Fitness Machine Service codecs for an indoor bike.

Section numbers refer to the Fitness Machine Service specification v1.0. Field formats that
the specification delegates to the Assigned Numbers come from the GATT Specification
Supplement, with one exception noted at `SupportedResistanceLevelRange`.
"""

import enum
from dataclasses import dataclass
from typing import Any, ClassVar, Self, TypeAlias

from phantomwatts.protocol._wire import Reader, sint, to_wire, uint


class FitnessMachineFeatures(enum.IntFlag):
    """Fitness Machine Features field (4.3.1.1)."""

    AVERAGE_SPEED = 1 << 0
    CADENCE = 1 << 1
    TOTAL_DISTANCE = 1 << 2
    INCLINATION = 1 << 3
    ELEVATION_GAIN = 1 << 4
    PACE = 1 << 5
    STEP_COUNT = 1 << 6
    RESISTANCE_LEVEL = 1 << 7
    STRIDE_COUNT = 1 << 8
    EXPENDED_ENERGY = 1 << 9
    HEART_RATE_MEASUREMENT = 1 << 10
    METABOLIC_EQUIVALENT = 1 << 11
    ELAPSED_TIME = 1 << 12
    REMAINING_TIME = 1 << 13
    POWER_MEASUREMENT = 1 << 14
    FORCE_ON_BELT_AND_POWER_OUTPUT = 1 << 15
    USER_DATA_RETENTION = 1 << 16


class TargetSettingFeatures(enum.IntFlag):
    """Target Setting Features field (4.3.1.2)."""

    SPEED = 1 << 0
    INCLINATION = 1 << 1
    RESISTANCE = 1 << 2
    POWER = 1 << 3
    HEART_RATE = 1 << 4
    TARGETED_EXPENDED_ENERGY = 1 << 5
    TARGETED_STEP_NUMBER = 1 << 6
    TARGETED_STRIDE_NUMBER = 1 << 7
    TARGETED_DISTANCE = 1 << 8
    TARGETED_TRAINING_TIME = 1 << 9
    TARGETED_TIME_IN_TWO_HEART_RATE_ZONES = 1 << 10
    TARGETED_TIME_IN_THREE_HEART_RATE_ZONES = 1 << 11
    TARGETED_TIME_IN_FIVE_HEART_RATE_ZONES = 1 << 12
    INDOOR_BIKE_SIMULATION = 1 << 13
    WHEEL_CIRCUMFERENCE = 1 << 14
    SPIN_DOWN_CONTROL = 1 << 15
    TARGETED_CADENCE = 1 << 16


@dataclass(frozen=True)
class Feature:
    """Fitness Machine Feature characteristic (4.3)."""

    machine: FitnessMachineFeatures
    target_settings: TargetSettingFeatures

    def encode(self) -> bytes:
        return uint(self.machine, 4, "fitness machine features") + uint(
            self.target_settings, 4, "target setting features"
        )

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        feature = cls(FitnessMachineFeatures(reader.uint(4)), TargetSettingFeatures(reader.uint(4)))
        reader.finish()
        return feature


class MachineType(enum.IntFlag):
    """Fitness Machine Type field of the advertised service data (3.1.2)."""

    TREADMILL = 1 << 0
    CROSS_TRAINER = 1 << 1
    STEP_CLIMBER = 1 << 2
    STAIR_CLIMBER = 1 << 3
    ROWER = 1 << 4
    INDOOR_BIKE = 1 << 5


@dataclass(frozen=True)
class ServiceData:
    """Payload of the Service Data AD type, after the service UUID (3.1)."""

    available: bool
    machine_types: MachineType

    def encode(self) -> bytes:
        return bytes([int(self.available)]) + uint(self.machine_types, 2, "fitness machine type")

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        service_data = cls(bool(reader.uint(1) & 0x01), MachineType(reader.uint(2)))
        reader.finish()
        return service_data


@dataclass(frozen=True)
class ExpendedEnergy:
    """The three energy fields, which share one flag bit. None means Data Not Available."""

    total_kcal: int | None
    per_hour_kcal: int | None
    per_minute_kcal: int | None

    _NOT_AVAILABLE_16: ClassVar[int] = 0xFFFF
    _NOT_AVAILABLE_8: ClassVar[int] = 0xFF

    def encode(self) -> bytes:
        return (
            _energy(self.total_kcal, 2, self._NOT_AVAILABLE_16, "total energy")
            + _energy(self.per_hour_kcal, 2, self._NOT_AVAILABLE_16, "energy per hour")
            + _energy(self.per_minute_kcal, 1, self._NOT_AVAILABLE_8, "energy per minute")
        )

    @classmethod
    def read(cls, reader: Reader) -> Self:
        total, per_hour, per_minute = reader.uint(2), reader.uint(2), reader.uint(1)
        return cls(
            None if total == cls._NOT_AVAILABLE_16 else total,
            None if per_hour == cls._NOT_AVAILABLE_16 else per_hour,
            None if per_minute == cls._NOT_AVAILABLE_8 else per_minute,
        )


def _energy(value: int | None, size: int, not_available: int, field: str) -> bytes:
    if value is None:
        return uint(not_available, size, field)
    if value == not_available:
        raise ValueError(f"{field} {value} is the Data Not Available marker")
    return uint(value, size, field)


@dataclass(frozen=True)
class _Field:
    name: str
    flag: int
    size: int
    signed: bool = False
    per_unit: int = 1

    def encode(self, value: float) -> bytes:
        raw = to_wire(value, self.per_unit)
        return sint(raw, self.size, self.name) if self.signed else uint(raw, self.size, self.name)

    def read(self, reader: Reader) -> float:
        raw = reader.sint(self.size) if self.signed else reader.uint(self.size)
        return raw if self.per_unit == 1 else raw / self.per_unit


_MORE_DATA = 1 << 0
_EXPENDED_ENERGY = 1 << 8
_SPEED = _Field("instantaneous_speed_kmh", _MORE_DATA, 2, per_unit=100)
_FIELDS_BEFORE_ENERGY = (
    _Field("average_speed_kmh", 1 << 1, 2, per_unit=100),
    _Field("instantaneous_cadence_rpm", 1 << 2, 2, per_unit=2),
    _Field("average_cadence_rpm", 1 << 3, 2, per_unit=2),
    _Field("total_distance_m", 1 << 4, 3),
    _Field("resistance_level", 1 << 5, 2, signed=True),
    _Field("instantaneous_power_w", 1 << 6, 2, signed=True),
    _Field("average_power_w", 1 << 7, 2, signed=True),
)
_FIELDS_AFTER_ENERGY = (
    _Field("heart_rate_bpm", 1 << 9, 1),
    _Field("metabolic_equivalent", 1 << 10, 1, per_unit=10),
    _Field("elapsed_time_s", 1 << 11, 2),
    _Field("remaining_time_s", 1 << 12, 2),
)


@dataclass(frozen=True)
class IndoorBikeData:
    """Indoor Bike Data characteristic (4.9). A field that is None is left out.

    `resistance_level` is the two-byte signed field of FTMS 1.0 era definitions, which is what
    deployed trainers and apps use; the current GATT Specification Supplement shrank it to one
    byte. Senders that want to stay unambiguous leave it out.
    """

    instantaneous_speed_kmh: float | None = None
    average_speed_kmh: float | None = None
    instantaneous_cadence_rpm: float | None = None
    average_cadence_rpm: float | None = None
    total_distance_m: int | None = None
    resistance_level: int | None = None
    instantaneous_power_w: int | None = None
    average_power_w: int | None = None
    expended_energy: ExpendedEnergy | None = None
    heart_rate_bpm: int | None = None
    metabolic_equivalent: float | None = None
    elapsed_time_s: int | None = None
    remaining_time_s: int | None = None

    def encode(self) -> bytes:
        # Bit 0 is inverted: it is "More Data", and speed is present when it is 0 (4.9.1.2).
        flags, body = 0, b""
        if self.instantaneous_speed_kmh is None:
            flags |= _MORE_DATA
        else:
            body += _SPEED.encode(self.instantaneous_speed_kmh)
        flags, body = self._append(_FIELDS_BEFORE_ENERGY, flags, body)
        if self.expended_energy is not None:
            flags |= _EXPENDED_ENERGY
            body += self.expended_energy.encode()
        flags, body = self._append(_FIELDS_AFTER_ENERGY, flags, body)
        return uint(flags, 2, "flags") + body

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        flags = reader.uint(2)
        values: dict[str, Any] = {}
        if not flags & _MORE_DATA:
            values[_SPEED.name] = _SPEED.read(reader)
        cls._read(_FIELDS_BEFORE_ENERGY, flags, reader, values)
        if flags & _EXPENDED_ENERGY:
            values["expended_energy"] = ExpendedEnergy.read(reader)
        cls._read(_FIELDS_AFTER_ENERGY, flags, reader, values)
        reader.finish()
        return cls(**values)

    def _append(self, fields: tuple[_Field, ...], flags: int, body: bytes) -> tuple[int, bytes]:
        for field in fields:
            value = getattr(self, field.name)
            if value is not None:
                flags |= field.flag
                body += field.encode(value)
        return flags, body

    @staticmethod
    def _read(fields: tuple[_Field, ...], flags: int, reader: Reader, into: dict[str, Any]) -> None:
        for field in fields:
            if flags & field.flag:
                into[field.name] = field.read(reader)


@dataclass(frozen=True)
class SupportedPowerRange:
    """Supported Power Range characteristic (4.14)."""

    minimum_w: int
    maximum_w: int
    increment_w: int

    def encode(self) -> bytes:
        return (
            sint(self.minimum_w, 2, "minimum power")
            + sint(self.maximum_w, 2, "maximum power")
            + uint(self.increment_w, 2, "minimum increment")
        )

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        power_range = cls(reader.sint(2), reader.sint(2), reader.uint(2))
        reader.finish()
        return power_range


@dataclass(frozen=True)
class SupportedResistanceLevelRange:
    """Supported Resistance Level Range characteristic (4.13).

    Encoded as sint16, sint16, uint16 with a resolution of 0.1, the format FTMS 1.0 was
    published with and the one trainers and apps in the field use. The current GATT
    Specification Supplement (3.231) lists three uint8 fields instead, which would not
    interoperate with them.
    """

    minimum: float
    maximum: float
    increment: float

    def encode(self) -> bytes:
        return (
            sint(to_wire(self.minimum, 10), 2, "minimum resistance level")
            + sint(to_wire(self.maximum, 10), 2, "maximum resistance level")
            + uint(to_wire(self.increment, 10), 2, "minimum increment")
        )

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        resistance_range = cls(reader.sint(2) / 10, reader.sint(2) / 10, reader.uint(2) / 10)
        reader.finish()
        return resistance_range


class OpCode(enum.IntEnum):
    """Fitness Machine Control Point op codes (Table 4.15)."""

    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_SPEED = 0x02
    SET_TARGET_INCLINATION = 0x03
    SET_TARGET_RESISTANCE_LEVEL = 0x04
    SET_TARGET_POWER = 0x05
    SET_TARGET_HEART_RATE = 0x06
    START_OR_RESUME = 0x07
    STOP_OR_PAUSE = 0x08
    SET_TARGETED_EXPENDED_ENERGY = 0x09
    SET_TARGETED_NUMBER_OF_STEPS = 0x0A
    SET_TARGETED_NUMBER_OF_STRIDES = 0x0B
    SET_TARGETED_DISTANCE = 0x0C
    SET_TARGETED_TRAINING_TIME = 0x0D
    SET_TARGETED_TIME_IN_TWO_HEART_RATE_ZONES = 0x0E
    SET_TARGETED_TIME_IN_THREE_HEART_RATE_ZONES = 0x0F
    SET_TARGETED_TIME_IN_FIVE_HEART_RATE_ZONES = 0x10
    SET_INDOOR_BIKE_SIMULATION_PARAMETERS = 0x11
    SET_WHEEL_CIRCUMFERENCE = 0x12
    SPIN_DOWN_CONTROL = 0x13
    SET_TARGETED_CADENCE = 0x14
    RESPONSE_CODE = 0x80


class ResultCode(enum.IntEnum):
    """Result codes of the Response Code indication (Table 4.24)."""

    SUCCESS = 0x01
    OP_CODE_NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    OPERATION_FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


class StopOrPauseAction(enum.IntEnum):
    """Control Information parameter (Table 4.16)."""

    STOP = 0x01
    PAUSE = 0x02


@dataclass(frozen=True)
class SimulationParameters:
    """Simulation Parameter Array (Table 4.20)."""

    wind_speed_mps: float
    grade_percent: float
    crr: float
    cw_kg_per_m: float

    def encode(self) -> bytes:
        return (
            sint(to_wire(self.wind_speed_mps, 1000), 2, "wind speed")
            + sint(to_wire(self.grade_percent, 100), 2, "grade")
            + uint(to_wire(self.crr, 10000), 1, "rolling resistance coefficient")
            + uint(to_wire(self.cw_kg_per_m, 100), 1, "wind resistance coefficient")
        )

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(
            wind_speed_mps=reader.sint(2) / 1000,
            grade_percent=reader.sint(2) / 100,
            crr=reader.uint(1) / 10000,
            cw_kg_per_m=reader.uint(1) / 100,
        )


class StatusOpCode(enum.IntEnum):
    """Fitness Machine Status op codes (Table 4.26)."""

    RESET = 0x01
    STOPPED_OR_PAUSED_BY_USER = 0x02
    STOPPED_BY_SAFETY_KEY = 0x03
    STARTED_OR_RESUMED_BY_USER = 0x04
    TARGET_SPEED_CHANGED = 0x05
    TARGET_INCLINE_CHANGED = 0x06
    TARGET_RESISTANCE_LEVEL_CHANGED = 0x07
    TARGET_POWER_CHANGED = 0x08
    TARGET_HEART_RATE_CHANGED = 0x09
    TARGETED_EXPENDED_ENERGY_CHANGED = 0x0A
    TARGETED_NUMBER_OF_STEPS_CHANGED = 0x0B
    TARGETED_NUMBER_OF_STRIDES_CHANGED = 0x0C
    TARGETED_DISTANCE_CHANGED = 0x0D
    TARGETED_TRAINING_TIME_CHANGED = 0x0E
    TARGETED_TIME_IN_TWO_HEART_RATE_ZONES_CHANGED = 0x0F
    TARGETED_TIME_IN_THREE_HEART_RATE_ZONES_CHANGED = 0x10
    TARGETED_TIME_IN_FIVE_HEART_RATE_ZONES_CHANGED = 0x11
    INDOOR_BIKE_SIMULATION_PARAMETERS_CHANGED = 0x12
    WHEEL_CIRCUMFERENCE_CHANGED = 0x13
    SPIN_DOWN_STATUS = 0x14
    TARGETED_CADENCE_CHANGED = 0x15
    CONTROL_PERMISSION_LOST = 0xFF


class _Bare:
    """A message that is its op code and nothing else."""

    OPCODE: ClassVar[int]

    def encode(self) -> bytes:
        return bytes([self.OPCODE])

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls()


@dataclass(frozen=True)
class _ResistanceLevel:
    OPCODE: ClassVar[int]

    level: float

    def encode(self) -> bytes:
        return bytes([self.OPCODE]) + uint(to_wire(self.level, 10), 1, "target resistance level")

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(level=reader.uint(1) / 10)


@dataclass(frozen=True)
class _Power:
    OPCODE: ClassVar[int]

    watts: int

    def encode(self) -> bytes:
        return bytes([self.OPCODE]) + sint(self.watts, 2, "target power")

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(watts=reader.sint(2))


@dataclass(frozen=True)
class _StopOrPause:
    OPCODE: ClassVar[int]

    action: StopOrPauseAction

    def encode(self) -> bytes:
        return bytes([self.OPCODE, self.action])

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(action=StopOrPauseAction(reader.uint(1)))


@dataclass(frozen=True)
class _Simulation:
    OPCODE: ClassVar[int]

    parameters: SimulationParameters

    def encode(self) -> bytes:
        return bytes([self.OPCODE]) + self.parameters.encode()

    @classmethod
    def read(cls, reader: Reader) -> Self:
        return cls(parameters=SimulationParameters.read(reader))


@dataclass(frozen=True)
class RequestControl(_Bare):
    OPCODE = OpCode.REQUEST_CONTROL


@dataclass(frozen=True)
class Reset(_Bare):
    OPCODE = OpCode.RESET


@dataclass(frozen=True)
class SetTargetResistanceLevel(_ResistanceLevel):
    OPCODE = OpCode.SET_TARGET_RESISTANCE_LEVEL


@dataclass(frozen=True)
class SetTargetPower(_Power):
    OPCODE = OpCode.SET_TARGET_POWER


@dataclass(frozen=True)
class StartOrResume(_Bare):
    OPCODE = OpCode.START_OR_RESUME


@dataclass(frozen=True)
class StopOrPause(_StopOrPause):
    OPCODE = OpCode.STOP_OR_PAUSE


@dataclass(frozen=True)
class SetIndoorBikeSimulationParameters(_Simulation):
    OPCODE = OpCode.SET_INDOOR_BIKE_SIMULATION_PARAMETERS


@dataclass(frozen=True)
class UnmodelledRequest:
    """A request this codec carries as raw bytes: other machine types, reserved op codes."""

    opcode: int
    parameter: bytes = b""

    def encode(self) -> bytes:
        return bytes([self.opcode]) + self.parameter


ModelledRequest: TypeAlias = (
    RequestControl
    | Reset
    | SetTargetResistanceLevel
    | SetTargetPower
    | StartOrResume
    | StopOrPause
    | SetIndoorBikeSimulationParameters
)
Request: TypeAlias = ModelledRequest | UnmodelledRequest

_REQUEST_TYPES: dict[int, type[ModelledRequest]] = {
    request_type.OPCODE: request_type
    for request_type in (
        RequestControl,
        Reset,
        SetTargetResistanceLevel,
        SetTargetPower,
        StartOrResume,
        StopOrPause,
        SetIndoorBikeSimulationParameters,
    )
}


class InvalidParameterError(ValueError):
    """A known op code arrived with a parameter the specification does not allow."""

    def __init__(self, opcode: int, reason: str) -> None:
        super().__init__(f"op code 0x{opcode:02X}: {reason}")
        self.opcode = opcode


def decode_request(data: bytes) -> Request:
    """Decode a value written to the Fitness Machine Control Point (4.16)."""
    if not data:
        raise ValueError("empty Fitness Machine Control Point value")
    opcode, parameter = data[0], bytes(data[1:])
    request_type = _REQUEST_TYPES.get(opcode)
    if request_type is None:
        return UnmodelledRequest(opcode, parameter)
    reader = Reader(parameter)
    try:
        request = request_type.read(reader)
        reader.finish()
    except ValueError as error:
        raise InvalidParameterError(opcode, str(error)) from error
    return request


@dataclass(frozen=True)
class ControlPointResponse:
    """Response Code indication (4.16.2.22, Table 4.23)."""

    request_opcode: int
    result: ResultCode
    parameter: bytes = b""

    def encode(self) -> bytes:
        return bytes([OpCode.RESPONSE_CODE, self.request_opcode, self.result]) + self.parameter

    @classmethod
    def decode(cls, data: bytes) -> Self:
        reader = Reader(data)
        if reader.uint(1) != OpCode.RESPONSE_CODE:
            raise ValueError("not a Response Code indication")
        return cls(reader.uint(1), ResultCode(reader.uint(1)), reader.rest())


@dataclass(frozen=True)
class MachineReset(_Bare):
    OPCODE = StatusOpCode.RESET


@dataclass(frozen=True)
class StoppedOrPausedByUser(_StopOrPause):
    OPCODE = StatusOpCode.STOPPED_OR_PAUSED_BY_USER


@dataclass(frozen=True)
class StartedOrResumedByUser(_Bare):
    OPCODE = StatusOpCode.STARTED_OR_RESUMED_BY_USER


@dataclass(frozen=True)
class TargetResistanceLevelChanged(_ResistanceLevel):
    OPCODE = StatusOpCode.TARGET_RESISTANCE_LEVEL_CHANGED


@dataclass(frozen=True)
class TargetPowerChanged(_Power):
    OPCODE = StatusOpCode.TARGET_POWER_CHANGED


@dataclass(frozen=True)
class IndoorBikeSimulationParametersChanged(_Simulation):
    OPCODE = StatusOpCode.INDOOR_BIKE_SIMULATION_PARAMETERS_CHANGED


@dataclass(frozen=True)
class ControlPermissionLost(_Bare):
    OPCODE = StatusOpCode.CONTROL_PERMISSION_LOST


@dataclass(frozen=True)
class UnmodelledStatus:
    """A status this codec carries as raw bytes."""

    opcode: int
    parameter: bytes = b""

    def encode(self) -> bytes:
        return bytes([self.opcode]) + self.parameter


ModelledStatus: TypeAlias = (
    MachineReset
    | StoppedOrPausedByUser
    | StartedOrResumedByUser
    | TargetResistanceLevelChanged
    | TargetPowerChanged
    | IndoorBikeSimulationParametersChanged
    | ControlPermissionLost
)
Status: TypeAlias = ModelledStatus | UnmodelledStatus

_STATUS_TYPES: dict[int, type[ModelledStatus]] = {
    status_type.OPCODE: status_type
    for status_type in (
        MachineReset,
        StoppedOrPausedByUser,
        StartedOrResumedByUser,
        TargetResistanceLevelChanged,
        TargetPowerChanged,
        IndoorBikeSimulationParametersChanged,
        ControlPermissionLost,
    )
}


def decode_status(data: bytes) -> Status:
    """Decode a Fitness Machine Status notification (4.17)."""
    if not data:
        raise ValueError("empty Fitness Machine Status value")
    opcode, parameter = data[0], bytes(data[1:])
    status_type = _STATUS_TYPES.get(opcode)
    if status_type is None:
        return UnmodelledStatus(opcode, parameter)
    reader = Reader(parameter)
    status = status_type.read(reader)
    reader.finish()
    return status
