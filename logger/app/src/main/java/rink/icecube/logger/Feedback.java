package rink.icecube.logger;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

/**
 * Sounds and vibration for the survey, so the user does not have to look at the screen.
 * Each cue is a sequence of DTMF-style tones (ToneGenerator on the media stream, so the
 * phone's silent mode does not mute it; set the media volume before going out) and a
 * vibration pattern. Settings > Survey: Sound and Vibration switch each off. Cues queue:
 * a cue that comes while another plays starts after it.
 */
final class Feedback {
    enum Cue {
        /** Mode selected: 1, 2 or 3 beeps. */
        MODE_SURFACE, MODE_OBJECT, MODE_REF,
        /** A capture written. */
        SURFACE, OBJECT,
        REF_START, REF_PAUSED, REF_RESUMED, REF_END, REF_CANCEL,
        PHOTO, POLE_OK, POLE_ALARM, ERROR,
        /** A capture written in a fix stretch that began with a large jump (fix may be false). */
        SUSPECT,
        /** Tilt capture: set started, lean taken, set written, set cancelled. */
        TILT_START, LEAN, TILT_END
    }

    private static final int BEEP = ToneGenerator.TONE_DTMF_5, HIGH = ToneGenerator.TONE_DTMF_D,
            HIGH2 = ToneGenerator.TONE_DTMF_A, LOW = ToneGenerator.TONE_DTMF_1, MID = ToneGenerator.TONE_DTMF_0;

    private static final Handler UI = new Handler(Looper.getMainLooper());
    private static ToneGenerator tones;
    private static long busyUntil;

    private Feedback() { }

    /** {tone, duration ms, gap after ms} triples. */
    static int[] tones(Cue c) {
        switch (c) {
            case MODE_SURFACE: return new int[] {BEEP, 120, 150};
            case MODE_OBJECT: return new int[] {BEEP, 120, 150, BEEP, 120, 150};
            case MODE_REF: return new int[] {BEEP, 120, 150, BEEP, 120, 150, BEEP, 120, 150};
            case SURFACE: return new int[] {HIGH, 90, 60};
            case OBJECT: return new int[] {HIGH2, 90, 40, HIGH, 140, 60};
            case REF_START: return new int[] {LOW, 350, 100};
            case REF_PAUSED: return new int[] {MID, 500, 100};
            case REF_RESUMED: return new int[] {MID, 120, 100};
            case REF_END: return new int[] {LOW, 150, 50, BEEP, 150, 50, HIGH, 300, 150};
            case REF_CANCEL: return new int[] {HIGH, 150, 50, LOW, 350, 100};
            case PHOTO: return new int[] {ToneGenerator.TONE_DTMF_9, 80, 60};
            case POLE_OK: return new int[] {BEEP, 100, 60, HIGH, 100, 100};
            case POLE_ALARM: return new int[] {HIGH, 180, 70, LOW, 180, 70, HIGH, 180, 70, LOW, 180, 70, HIGH, 180, 70, LOW, 400, 150};
            case SUSPECT: return new int[] {HIGH, 70, 40, MID, 70, 40, HIGH, 70, 40, MID, 70, 40, LOW, 250, 100};
            case TILT_START: return new int[] {LOW, 120, 60, MID, 120, 60, BEEP, 120, 100};
            case LEAN: return new int[] {BEEP, 70, 30, BEEP, 70, 60};
            case TILT_END: return new int[] {BEEP, 120, 40, HIGH2, 120, 40, HIGH, 300, 150};
            default: return new int[] {ToneGenerator.TONE_PROP_NACK, 450, 100};   // ERROR: low buzz
        }
    }

    /** Vibration: off/on durations in ms, starting with a 0 ms wait. */
    static long[] vibration(Cue c) {
        switch (c) {
            case MODE_SURFACE: return new long[] {0, 80};
            case MODE_OBJECT: return new long[] {0, 80, 120, 80};
            case MODE_REF: return new long[] {0, 80, 120, 80, 120, 80};
            case SURFACE: return new long[] {0, 50};
            case OBJECT: return new long[] {0, 50, 60, 120};
            case REF_START: return new long[] {0, 300};
            case REF_PAUSED: return new long[] {0, 500};
            case REF_RESUMED: return new long[] {0, 100};
            case REF_END: return new long[] {0, 100, 80, 100, 80, 300};
            case REF_CANCEL: return new long[] {0, 300, 100, 100};
            case PHOTO: return new long[] {0, 40};
            case POLE_OK: return new long[] {0, 60, 80, 60};
            case POLE_ALARM: return new long[] {0, 300, 100, 300, 100, 300, 100, 800};
            case SUSPECT: return new long[] {0, 60, 50, 60, 50, 60, 50, 250};
            case TILT_START: return new long[] {0, 80, 80, 80, 80, 80};
            case LEAN: return new long[] {0, 40, 40, 40};
            case TILT_END: return new long[] {0, 100, 60, 300};
            default: return new long[] {0, 450};
        }
    }

    /** Plays a cue (from any thread). */
    static void play(Context ctx, Cue c) {
        Context app = ctx.getApplicationContext();
        UI.post(() -> playNow(app, c));
    }

    private static void playNow(Context ctx, Cue c) {
        SharedPreferences p = Settings.prefs(ctx);
        long now = SystemClock.uptimeMillis();
        long start = Math.max(now, busyUntil);
        int[] t = tones(c);
        long at = start;
        if (Settings.sound(p)) {
            for (int i = 0; i + 2 < t.length; i += 3) {
                int tone = t[i], dur = t[i + 1];
                UI.postAtTime(() -> tone(tone, dur), at);
                at += dur + t[i + 2];
            }
        }
        if (Settings.vibration(p)) {
            long[] v = vibration(c);
            UI.postAtTime(() -> vibrate(ctx, v), start);
            long total = 0;
            for (long x : v) total += x;
            at = Math.max(at, start + total + 100);
        }
        busyUntil = at;
    }

    private static void tone(int tone, int ms) {
        try {
            if (tones == null) tones = new ToneGenerator(AudioManager.STREAM_MUSIC, ToneGenerator.MAX_VOLUME);
            tones.startTone(tone, ms);
        } catch (RuntimeException e) {   // no audio available: stay silent
            tones = null;
        }
    }

    private static void vibrate(Context ctx, long[] pattern) {
        Vibrator v;
        if (Build.VERSION.SDK_INT >= 31) {
            VibratorManager m = (VibratorManager) ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
            v = m == null ? null : m.getDefaultVibrator();
        } else {
            v = ctx.getSystemService(Vibrator.class);
        }
        if (v == null || !v.hasVibrator()) return;
        try { v.vibrate(VibrationEffect.createWaveform(pattern, -1)); } catch (RuntimeException ignored) { }
    }
}
