"""What the trainer's GATT services say, as bytes, with no Bluetooth stack involved.

The front end sits between the port and the codecs. It turns telemetry into characteristic
values and Fitness Machine Control Point writes into commands, and it keeps the log of what
the app asked for. `phantomwatts.bumble_adapter` puts it on the air.
"""

from collections.abc import Callable
from dataclasses import dataclass

from phantomwatts.port import (
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
    TrainerBackend,
)
from phantomwatts.protocol import advertising, cps, ftms, hrs, uuids

ADVERTISED_SERVICES = (
    uuids.FITNESS_MACHINE_SERVICE,
    uuids.CYCLING_POWER_SERVICE,
    uuids.HEART_RATE_SERVICE,
)

FITNESS_MACHINE_FEATURE = ftms.Feature(
    machine=ftms.FitnessMachineFeatures.CADENCE
    | ftms.FitnessMachineFeatures.TOTAL_DISTANCE
    | ftms.FitnessMachineFeatures.ELAPSED_TIME
    | ftms.FitnessMachineFeatures.POWER_MEASUREMENT,
    target_settings=ftms.TargetSettingFeatures.RESISTANCE
    | ftms.TargetSettingFeatures.POWER
    | ftms.TargetSettingFeatures.INDOOR_BIKE_SIMULATION,
)

# Wheel revolution data is left out on purpose: supporting it makes the Cycling Power Control
# Point mandatory (CPS 3.4), and speed already travels in the Indoor Bike Data.
CYCLING_POWER_FEATURE = (
    cps.CyclingPowerFeature.CRANK_REVOLUTION_DATA
    | cps.CyclingPowerFeature.NOT_FOR_DISTRIBUTED_SYSTEM
)
SENSOR_LOCATION = cps.SensorLocation.REAR_HUB
BODY_SENSOR_LOCATION = hrs.BodySensorLocation.CHEST

_RESULT_CODES = {
    Outcome.SUCCESS: ftms.ResultCode.SUCCESS,
    Outcome.NOT_SUPPORTED: ftms.ResultCode.OP_CODE_NOT_SUPPORTED,
    Outcome.INVALID_PARAMETER: ftms.ResultCode.INVALID_PARAMETER,
    Outcome.FAILED: ftms.ResultCode.OPERATION_FAILED,
    Outcome.CONTROL_NOT_PERMITTED: ftms.ResultCode.CONTROL_NOT_PERMITTED,
}

_UINT16_MAX = 0xFFFF
_CRANK_TICKS_PER_SECOND = 1024


@dataclass(frozen=True)
class Unrecognized:
    """A control point write the trainer did not act on: unsupported op code or bad parameter."""

    opcode: int
    parameter: bytes


@dataclass(frozen=True)
class CommandRecord:
    command: Command | Unrecognized
    outcome: Outcome


@dataclass(frozen=True)
class ControlPointReply:
    """What follows a control point write: the response indication and maybe a status."""

    indication: bytes
    status: bytes | None


@dataclass(frozen=True)
class Notifications:
    indoor_bike_data: bytes
    cycling_power_measurement: bytes
    heart_rate_measurement: bytes


def advertisement(name: str) -> advertising.Advertisement:
    service_data = ftms.ServiceData(available=True, machine_types=ftms.MachineType.INDOOR_BIKE)
    return advertising.trainer_advertisement(
        name, ADVERTISED_SERVICES, uuids.FITNESS_MACHINE_SERVICE, service_data.encode()
    )


