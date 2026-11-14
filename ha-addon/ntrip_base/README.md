# NTRIP Base Station app (Home Assistant OS)

Home Assistant calls add-ons "apps" from 2026.2 on.

Relays RTCM3 corrections from a USB GNSS base receiver (e.g. SparkFun LG290P
breakout) to an NTRIP caster on your LAN, using RTKLIB `str2str`
(2.4.3 b34, Ubuntu 24.04 package).

## Install

1. Install the **Samba share** app so you can reach the `local_apps` share on the
   Home Assistant box. The older `addons` share is the same folder.
2. Copy this whole `ntrip_base` folder there. Keep the LF line endings in `run.sh`.
3. Settings → Apps → Install app → ⋮ (top right) → **Check for updates**.
   *NTRIP Base Station* appears under local apps. If it doesn't, hard-refresh the
   page and repeat.
4. Install it. The first install builds the image on the Odroid, which takes a
   few minutes.
5. Plug the receiver into USB. Open the add-on's **Configuration** tab and pick
   the receiver under `device`. The `/dev/serial/by-id/...` entries are stable
   across reboots. The CH342 on the LG290P breakout shows up as two ports; use
   the one wired to the UART where you enabled RTCM output.
6. Set `password`, using letters, digits and underscores only. Then start the
   add-on and check the **Log** tab. Once a client connects, the status line
   should show bytes flowing, e.g. `[CC---] ... bps`.

## Options

| option | default | meaning |
|---|---|---|
| device | — | serial device of the base receiver |
| baud | 460800 | receiver UART baud (LG290P default 460800) |
| mountpoint | BASE | NTRIP mountpoint name |
| username / password | rover / changeme | NTRIP login (letters, digits, `_`) |
| extra_output | empty | optional second str2str output, e.g. `ntrips://:PASS@rtk2go.com:2101/YOUR_MOUNT` to also push to RTK2go |
| control | false | open TCP port 2102 as a two-way pipe to the receiver, for setup from another computer |

## Configuring the receiver over the network

With `control` on, anything sent to TCP port 2102 goes to the receiver, and everything the
receiver outputs comes back. Tools such as PyGPSClient can connect to it as a socket. The
caster keeps running at the same time.

The port has no password, so anyone on your network can reconfigure the receiver. Turn
`control` off when you finish. Do not update firmware through it, because a dropped
connection during a flash can leave the receiver unusable.

## SW Maps (rover side)

NTRIP client → Address: Home Assistant IP · Port: 2101 · Mountpoint: BASE ·
Username/password as above · NTRIP V1 or V2.

## Notes

- The receiver must already be configured as a base, with RTCM3 output on the
  chosen UART. This add-on only relays bytes; it does not configure the
  receiver.
- For an F9P rover (SparkFun RTK Express), enable MSM4 or MSM7 for GPS,
  GLONASS, Galileo and BeiDou, plus messages 1005 and 1230. Disable NMEA on
  that UART so only RTCM goes out.
- Port 2101 is exposed only on the local network. Don't forward it on your
  router.
