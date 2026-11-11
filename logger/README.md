# Ice Cube Logger 0.2

Records the RTK Express GPS over Bluetooth or USB, runs its own NTRIP client, and logs
phone accelerometer, gravity and gyroscope data, all on one phone clock. It has no map.

## Build
`./gradlew assembleDebug test lint` (needs the Android SDK; set `sdk.dir` in
`local.properties`). The APK is `app/build/outputs/apk/debug/app-debug.apk`. Or open this
folder in Android Studio and press Run.

## Settings
Press Settings on the main screen. Changes apply at the next Connect.
- Caster: host, port, mount point, user, password (shown as dots), Log corrections,
  Connect caster at app launch.
- GPS link: USB or Bluetooth. For Bluetooth, pick the receiver from the paired devices;
  pair it in Android Bluetooth settings first. Configure receiver (RAM only) is on by
  default for USB and off for Bluetooth, because the RTK Express firmware already sets up
  the Bluetooth port. Start logging when the GPS connects starts a session on its own: for
  USB when Android opens the app on attach, for Bluetooth when the receiver connects after
  the app is opened.

## Use
1. Close SW Maps first: only one app can hold the receiver's Bluetooth or USB link.
2. For USB, connect to the GPS USB port with an OTG cable, not the ESP32 port, and allow
   USB access.
3. Press Connect GPS and/or Connect caster. Either one starts a session. The caster works
   without the GPS; corrections go to the GPS whenever both are connected, and the GGA
   from the GPS goes to the caster.
4. Mark control point writes a mark to the session.
5. Press Stop session when finished. If a link drops, the app logs it and reconnects
   (Bluetooth every 3 to 30 s, USB on re-attach, caster every 5 to 60 s).

## Files
Each session is a folder in `Documents/IceCubeLogger/sessions/`, which survives uninstall.
(On Android 10 and older, or if Documents cannot be written, it is
`Android/media/rink.icecube.logger/sessions/` instead, and `events.csv` says so.) The
folder path is on the main screen.
- `gps.ubx`: every byte from the receiver, unchanged and in read order
- `gps_chunks.csv`: `elapsed_ns,byte_offset,length` for each read, for USB or Bluetooth
- `corrections.rtcm`: every byte from the caster, unchanged (when Log corrections is on)
- `ntrip_chunks.csv`: `elapsed_ns,byte_offset,length` for each caster read
- `imu.csv`: acc, grav and gyro samples (m/s², rad/s); times are sensor event times
- `events.csv`: session start and stop, links connected and lost (with transport and
  device), retries, control marks, and final counts

`elapsed_ns` is `SystemClock.elapsedRealtimeNanos()`. Offsets keep counting across link
drops. The app changes receiver settings in RAM only, and only when Configure is on.
A power cycle restores the receiver's saved settings.
