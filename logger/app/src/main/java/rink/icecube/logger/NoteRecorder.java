package rink.icecube.logger;

import android.content.Context;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.SystemClock;

import java.io.File;
import java.io.IOException;

import rink.icecube.logger.core.NoteRule;

/**
 * One voice note being recorded (logger 0.14.0, #16): the platform MediaRecorder writing
 * mono AAC in an MPEG-4 (.m4a) file at {@link NoteRule#BITRATE} and
 * {@link NoteRule#SAMPLE_RATE}, stopping by itself after {@link NoteRule#MAX_MS}. Used only
 * from the map screen while it is in front (RECORD_AUDIO is a foreground permission here;
 * the service never records). Create and stop it on the main thread: the recorder calls
 * back on the thread that made it.
 */
final class NoteRecorder {
    private final MediaRecorder rec;
    private final long startMs;
    private boolean stopped;
    /** The cap was reached: the recorder stopped itself (asynchronously) and wrote the file. */
    private volatile boolean capped;

    private NoteRecorder(MediaRecorder rec, long startMs) {
        this.rec = rec;
        this.startMs = startMs;
    }

    /**
     * Starts recording into f (an empty file). atMax runs once on this thread when the
     * 60 s cap is reached; onError runs once on this thread when the recorder fails midway
     * (the microphone taken, the media server died); the caller then calls {@link #stop}
     * either way. Throws IOException (or a RuntimeException from the recorder, e.g. the
     * microphone is busy) when it cannot start; the recorder is released then.
     */
    @SuppressWarnings("deprecation")
    static NoteRecorder start(Context c, File f, Runnable atMax, Runnable onError) throws IOException {
        MediaRecorder r = Build.VERSION.SDK_INT >= 31 ? new MediaRecorder(c) : new MediaRecorder();
        NoteRecorder[] self = new NoteRecorder[1];
        long startMs;
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC);
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            r.setAudioChannels(1);
            r.setAudioSamplingRate(NoteRule.SAMPLE_RATE);
            r.setAudioEncodingBitRate(NoteRule.BITRATE);
            r.setMaxDuration(NoteRule.MAX_MS);
            r.setOutputFile(f.getAbsolutePath());
            r.setOnInfoListener((mr, what, extra) -> {
                if (what != MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED
                        || self[0] == null || self[0].stopped) return;
                self[0].capped = true;
                atMax.run();
            });
            r.setOnErrorListener((mr, what, extra) -> {
                if (self[0] == null || self[0].stopped) return;
                onError.run();
            });
            r.prepare();
            // taken before start() returns, so elapsedMs() never reads shorter than the
            // recording: a STOP just before the cap's info event still counts as full
            startMs = SystemClock.elapsedRealtime();
            r.start();
        } catch (IOException | RuntimeException e) {
            r.release();
            throw e;
        }
        self[0] = new NoteRecorder(r, startMs);
        return self[0];
    }

    /** Milliseconds since the start. */
    long elapsedMs() { return SystemClock.elapsedRealtime() - startMs; }

    /**
     * Stops and releases the recorder; returns the note's length in ms (capped at the
     * maximum), or -1 when the recorder had nothing to write (stop failed, as for a very
     * short note; at or past the cap a failed stop is the recorder having stopped itself,
     * and the length stands, even before the cap's info event has arrived). The caller
     * decides whether the file is kept (NoteRule.keep). Once only.
     */
    long stop() {
        if (stopped) return -1;
        stopped = true;
        long elapsed = elapsedMs();
        long ms = Math.min(elapsed, NoteRule.MAX_MS);
        boolean full = capped || NoteRule.reachedCap(elapsed);
        boolean ok = true;
        try {
            rec.stop();
        } catch (RuntimeException e) {   // no valid audio was recorded
            ok = false;
        } finally {
            rec.release();
        }
        return ok || full ? ms : -1;
    }
}
