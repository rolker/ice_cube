# Ice Cube Logger 0.1

Records the RTK Express GPS over USB, runs its own NTRIP client, and logs phone
accelerometer, gravity and gyroscope data on the same clock. It has no map yet.

## Build
1. Open this folder in Android Studio and let it sync.
2. Turn on USB debugging on the phone and press Run.

## Use
1. Connect the phone to the GPS USB port with an OTG cable. Do not use the ESP32 port.
2. Close SW Maps first, because only one app can use the connection.
3. Enter the same NTRIP host, port, mount point, user and password you use in SW Maps.
4. Press Start and allow USB access.
5. Check that "config acks" reads 1 and naks reads 0, then wait for RTK FIXED.
6. Press Stop when finished.

## Files
Each session is a folder in `Android/media/rink.icecube.logger/sessions/`:
- `gps.ubx`: every byte from the receiver, unchanged
- `usb_chunks.csv`: phone time and byte offset of each USB read, which ties GPS data to phone time
- `imu.csv`: acc, grav and gyro samples (m/s², rad/s)
- `events.csv`: start and stop, control marks, and final loss counts

The app changes receiver settings in RAM only. A power cycle restores your saved settings.