class GattFrontEnd:
    def __init__(
        self, backend: TrainerBackend, on_command: Callable[[CommandRecord], None] | None = None
    ) -> None:
        self._backend = backend
        self._on_command = on_command
        self._commands: list[CommandRecord] = []

    @property
    def commands(self) -> list[CommandRecord]:
        """Every control point write so far, oldest first, with what became of it."""
        return list(self._commands)

    def supported_power_range(self) -> bytes:
        power = self._backend.capabilities.power_w
        return ftms.SupportedPowerRange(
            round(power.minimum), round(power.maximum), round(power.step)
        ).encode()

    def supported_resistance_level_range(self) -> bytes:
        resistance = self._backend.capabilities.resistance_level
        return ftms.SupportedResistanceLevelRange(
            resistance.minimum, resistance.maximum, resistance.step
        ).encode()

    def notifications(self) -> Notifications:
        telemetry = self._backend.telemetry()
        power_w = round(_clamp(telemetry.power_w, -0x8000, 0x7FFF))
        crank_ticks = round(telemetry.last_crank_event_s * _CRANK_TICKS_PER_SECOND)
        return Notifications(
            indoor_bike_data=ftms.IndoorBikeData(
                instantaneous_speed_kmh=_clamp(telemetry.speed_mps * 3.6, 0, _UINT16_MAX / 100),
                instantaneous_cadence_rpm=_clamp(telemetry.cadence_rpm, 0, _UINT16_MAX / 2),
                total_distance_m=int(_clamp(telemetry.distance_m, 0, 0xFFFFFF)),
                instantaneous_power_w=power_w,
                elapsed_time_s=int(_clamp(telemetry.elapsed_s, 0, _UINT16_MAX)),
            ).encode(),
            cycling_power_measurement=cps.CyclingPowerMeasurement(
                instantaneous_power_w=power_w,
                # Both crank fields are free-running counters that wrap (CPS 3.2.1.6).
                crank_revolutions=cps.CrankRevolutions(
                    cumulative=telemetry.crank_revolutions % (_UINT16_MAX + 1),
                    last_event_time_s=crank_ticks % (_UINT16_MAX + 1) / _CRANK_TICKS_PER_SECOND,
                ),
            ).encode(),
            heart_rate_measurement=hrs.HeartRateMeasurement(
                round(_clamp(telemetry.heart_rate_bpm, 0, 0xFF))
            ).encode(),
        )

    def control_point_written(self, value: bytes) -> ControlPointReply:
        """Carry out one Fitness Machine Control Point write.

        An empty value has no op code to answer, so it raises ValueError instead.
        """
        try:
            request = ftms.decode_request(value)
        except ftms.InvalidParameterError as error:
            return self._refuse(error.opcode, bytes(value[1:]), Outcome.INVALID_PARAMETER)
        if isinstance(request, ftms.UnmodelledRequest):
            return self._refuse(request.opcode, request.parameter, Outcome.NOT_SUPPORTED)

        command = _command_for(request)
        outcome = self._backend.execute(command)
        self._record(CommandRecord(command, outcome))
        status = _status_for(request) if outcome is Outcome.SUCCESS else None
        return ControlPointReply(
            ftms.ControlPointResponse(request.OPCODE, _RESULT_CODES[outcome]).encode(),
            None if status is None else status.encode(),
        )

    def client_gone(self) -> None:
        self._backend.release_control()

    def _refuse(self, opcode: int, parameter: bytes, outcome: Outcome) -> ControlPointReply:
        self._record(CommandRecord(Unrecognized(opcode, parameter), outcome))
        return ControlPointReply(
            ftms.ControlPointResponse(opcode, _RESULT_CODES[outcome]).encode(), None
        )

    def _record(self, record: CommandRecord) -> None:
        self._commands.append(record)
        if self._on_command is not None:
            self._on_command(record)


def _command_for(request: ftms.ModelledRequest) -> Command:
    match request:
        case ftms.RequestControl():
            return RequestControl()
        case ftms.Reset():
            return Reset()
        case ftms.SetTargetResistanceLevel(level):
            return SetTargetResistance(level)
        case ftms.SetTargetPower(watts):
            return SetTargetPower(watts)
        case ftms.StartOrResume():
            return Start()
        case ftms.StopOrPause(action):
            return Stop() if action is ftms.StopOrPauseAction.STOP else Pause()
        case ftms.SetIndoorBikeSimulationParameters(parameters):
            return SetSimulation(
                grade_percent=parameters.grade_percent,
                wind_speed_mps=parameters.wind_speed_mps,
                crr=parameters.crr,
                cw_kg_per_m=parameters.cw_kg_per_m,
            )


def _status_for(request: ftms.ModelledRequest) -> ftms.Status | None:
    """The Fitness Machine Status that announces a request that succeeded (FTMS 4.17)."""
    match request:
        case ftms.RequestControl():
            return None
        case ftms.Reset():
            return ftms.MachineReset()
        case ftms.SetTargetResistanceLevel(level):
            return ftms.TargetResistanceLevelChanged(level)
        case ftms.SetTargetPower(watts):
            return ftms.TargetPowerChanged(watts)
        case ftms.StartOrResume():
            return ftms.StartedOrResumedByUser()
        case ftms.StopOrPause(action):
            return ftms.StoppedOrPausedByUser(action)
        case ftms.SetIndoorBikeSimulationParameters(parameters):
            return ftms.IndoorBikeSimulationParametersChanged(parameters)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
