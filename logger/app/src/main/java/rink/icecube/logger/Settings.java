package rink.icecube.logger;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

import rink.icecube.logger.core.Coverage;
import rink.icecube.logger.core.Limit;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.ReferenceFrame;
import rink.icecube.logger.core.SettleMigration;
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
    static final String GPS_WAIT_MIN = "gps_wait_min";   // 0.8.1, text checked by Limit.GPS_WAIT_MIN
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
    // 0.11.0 (#15): fix_settle_s is the young-fix warning limit (key kept); fix_settle_migrated
    // marks the one-time move of a stored 10 to 120 (core.SettleMigration); a float spell limit
    static final String SETTLE_S = "fix_settle_s", JUMP_M = "fix_jump_m", FLOAT_SPELL_S = "fix_float_spell_s";
    // 0.12.0 (#23): pole check cues, text checked by Limit.PLACE_CM and Limit.STEADY_CM
    static final String PLACE_CM = "pole_place_cm", STEADY_CM = "pole_steady_cm";
    static final String SETTLE_MIGRATED = "fix_settle_migrated";
    static final String TILT_COVER_DEG = "tilt_cover_deg", TILT_RMS_CM = "tilt_rms_cm", TILT_MIN_DEG = "tilt_min_deg",
            TILT_MAX_DEG = "tilt_max_deg", TILT_SIGMA_CM = "tilt_sigma_cm", TILT_IMU_DEG = "tilt_imu_deg",
            LEAN_SCATTER_CM = "lean_scatter_cm";
    // 0.13.0 (#35): auto capture on or off (default on: it stays off until the pole is
    // calibrated), and its limits, text checked by Limit.AUTO_PLUMB_DEG, AUTO_HOLD_S, AUTO_STILL_CM
    static final String AUTO_CAPTURE = "auto_capture";
    static final String AUTO_PLUMB_DEG = "auto_plumb_deg", AUTO_HOLD_S = "auto_hold_s", AUTO_STILL_CM = "auto_still_cm";
    // 0.7, coverage overlay: shown or not (MENU > Coverage), and its Settings > Coverage values
    static final String COVERAGE_ON = "coverage_on";   // default on
    static final String LAST_LAYER_IMPORT_URI = "last_layer_import_uri";   // last picked layer file, picker hint
    static final String COV_TARGET_CM = "coverage_target_cm", COV_CELL_M = "coverage_cell_m",
            COV_RADIUS_M = "coverage_radius_m", COV_REACH_M = "coverage_reach_m",
            COV_GROUND_CM = "coverage_ground_cm", COV_WANDER_CM = "coverage_wander_cm";
    /** The numeric settings: key and its limit, in Settings screen order (Survey, then Coverage from COVERAGE_FIRST). */
    static final String[] LIMIT_KEYS = {PLACE_CM, STEADY_CM, SETTLE_S, JUMP_M, FLOAT_SPELL_S, TILT_COVER_DEG, TILT_RMS_CM, TILT_MIN_DEG, TILT_MAX_DEG,
            TILT_SIGMA_CM, TILT_IMU_DEG, LEAN_SCATTER_CM, AUTO_PLUMB_DEG, AUTO_HOLD_S, AUTO_STILL_CM,
            COV_TARGET_CM, COV_CELL_M, COV_RADIUS_M, COV_REACH_M, COV_GROUND_CM, COV_WANDER_CM};
    static final Limit[] LIMITS = {Limit.PLACE_CM, Limit.STEADY_CM, Limit.SETTLE_S, Limit.JUMP_M, Limit.FLOAT_SPELL_S, Limit.TILT_COVER_DEG, Limit.TILT_RMS_CM,
            Limit.TILT_MIN_DEG, Limit.TILT_MAX_DEG, Limit.TILT_SIGMA_CM, Limit.TILT_IMU_DEG, Limit.LEAN_SCATTER_CM,
            Limit.AUTO_PLUMB_DEG, Limit.AUTO_HOLD_S, Limit.AUTO_STILL_CM,
            Limit.COV_TARGET_CM, Limit.COV_CELL_M, Limit.COV_RADIUS_M, Limit.COV_REACH_M, Limit.COV_GROUND_CM,
            Limit.COV_WANDER_CM};
    /** Index of the first Coverage setting in LIMIT_KEYS and LIMITS. */
    static final int COVERAGE_FIRST = java.util.Arrays.asList(LIMIT_KEYS).indexOf(COV_TARGET_CM);

    private Settings() { }

    private static volatile boolean settleChecked;

    /** The settings, with the one-time settle migration (see migrateSettle) run on the first read. */
    static SharedPreferences prefs(Context c) {
        SharedPreferences p = c.getSharedPreferences("settings", Context.MODE_PRIVATE);
        if (!settleChecked) {
            // the flag is set only after the migration, under a lock, so a concurrent first
            // read (service and Settings screen) waits rather than seeing the old 10
            synchronized (Settings.class) {
                if (!settleChecked) {
                    migrateSettle(p);
                    settleChecked = true;
                }
            }
        }
        return p;
    }

    /**
     * 0.11.0 (#15): a stored fix_settle_s of 10 (the old default) becomes 120, once; the
     * marker pref then keeps a 10 chosen later. Runs wherever the settings are first read
     * (service or Settings screen), so neither ever shows the old value.
     */
    private static void migrateSettle(SharedPreferences p) {
        if (p.getBoolean(SETTLE_MIGRATED, false)) return;
        String stored = p.getString(SETTLE_S, null);
        String moved = SettleMigration.migrate(stored, false);
        SharedPreferences.Editor e = p.edit();
        if (moved != null && !moved.equals(stored)) e.putString(SETTLE_S, moved);
        e.putBoolean(SETTLE_MIGRATED, true).apply();
    }

    static boolean bluetooth(SharedPreferences p) { return BLUETOOTH.equals(p.getString(TRANSPORT, BLUETOOTH)); }

    /** Sending the UBX configuration defaults to on for USB and off for Bluetooth. */
    static boolean configure(SharedPreferences p, boolean bluetooth) {
        return bluetooth ? p.getBoolean(CONFIGURE_BT, false) : p.getBoolean(CONFIGURE_USB, true);
    }

    static boolean logCorrections(SharedPreferences p) { return p.getBoolean(LOG_CORRECTIONS, true); }

    /** Connect caster when the GPS connects (key caster_at_launch, the 0.8.0 "Connect caster at app launch"). */
    static boolean casterWithGps(SharedPreferences p) { return p.getBoolean(CASTER_AT_LAUNCH, false); }

    /** "Ice Cube Logger 0.11.0 (build 15)" */
    static String versionLine() {
        return "Ice Cube Logger " + BuildConfig.VERSION_NAME + " (build " + BuildConfig.VERSION_CODE + ")";
    }

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

    /** Pole check placement and steadiness cue limits in metres (0: cue off). */
    static double placeM(SharedPreferences p) { return limit(p, PLACE_CM, Limit.PLACE_CM) / 100; }
    static double steadyM(SharedPreferences p) { return limit(p, STEADY_CM, Limit.STEADY_CM) / 100; }

    static long settleNs(SharedPreferences p) { return Math.round(limit(p, SETTLE_S, Limit.SETTLE_S) * 1e9); }
    static double jumpM(SharedPreferences p) { return limit(p, JUMP_M, Limit.JUMP_M); }
    static long floatSpellNs(SharedPreferences p) { return Math.round(limit(p, FLOAT_SPELL_S, Limit.FLOAT_SPELL_S) * 1e9); }

    static boolean autoCapture(SharedPreferences p) { return p.getBoolean(AUTO_CAPTURE, true); }
    static double autoPlumbDeg(SharedPreferences p) { return limit(p, AUTO_PLUMB_DEG, Limit.AUTO_PLUMB_DEG); }
    static double autoHoldS(SharedPreferences p) { return limit(p, AUTO_HOLD_S, Limit.AUTO_HOLD_S); }
    static double autoStillM(SharedPreferences p) { return limit(p, AUTO_STILL_CM, Limit.AUTO_STILL_CM) / 100; }

    static boolean coverageOn(SharedPreferences p) { return p.getBoolean(COVERAGE_ON, true); }

    /**
     * The Settings > Coverage values in metres, within Limit.coverageProblem's bounds. For
     * values saved by an earlier version: one outside its own Limit range falls back to its
     * default (Settings.limit), then Limit.boundCoverage cuts a radius or reach over 50 grid
     * spacings to 50 and raises a radius under 0.71 grid spacings to 0.71.
     */
    static Coverage.Params coverage(SharedPreferences p) {
        return Limit.boundCoverage(new Coverage.Params(limit(p, COV_CELL_M, Limit.COV_CELL_M), limit(p, COV_RADIUS_M, Limit.COV_RADIUS_M),
                limit(p, COV_REACH_M, Limit.COV_REACH_M), limit(p, COV_TARGET_CM, Limit.COV_TARGET_CM) / 100,
                limit(p, COV_WANDER_CM, Limit.COV_WANDER_CM) / 100, limit(p, COV_GROUND_CM, Limit.COV_GROUND_CM) / 100));
    }

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
