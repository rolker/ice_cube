package rink.icecube.logger;

import android.content.Context;
import android.content.SharedPreferences;

/** SharedPreferences keys and defaults shared by the screens and the service. */
final class Settings {
    static final String HOST = "host", PORT = "port", MOUNT = "mount", USER = "user", PASS = "pass";
    static final String LOG_CORRECTIONS = "log_corrections", CASTER_AT_LAUNCH = "caster_at_launch";
    static final String TRANSPORT = "transport", BT_ADDRESS = "bt_address", BT_NAME = "bt_name";
    static final String CONFIGURE_USB = "configure_usb", CONFIGURE_BT = "configure_bt";
    static final String LOG_ON_GPS_CONNECT = "log_on_gps_connect";
    static final String USB = "usb", BLUETOOTH = "bluetooth";

    private Settings() { }

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("settings", Context.MODE_PRIVATE); }

    static boolean bluetooth(SharedPreferences p) { return BLUETOOTH.equals(p.getString(TRANSPORT, BLUETOOTH)); }

    /** Sending the UBX configuration defaults to on for USB and off for Bluetooth. */
    static boolean configure(SharedPreferences p, boolean bluetooth) {
        return bluetooth ? p.getBoolean(CONFIGURE_BT, false) : p.getBoolean(CONFIGURE_USB, true);
    }

    static boolean logCorrections(SharedPreferences p) { return p.getBoolean(LOG_CORRECTIONS, true); }

    static int port(SharedPreferences p) {
        try { return Integer.parseInt(p.getString(PORT, "2101").trim()); }
        catch (NumberFormatException e) { return 2101; }
    }
}
