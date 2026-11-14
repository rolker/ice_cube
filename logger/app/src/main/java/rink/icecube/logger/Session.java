package rink.icecube.logger;

import android.content.Context;
import android.os.Build;
import android.os.SystemClock;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

import rink.icecube.logger.core.ChunkLog;

/**
 * One session folder. All times are SystemClock.elapsedRealtimeNanos().
 * gps.ubx, gps_chunks.csv, imu.csv and events.csv always exist; corrections.rtcm and
 * ntrip_chunks.csv are created on the first logged correction byte.
 *
 * Location: Documents/IceCubeLogger/sessions/<time>/ on Android 11 and later, which
 * survives uninstall. If that folder cannot be written, or on Android 10 and earlier,
 * the app's media folder (Android/media/<package>/sessions/) is used instead.
 */
final class Session {
    final File dir;
    final long startNanos = SystemClock.elapsedRealtimeNanos();
    final ChunkLog gps;
    final ImuLogger imu;
    private final BufferedWriter events;
    private ChunkLog corrections;
    private boolean closed;
    private final Thread flusher;

    private Session(Context ctx, File dir) throws IOException {
        this.dir = dir;
        if (!dir.mkdirs() && !dir.isDirectory()) throw new IOException("could not create " + dir);
        events = new BufferedWriter(new FileWriter(new File(dir, "events.csv")));
        ChunkLog g = null;
        ImuLogger m = null;
        try {
            events.write("elapsed_ns,wall_ms,event\n");
            events.flush();
            g = new ChunkLog(new File(dir, "gps.ubx"), new File(dir, "gps_chunks.csv"));
            m = new ImuLogger(ctx, new File(dir, "imu.csv"));
        } catch (IOException e) {
            if (g != null) { try { g.close(); } catch (IOException ignored) { } }
            events.close();
            throw e;
        }
        gps = g;
        imu = m;
        imu.start();
        flusher = new Thread(this::flushLoop, "flush");
        flusher.setDaemon(true);
        flusher.start();
    }

    /** Opens a new session folder, preferring the public Documents folder. */
    static Session open(Context ctx) throws IOException {
        String name = new SimpleDateFormat("yyyy-MM-dd_HH-mm-ss", Locale.US).format(new Date());
        String fallbackReason = "Android " + Build.VERSION.SDK_INT + " before 11";
        File docs = Storage.primaryRoot();
        if (docs != null) {
            try {
                return new Session(ctx, new File(docs, "sessions/" + name));
            } catch (IOException | SecurityException e) {
                fallbackReason = "Documents not writable: " + e.getMessage();
            }
        }
        Session s = new Session(ctx, new File(Storage.fallbackRoot(ctx), "sessions/" + name));
        s.event("storage fallback to app media folder (" + fallbackReason + ")");
        return s;
    }

    /** Appends one line to events.csv. Commas in the text become semicolons. */
    synchronized void event(String text) {
        if (closed) return;
        try {
            events.write(SystemClock.elapsedRealtimeNanos() + "," + System.currentTimeMillis() + ","
                    + text.replace(',', ';').replace('\n', ' ') + "\n");
            events.flush();
        } catch (IOException ignored) { }
    }

    void writeGps(long elapsedNs, byte[] b, int n) throws IOException {
        if (!closed) gps.write(elapsedNs, b, n);
    }

    void writeCorrections(long elapsedNs, byte[] b, int n) throws IOException {
        ChunkLog c;
        synchronized (this) {
            if (closed) return;
            if (corrections == null) {
                corrections = new ChunkLog(new File(dir, "corrections.rtcm"), new File(dir, "ntrip_chunks.csv"));
            }
            c = corrections;
        }
        c.write(elapsedNs, b, n);
    }

    private void flushLoop() {
        while (true) {
            try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
            synchronized (this) {
                if (closed) return;
                try {
                    gps.flush();
                    if (corrections != null) corrections.flush();
                } catch (IOException ignored) { }
            }
            imu.flush();
        }
    }

    /** Pushes everything written so far to the files, for a snapshot. */
    void flushAll() {
        synchronized (this) {
            if (closed) return;
            try {
                gps.flush();
                if (corrections != null) corrections.flush();
                events.flush();
            } catch (IOException ignored) { }
        }
        imu.flush();
    }

    /** Writes the last event and closes every file. */
    void close(String lastEvent) {
        event(lastEvent);
        imu.stop();
        synchronized (this) {
            closed = true;
            try { gps.close(); } catch (IOException ignored) { }
            if (corrections != null) { try { corrections.close(); } catch (IOException ignored) { } }
            try { events.close(); } catch (IOException ignored) { }
        }
        flusher.interrupt();
    }
}
