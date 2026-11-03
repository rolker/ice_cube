# RTK base station

## Why

Surveys of the yard on MaCORS corrections showed heights drifting by about 8 cm over minutes
while the rover reported RTK fixed with about 1 cm accuracy. Epping is outside the northern
edge of the network, and the nearest station is about 25 km away. Details are in
rtk-test-2026-10-26.md.

A base in the yard shortens the baseline to tens of metres, so the atmospheric errors behind
that drift should largely cancel. This has not been tested yet.

## Hardware

Ordered from SparkFun on 2026-10-26:

- Quadband GNSS RTK Breakout, LG290P (GPS-26620)
- L1/L2 multi-band magnetic mount antenna, 5 m cable, SMA (GPS-15192)
- GPS antenna ground plate (GPS-17519)

Already on hand:

- Rover: SparkFun RTK Express (u-blox ZED-F9P, L1/L2), Bluetooth to SW Maps on a phone
- Server: Odroid running Home Assistant OS
- Optional: standard SMA male-to-female RG58 extension, if the Odroid is more than 5 m away.
  Avoid RP-SMA. 35 ft of RG58 was estimated at 12-13 dB total loss including the antenna lead.

The board has three UARTs: USB-C through a CH342 bridge, a 4-pin locking JST, and a
BlueSMiRF header. Qwiic, I2C and SPI are marked under development, so use UART.
The CH342 appears as two USB serial ports.

## How it fits together

```
antenna -> LG290P -> USB -> Odroid (ntrip_base add-on, port 2101) -> Wi-Fi -> phone (SW Maps) -> RTK Express
```

`ha-addon/ntrip_base/` is the Home Assistant add-on. It relays bytes only, so the receiver has
to be configured as a base first. The add-on's server and start-up script were tested with a
simulated serial port, but it has not yet been built on the Odroid. If the ARM build
fails, the fallback is compiling RTKLIB inside the add-on.

## Key idea

The rink needs surfaces that are comparable from survey to survey, more than it needs
absolute accuracy. An error in the base coordinate shifts every rover point by the same
amount, so it cancels when surveys are differenced. Moving the antenna or changing the
coordinate does not cancel.

So: a rigid mount first, and one coordinate kept for the whole season.

## Setup

### 1. Mount the antenna

- Open sky, away from walls and metal, on something that will not shift with frost heave
  or snow load. A roof or a concrete-footed post beats a stake in soil.
- Center the antenna on the ground plate, and fasten it so it cannot be bumped or slide.
- Seal any outdoor cable joint with self-amalgamating tape or a small box.
- Record the antenna reference point height.

### 2. Configure the receiver

Connect the board to a computer over USB-C, using a data cable, and confirm a normal position
fix before changing anything. **On this WSL2 machine**, USB serial devices are not visible in
Linux without `usbipd`, so run Quectel QGNSS or PyGPSClient on Windows.

Commands as reported in SparkFun and Quectel forum posts. Check them against Quectel's
LG290P Base Station Mode application note and protocol spec. The checksum after `*` is required.

```
$PQTMCFGRCVRMODE,W,2*29                  base mode
$PQTMSAVEPAR*5A                          save to flash
$PQTMSRR*4B                              reboot
$PQTMCFGSVIN,W,1,3600,1.2,0,0,0*0A       survey-in: 1 hour, 1.2 m limit
```

The F9P rover needs MSM4 or MSM7 for GPS, GLONASS, Galileo and BeiDou, plus 1005 (base position)
and 1230 (GLONASS biases), with NMEA off on the port the add-on reads. Reported behavior:
base mode turns NMEA off and turns on MSM4 and 1005, so the port looks like gibberish after
reboot, which is expected. Confirm 1230 and all four constellations are actually being sent.
MSM7 may not be selectable on all firmware.

When the survey-in finishes, copy the ECEF result into fixed mode (`PQTMCFGSVIN,W,2,...`) and
save. Keep the coordinate only in `private/`.

### 3. Install the add-on

Follow `ha-addon/ntrip_base/README.md`. Set a real password, and do not forward port 2101.
The README default of 460800 baud is the expected LG290P rate.

### 4. Point the rover at it

SW Maps NTRIP client: Odroid IP, port 2101, mountpoint `BASE`. Set Minimum Fix Quality to
RTK Fix. Check that the stronger satellites show C/N0 in the mid-40s dB-Hz or better.
If Wi-Fi does not reach the yard, use the add-on's `extra_output` to push to RTK2go and
pull the stream over cellular.

### 5. Prove it works

Repeat the October drift test on the deck-corner mark: log tracks at 1 Hz for about 20 minutes.
The test passes if height stays within about 2 cm with no slow wander.

Then re-shoot the deck step and other fixed marks. That gives the offset between the base's
coordinates and the earlier MaCORS surveys, which are in NAD83(2011), so old and new
data can be compared.

## Survey practice

- Export CSV from SW Maps. It has time, fix type, accuracy, DOP and satellites for each shot.
  GeoJSON drops all of that. Keep the SWMZ as a backup.
- Re-shoot a fixed mark with a consistent `CHECK` remark at the start, middle and end. On
  MaCORS, do it every 2-3 minutes.
- Discard the first minute after switching correction source.
- Shoot the rink area twice, at least 15 minutes apart.

## Better coordinate, later

Survey-in gives an approximate position, which is fine for relative work. To tie it into a
real frame, log raw data and submit it to OPUS or a PPP service such as CSRS-PPP. Reports vary
from 1-2 hours to 24 hours of data. RTKLIB's `convbin` converts logs to RINEX.

Choose before the first ice survey, then leave it alone all season.

## MaCORS outages

Planned firewall maintenance, 30 minutes each: Tue 2026-11-03 06:00 EST, Thu 2026-11-05 11:30 EST.

## Sources

- [SparkFun LG290P product page](https://www.sparkfun.com/sparkfun-quadband-gnss-rtk-breakout-lg290p-qwiic.html)
- [SparkFun forum: LG290P base station build](https://community.sparkfun.com/t/question-about-lg290p-rtk-breakout-base-station-build/68706)
- [SparkFun PQTM command notes](https://docs.sparkfun.com/SparkFun_RTK_Postcard/pqmt_commands/)
- [LG290P wiki notes](https://wiki.millerjs.org/quectel_lg290p)
- [Quectel forum: survey-in workflow](https://forums.quectel.com/t/lg290p-correct-workflow-to-survey-in/42964)
- [Quectel forum: base mode behavior](https://forums.quectel.com/t/lg290p-questions-about-its-behaviour-in-base-station-mode/38843)
