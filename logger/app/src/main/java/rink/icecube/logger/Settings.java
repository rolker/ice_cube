package rink.icecube.logger;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

import rink.icecube.logger.core.Limit;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.ReferenceFrame;
import rink.icecube.logger.core.SurveyLimits;
import rink.icecube.logger.core.SurveyMode;
import rink.icecube.logger.core.TiltSet;

/** SharedPreferences keys and defaults shared by the screens and the service. */
final class Settings {
    static final String HOST = "host", PORT = "port", MOUNT = "mount", USER = "user", PASS = "pass";
    static final String LOG_CORRECTIONS = "log_corrections", CASTER_AT_LAUNCH = "caster_at_launch";
    static final String TRANSPORT = "transport", BT_ADDRESS = "bt_address", BT_NAME = "bt_name";
    static final String CONFIGURE_USB = "configure_usb", CONFIGURE_BT = "configure_bt";
    static final String LOG_ON_GPS_CONNECT = "log_on_gps_connect";
    static final String USB = "usb", BLUETOOTH = "bluetooth";
    static final String POLE_HEIGHT = "pole_height";      // metres as text, PoleHeight.format
    static final String LAYERS_OFF = "layers_off";        // set of layer file names switched off
    static final String FRAME = "frame", FRAME_EPOCH = "frame_epoch";   // caster's frame when the sourcetable has none
    static final String ORIENTATION = "orientation";      // core.ScreenOrientation key, default auto
    static final String MODE = "survey_mode";             // core.SurveyMode key, default surface
    static final String REF_SECONDS = "ref_seconds";      // text, whole seconds 10 to 300, default 30
    static final String POLE_TOLERANCE_CM = "pole_tolerance_cm";   // text, cm 0.5 to 20, default 3
    static final String VOLUME_KEYS = "volume_keys", SOUND = "sound", VIBRATION = "vibration";   // default on
    static final String SHOW_EARLIER_REFS = "show_earlier_refs";   // default off
    static final String LABEL_CAPTURES = "label_captures";         // default on
    // 0.6, all text checked by a core.Limit: false-fix guard and tilt capture gates
    static final String SETTLE_S = "fix_settle_s", JUMP_M = "fix_jump_m";
    static final String TILT_COVER_DEG = "tilt_cover_deg", TILT_RMS_CM = "tilt_rms_cm", TILT_MIN_DEG = "tilt_min_deg",
            TILT_MAX_DEG = "tilt_max_deg", TILT_SIGMA_CM = "tilt_sigma_cm", TILT_IMU_DEG = "tilt_imu_deg",
            LEAN_SCATTER_CM = "lean_scatter_cm";
    /** The 0.6 numeric settings: key and its limit, in Settings screen order. */
    static final String[] LIMIT_KEYS = {SETTLE_S, JUMP_M, TILT_COVER_DEG, TILT_RMS_CM, TILT_MIN_DEG, TILT_MAX_DEG,
            TILT_SIGMA_CM, TILT_IMU_DEG, LEAN_SCATTER_CM};
    static final Limit[] LIMITS = {Limit.SETTLE_S, Limit.JUMP_M, Limit.TILT_COVER_DEG, Limit.TILT_RMS_CM,
            Limit.TILT_MIN_DEG, Limit.TILT_MAX_DEG, Limit.TILT_SIGMA_CM, Limit.TILT_IMU_DEG, Limit.LEAN_SCATTER_CM};

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

    static double poleHeight(SharedPreferences p) {
        return PoleHeight.parseOrDefault(p.getString(POLE_HEIGHT, PoleHeight.format(PoleHeight.DEFAULT)));
    }

    static Set<String> layersOff(SharedPreferences p) {
        return new HashSet<>(p.getStringSet(LAYERS_OFF, Collections.<String>emptySet()));
    }

    static void setLayerOn(SharedPreferences p, String file, boolean on) {
        Set<String> off = layersOff(p);
        if (on) off.remove(file); else off.add(file);
        p.edit().putStringSet(LAYERS_OFF, off).apply();
    }

    /** The Frame setting, or null for Unknown. An invalid epoch counts as none. */
    static ReferenceFrame frame(SharedPreferences p) {
        double epoch;
        try { epoch = ReferenceFrame.parseEpoch(p.getString(FRAME_EPOCH, "")); }
        catch (IllegalArgumentException e) { epoch = Double.NaN; }
        return ReferenceFrame.fromSetting(p.getString(FRAME, ReferenceFrame.SETTING_NAMES[0]), epoch);
    }

    static SurveyMode mode(SharedPreferences p) { return SurveyMode.fromKey(p.getString(MODE, SurveyMode.SURFACE.key)); }

    static int refSeconds(SharedPreferences p) {
        return SurveyLimits.refSecondsOrDefault(p.getString(REF_SECONDS, String.valueOf(SurveyLimits.REF_SECONDS_DEFAULT)));
    }

    /** Pole check tolerance in metres. */
    static double poleTolerance(SharedPreferences p) {
        return SurveyLimits.toleranceCmOrDefault(p.getString(POLE_TOLERANCE_CM, "3")) / 100;
    }

    static boolean volumeKeys(SharedPreferences p) { return p.getBoolean(VOLUME_KEYS, true); }
    static boolean sound(SharedPreferences p) { return p.getBoolean(SOUND, true); }
    static boolean vibration(SharedPreferences p) { return p.getBoolean(VIBRATION, true); }
    static boolean showEarlierRefs(SharedPreferences p) { return p.getBoolean(SHOW_EARLIER_REFS, false); }
    static boolean labelCaptures(SharedPreferences p) { return p.getBoolean(LABEL_CAPTURES, true); }

    static double limit(SharedPreferences p, String key, Limit l) { return l.orDefault(p.getString(key, l.format(l.def))); }

    static long settleNs(SharedPreferences p) { return Math.round(limit(p, SETTLE_S, Limit.SETTLE_S) * 1e9); }
    static double jumpM(SharedPreferences p) { return limit(p, JUMP_M, Limit.JUMP_M); }

    static TiltSet.Gates tiltGates(SharedPreferences p) {
        TiltSet.Gates g = new TiltSet.Gates();
        g.minCoverDeg = limit(p, TILT_COVER_DEG, Limit.TILT_COVER_DEG);
        g.maxRms = limit(p, TILT_RMS_CM, Limit.TILT_RMS_CM) / 100;
        g.minTiltDeg = limit(p, TILT_MIN_DEG, Limit.TILT_MIN_DEG);
        g.maxTiltDeg = limit(p, TILT_MAX_DEG, Limit.TILT_MAX_DEG);
        g.maxSigma = limit(p, TILT_SIGMA_CM, Limit.TILT_SIGMA_CM) / 100;
        g.imuDeg = limit(p, TILT_IMU_DEG, Limit.TILT_IMU_DEG);
        g.maxLeanScatter = limit(p, LEAN_SCATTER_CM, Limit.LEAN_SCATTER_CM) / 100;
        return g;
    }
}
