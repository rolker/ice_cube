package rink.icecube.logger;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

/**
 * The link and session actions both screens use (map and Details): connect and disconnect
 * the GPS and the caster, start and stop the session, the launch autostart options, and
 * the runtime permission requests (notifications, Bluetooth, USB device access).
 */
final class Control {
    private Control() { }

    private static String usbAsked;                // device name we already asked USB permission for
    static boolean gpsAutostartPending;            // waiting for the Bluetooth permission answer

    static void send(Context c, String action, boolean wait) {
        c.startForegroundService(new Intent(c, LoggerService.class).setAction(action)
                .putExtra(LoggerService.EXTRA_WAIT, wait));
    }

    /** The service if it has a session or a link running, else null. */
    static LoggerService live() {
        LoggerService s = LoggerService.current;
        return s != null && (s.sessionOpen() || s.gpsActive() || s.caster() != null) ? s : null;
    }

    static boolean bluetoothAllowed(Context c) {
        return Build.VERSION.SDK_INT < 31
                || c.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    static void requestRuntimePermissions(Activity a) {
        List<String> want = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33
                && a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            want.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (Build.VERSION.SDK_INT >= 31 && !bluetoothAllowed(a)) want.add(Manifest.permission.BLUETOOTH_CONNECT);
        if (!want.isEmpty()) a.requestPermissions(want.toArray(new String[0]), 1);
    }

    /** Call from onRequestPermissionsResult: finishes a Bluetooth autostart that waited for the permission. */
    static void permissionsAnswered(Activity a) {
        if (gpsAutostartPending && bluetoothAllowed(a)) {
            gpsAutostartPending = false;
            send(a, LoggerService.ACTION_GPS_CONNECT, true);
        }
    }

    /** The two launch options from Settings. A USB re-attach while open only restarts the GPS. */
    static void autostart(Activity a, Intent launch, boolean appLaunch) {
        SharedPreferences p = Settings.prefs(a);
        LoggerService s = LoggerService.current;
        boolean usbLaunch = launch != null && UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(launch.getAction());
        if (appLaunch && p.getBoolean(Settings.CASTER_AT_LAUNCH, false)
                && !p.getString(Settings.HOST, "").trim().isEmpty() && (s == null || s.caster() == null)) {
            send(a, LoggerService.ACTION_CASTER_CONNECT, false);
        }
        if (!p.getBoolean(Settings.LOG_ON_GPS_CONNECT, false) || (s != null && s.gpsActive())) return;
        boolean bt = Settings.bluetooth(p);
        if (bt && !usbLaunch) {
            if (bluetoothAllowed(a)) send(a, LoggerService.ACTION_GPS_CONNECT, true);
            else gpsAutostartPending = true;
        } else if (!bt && usbLaunch) {
            send(a, LoggerService.ACTION_GPS_CONNECT, true);
        }
    }

    /** Connects the GPS. Returns a message when it cannot (Bluetooth permission missing), else null. */
    static String connectGps(Activity a) {
        if (Settings.bluetooth(Settings.prefs(a)) && !bluetoothAllowed(a)) {
            requestRuntimePermissions(a);
            return "Allow Bluetooth (Nearby devices) first";
        }
        usbAsked = null;
        send(a, LoggerService.ACTION_GPS_CONNECT, false);
        return null;
    }

    static void disconnectGps(Context c) { send(c, LoggerService.ACTION_GPS_DISCONNECT, false); }

    /** Connects the caster, or opens Settings when no caster is set. */
    static void connectCaster(Activity a) {
        if (Settings.prefs(a).getString(Settings.HOST, "").trim().isEmpty()) {
            a.startActivity(new Intent(a, SettingsActivity.class));
            return;
        }
        send(a, LoggerService.ACTION_CASTER_CONNECT, false);
    }

    static void disconnectCaster(Context c) { send(c, LoggerService.ACTION_CASTER_DISCONNECT, false); }

    static void startSession(Context c) { send(c, LoggerService.ACTION_START_SESSION, false); }

    /** Stops the session and both links. */
    static void stopAll(Context c) { send(c, LoggerService.ACTION_STOP, false); }

    /** Asks once per attached device for USB permission while the service waits for it. */
    static void askUsbPermissionIfNeeded(Activity a) {
        LoggerService s = live();
        if (s == null || !s.usbNeedsPermission()) return;
        UsbManager m = (UsbManager) a.getSystemService(Context.USB_SERVICE);
        UsbDevice d = UsbLink.findGps(m);
        if (d == null || m.hasPermission(d) || d.getDeviceName().equals(usbAsked)) return;
        usbAsked = d.getDeviceName();
        Intent i = new Intent("rink.icecube.logger.USB_PERMISSION").setPackage(a.getPackageName());
        m.requestPermission(d, PendingIntent.getBroadcast(a, 0, i, PendingIntent.FLAG_MUTABLE));
    }
}
