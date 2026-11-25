# What the trainer says on the air

This is the reference for anyone pointing an app at it: what it exposes, how it answers, and
the places where I had to choose between sources that disagree.

Implemented from the Bluetooth SIG documents: Fitness Machine Service 1.0, Cycling Power Service
1.1, Heart Rate Service 1.0, the GATT Specification Supplement (version of 2026-09-09) and the
Assigned Numbers. Section numbers below refer to those.

## Advertising

Legacy advertising PDUs, connectable, every 100 ms, from a static random address (default
`C0:FF:EE:00:00:01`).

| AD type | Value |
| --- | --- |
| Flags | LE General Discoverable Mode, BR/EDR Not Supported |
| Complete List of 16-bit Service UUIDs | `1826` Fitness Machine, `1818` Cycling Power, `180D` Heart Rate |
| Service Data, UUID `1826` | Fitness Machine Available, type Indoor Bike (`01 20 00`, FTMS 3.1) |
| Complete Local Name | `PhantomBike` |

Everything a scanner filters on is in the advertising data, not in the scan response: a passive
scanner never asks for one, and Bumble's simulated link does not deliver one. That leaves 11
bytes for the name, which is why the default is `PhantomBike` and not the project's name. A
longer `--name` is advertised shortened (AD type Shortened Local Name) and sent complete in the
scan response and in the GAP Device Name.

## Services

| Service | Characteristic | Properties | Content |
| --- | --- | --- | --- |
| Fitness Machine `1826` | Fitness Machine Feature `2ACC` | read | cadence, total distance, elapsed time, power measurement; targets: resistance, power, indoor bike simulation |
| | Indoor Bike Data `2AD2` | notify | speed, cadence, total distance, power, elapsed time |
| | Supported Resistance Level Range `2AD6` | read | 0 to 20, step 0.1 |
| | Supported Power Range `2AD8` | read | 0 to 2000 W, step 1 W |
| | Fitness Machine Control Point `2AD9` | write, indicate | see below |
| | Fitness Machine Status `2ADA` | notify | see below |
| Cycling Power `1818` | Cycling Power Measurement `2A63` | notify | power, crank revolution data |
| | Cycling Power Feature `2A65` | read | crank revolution data, not for use in a distributed system |
| | Sensor Location `2A5D` | read | rear hub |
| Heart Rate `180D` | Heart Rate Measurement `2A37` | notify | heart rate, 8 bit |
| | Body Sensor Location `2A38` | read | chest |
| Device Information `180A` | manufacturer, model, software revision | read | `phantomwatts`, `Virtual indoor bike`, the package version |

Generic Access and Generic Attribute are Bumble's defaults. Notifications go out once per
`--interval`, one second by default, which is what both FTMS (4.9.1) and CPS (3.2.1) suggest.

The ATT MTU stays at the default of 23, whatever the client asks for. Bumble's virtual controller
announces 27-byte LE buffers and does not fragment what it hands to its host, so this keeps
every packet within the size it announced. Nothing here needs more: the longest notification is
13 bytes.

## Control point

| Op code | Request | Answer |
| --- | --- | --- |
| `00` | Request Control | success, always |
| `01` | Reset | success; back to free ride, session stopped, counters zeroed, control permission ended |
| `04` | Set Target Resistance Level | success inside the supported range, else invalid parameter |
| `05` | Set Target Power | success inside the supported range, else invalid parameter |
| `07` | Start or Resume | success; operation failed when the session is already started |
| `08` | Stop or Pause | success; operation failed when there is nothing to stop or pause |
| `11` | Set Indoor Bike Simulation Parameters | success |
| anything else | | op code not supported |

- Every request except Request Control answers control not permitted until control has been
  requested (4.16.2). Reset ends the permission again, as 4.16.2.1 says, and so does a
  disconnection.
- A known op code with a parameter of the wrong length or a reserved value answers invalid
  parameter.
- A write before the control point's indications are enabled is rejected with the ATT error
  Client Characteristic Configuration Descriptor Improperly Configured, `0xFD` (4.16.3). An
  empty write is rejected with Invalid Attribute Value Length.
- The response is indicated after the Write Response. A request that succeeded and changed
  something is followed by a Fitness Machine Status notification: reset `01`, stopped or paused
  `02`, started or resumed `04`, target resistance level changed `07`, target power changed
  `08`, simulation parameters changed `12`.

## What the modes do

The rider pedals as the scenario says, whatever the app does.

- Free ride: power is the rider's, speed is what that power gives on a flat road.
- ERG (target power): power moves to the target with a time constant of 2 s, as long as the
  cadence is not zero.
- Simulation: power stays the rider's, speed follows from grade, wind, Crr and Cw.
- Resistance: power stays the rider's; one resistance level slows the rider like one percent
  of grade. This is my simplification, a resistance level has no unit.

The training session (start, stop, pause) only gates the session counters: elapsed time and
total distance grow while it is started, and Reset zeroes them (4.16.2.2, 4.16.2.8). Power,
cadence and speed are reported in every state, as a real trainer does when nobody pressed start.

## Where I did not follow a document to the letter

1. **No encryption on the control point.** FTMS Table 4.1 lists "Encryption" as its security
   permission. As far as I know trainers in the field accept unencrypted writes, since apps
   connect to them without pairing; a trainer that demanded encryption would not behave like
   the ones it stands in for. I have not measured this on a real trainer.
2. **Supported Resistance Level Range is six bytes**: sint16, sint16, uint16 at a resolution of
   0.1. That is the layout pycycling, a client library written against real trainers, parses,
   and to my knowledge the one the characteristic had when FTMS 1.0 came out. The current GATT
   Specification Supplement (3.231) lists three uint8 fields instead. The control point
   parameter is a uint8 at 0.1 either way (FTMS Table 4.15).
3. **Indoor Bike Data carries no Resistance Level field.** The same two sources disagree on its
   size (two bytes against one), and a field of the wrong size shifts every field after it,
   power included. The codec can read and write the two-byte form; the trainer does not send it.
4. **No wheel revolution data in the Cycling Power Measurement.** Supporting it makes the
   Cycling Power Control Point mandatory (CPS 3.4), which this trainer does not have. Speed is
   in the Indoor Bike Data.
5. **Status notifications also go to the client that caused them.** 4.17.1 only requires them
   for the other connected clients. Sending them to every subscriber is simpler and, as far as
   I know, what trainers do.
6. **Procedure Already In Progress (`0xFE`) is never returned.** Every request completes before
   the next one can arrive, so there is never a procedure in progress to collide with.
7. **Drag and wind.** FTMS names the simulation parameters without giving a formula. I use
   drag = Cw / 2 x airspeed squared and a positive wind speed as a headwind. With the Cw of 0.51
   kg/m that apps usually send, 200 W on the flat is 31.5 km/h for an 84 kg rider and bike.

Heart rate is a first order lag towards a value that depends on power. It is plausible, not
physiology.

## How the codecs are checked

- Table tests with bytes worked out by hand from the specifications, in both directions.
- Against pycycling's parsers, which are written to talk to real trainers, and against Bumble's
  own Heart Rate profile (`tests/test_oracles.py`). Where pycycling departs from the documents
  the comparison says so: it scales Crr by 1000 instead of 10000, and its Cycling Power Feature
  table leaves out two features, which shifts its bits from 5 up.
- End to end, by a central that shares only the codecs with the trainer.

No real trainer and no real app has been compared against it yet.
