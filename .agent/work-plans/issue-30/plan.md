# Plan: Logger notification and service lifetime when only viewing the map (#30)

## Issue

https://github.com/rolker/ice_cube/issues/30

Stacked on #28 (`feature/issue-24`, logger 0.8.0); this is logger 0.8.1.

## Context

Opening the logger with the rover off leaves the ongoing "Logging GPS, corrections and
sensors" notification on. `Control.autostart` sends `ACTION_GPS_CONNECT` with `wait = true`
(Log on GPS connect) and, with Caster at launch, connects the caster, which opens a session
(`startCaster` calls `ensureSession`). `LoggerService.stopIfIdle()` never stops while a GPS
worker or caster exists, the wait has no end, and the notification text is fixed.

## Approach

1. **`core/ServiceState` (pure Java).** A snapshot of the service (session name or none,
   GPS active / connected / waiting for the rover, caster active / connected) with
   `text()` for the notification, `idle()`, `waitExpired(startMs, nowMs, timeoutMs)` (only
   with no session) and `stopOnTaskRemoved()` (no session). Unit-tested.
2. **Honest notification.** `LoggerService` builds it from the snapshot ("Waiting for the
   rover…", "Caster connected, no GPS · session …", "Logging session …") with a Stop
   action (`ACTION_STOP`) only while no session is open (owner's decision on PR #31: a
   session ends only with REC and its confirmation). A 2 s main-thread
   tick updates the text when it changes and ends an expired wait. Every
   `onStartCommand` still calls `startForeground` with the connectedDevice type first.
3. **Bounded wait.** A GPS started with `wait` keeps its start time and the timeout from
   the new setting (GPS link > "Wait for the rover", min, 0.5 to 60, default 2, a
   `core.Limit`). With no session and no link yet when it expires, the GPS stops and the
   service stops if idle.
4. **Task removed.** `onTaskRemoved` with no session stops the links and the service; with
   a session nothing changes.
5. **Caster follows the GPS.** The Caster at launch setting (same key) becomes "Connect
   caster when the GPS connects": the service starts the caster when a GPS worker first
   connects, if none runs and a host is set. `Control.autostart` no longer connects the
   caster, so map-only use starts nothing unless Log on GPS connect waits for the rover.
6. **App version in the UI.** "Ice Cube Logger 0.8.1 (build 12)" at the top of Settings
   and at the bottom of the MENU panel.
7. **Version and docs.** 0.8.1, versionCode 11; README (Settings, Use, notification).

## Files to Change

- `logger/app/src/main/java/rink/icecube/logger/core/ServiceState.java` (new), `core/Limit.java`
- `logger/app/src/main/java/rink/icecube/logger/LoggerService.java`, `Control.java`,
  `Settings.java`, `SettingsActivity.java`, `MainActivity.java`
- `logger/app/src/test/java/rink/icecube/logger/core/ServiceStateTest.java` (new), `LimitTest.java`
- `logger/app/build.gradle`, `logger/README.md`

## Tests

Unit: text per state (waiting, GPS connected without session, caster only, logging with
each link combination, session with no links), idle, wait expiry before/at/after the
timeout and never with a session or once connected, task removal with and without a
session, the new Limit. On the phone: map-only launch (no notification, or it ends after
the wait), Stop action, swipe the app away with and without a session.

## Risks

- The wait also ends while the app is in front; the user reconnects with the GPS dot.
