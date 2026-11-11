package rink.icecube.logger;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Locale;

/**
 * Writes phone accelerometer, gravity and gyroscope samples to CSV. Times are sensor event
 * timestamps. It also keeps the last few seconds of gravity samples in memory, stamped with
 * SystemClock.elapsedRealtimeNanos() on arrival (the GGA epochs' clock), for tilt capture.
 */
final class ImuLogger implements SensorEventListener {
    private static final int PERIOD_US = 10000, RING = 1024;
    private final long[] gravNs = new long[RING];
    private final float[][] grav = new float[RING][3];
    private int gravCount;
    private final SensorManager sm;
    private final BufferedWriter w;
    private HandlerThread thread;
    volatile long samples;
    volatile boolean failed;

    ImuLogger(Context ctx, File file) throws IOException {
        sm = (SensorManager) ctx.getSystemService(Context.SENSOR_SERVICE);
        w = new BufferedWriter(new FileWriter(file), 1 << 16);
        w.write("sensor,event_ns,x,y,z\n");
    }

    void start() {
        thread = new HandlerThread("imu");
        thread.start();
        Handler h = new Handler(thread.getLooper());
        for (int type : new int[] {Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_GRAVITY, Sensor.TYPE_GYROSCOPE}) {
            Sensor s = sm.getDefaultSensor(type);
            if (s != null) sm.registerListener(this, s, PERIOD_US, h);
        }
    }

    void stop() {
        sm.unregisterListener(this);
        if (thread != null) thread.quitSafely();
        try {
            if (thread != null) thread.join(1000);
            synchronized (this) { w.close(); }
        } catch (IOException | InterruptedException ignored) { }
    }

    @Override public synchronized void onSensorChanged(SensorEvent e) {
        String n;
        switch (e.sensor.getType()) {
            case Sensor.TYPE_ACCELEROMETER: n = "acc"; break;
            case Sensor.TYPE_GRAVITY: n = "grav"; break;
            default: n = "gyro"; break;
        }
        if (e.sensor.getType() == Sensor.TYPE_GRAVITY) {
            int i = gravCount % RING;
            gravNs[i] = SystemClock.elapsedRealtimeNanos();
            grav[i][0] = e.values[0]; grav[i][1] = e.values[1]; grav[i][2] = e.values[2];
            gravCount++;
        }
        try {
            w.write(String.format(Locale.US, "%s,%d,%.5f,%.5f,%.5f\n", n, e.timestamp, e.values[0], e.values[1], e.values[2]));
            samples++;
        } catch (IOException x) { failed = true; }
    }

    /** Mean gravity vector (m/s², phone axes) of the samples that arrived in [fromNs, toNs], and their count; null if none. */
    synchronized double[] gravityMean(long fromNs, long toNs) {
        double x = 0, y = 0, z = 0;
        int n = 0;
        for (int k = 0; k < Math.min(gravCount, RING); k++) {
            int i = (gravCount - 1 - k) % RING;
            if (gravNs[i] < fromNs) break;
            if (gravNs[i] > toNs) continue;
            x += grav[i][0]; y += grav[i][1]; z += grav[i][2];
            n++;
        }
        return n == 0 ? null : new double[] {x / n, y / n, z / n, n};
    }

    @Override public void onAccuracyChanged(Sensor s, int a) { }

    synchronized void flush() {
        try { w.flush(); } catch (IOException x) { failed = true; }
    }
}
