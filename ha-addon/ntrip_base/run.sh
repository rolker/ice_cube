#!/usr/bin/env bash
# Relay RTCM3 from a USB/serial GNSS base receiver to an NTRIP caster on port 2101.
set -uo pipefail
OPTS="${OPTIONS_FILE:-/data/options.json}"
opt() { jq -r "$1 // empty" "$OPTS"; }

DEV="$(opt .device)"; BAUD="$(opt .baud)"; MNT="$(opt .mountpoint)"
USR="$(opt .username)"; PWD_="$(opt .password)"; EXTRA="$(opt .extra_output)"; CONTROL="$(opt .control)"

if [ -z "$DEV" ]; then echo "[ntrip_base] No serial device selected - set 'device' in the add-on configuration."; exit 1; fi
# RTKLIB prepends /dev/ to serial port names on Linux
PORT="${DEV#/dev/}"
IN="serial://${PORT}:${BAUD}:8:n:1:off"
OUT=( -out "ntripc://${USR}:${PWD_}@:2101/${MNT}" )
[ -n "$EXTRA" ] && OUT+=( -out "$EXTRA" )
BACK=()
if [ "$CONTROL" = "true" ]; then
  # Two-way pipe to the receiver: -b sends what arrives on this output back to the input.
  OUT+=( -out "tcpsvr://:2102" )
  BACK=( -b "$(( ${#OUT[@]} / 2 ))" )
fi

echo "[ntrip_base] input : $DEV @ ${BAUD} baud"
echo "[ntrip_base] caster: port 2101, mountpoint ${MNT}, user ${USR}"
[ -n "$EXTRA" ] && echo "[ntrip_base] extra output configured"
[ "$CONTROL" = "true" ] && echo "[ntrip_base] WARNING: control port 2102 is on, with no password. Turn it off after setup."

while true; do
  ${STR2STR:-str2str} -in "$IN" "${OUT[@]}" "${BACK[@]}" -s 10000 -r 5000
  echo "[ntrip_base] str2str exited (code $?) - restarting in 5 s"
  sleep 5
done
