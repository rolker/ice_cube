package rink.icecube.logger;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Locale;

/** Writes phone accelerometer, gravity and gyroscope samples to CSV. Times are sensor event timestamps. */
final class ImuLogger implements SensorEventListener {
    private static final int PERIOD_US = 10000;
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
        try {
            w.write(String.format(Locale.US, "%s,%d,%.5f,%.5f,%.5f\n", n, e.timestamp, e.values[0], e.values[1], e.values[2]));
            samples++;
        } catch (IOException x) { failed = true; }
    }

    @Override public void onAccuracyChanged(Sensor s, int a) { }

    synchronized void flush() {
        try { w.flush(); } catch (IOException x) { failed = true; }
    }
}
