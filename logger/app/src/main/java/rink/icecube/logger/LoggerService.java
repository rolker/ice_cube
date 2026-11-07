package rink.icecube.logger;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;

import java.io.BufferedOutputStream;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.StreamParser;
import rink.icecube.logger.core.UbxConfig;

/** Foreground service that keeps logging with the screen off. */
public final class LoggerService extends Service {
    static final String EXTRA_DEVICE = "device";
    static final String EXTRA_HOST = "host", EXTRA_PORT = "port", EXTRA_MOUNT = "mount", EXTRA_USER = "user", EXTRA_PASS = "pass";
    private static final String CHANNEL = "logger";

    /** Latest state for the screen. */
    static volatile LoggerService current;

    private volatile boolean running;
    private Thread reader;
    private UsbLink link;
    private NtripClient ntrip;
    private ImuLogger imu;
    private PowerManager.WakeLock wake;
    private BufferedOutputStream gpsOut;
    private BufferedWriter chunkOut, eventOut;
    private volatile String latestGga;
    private volatile Gga lastFix;
    final StreamParser parser = new StreamParser(s -> { latestGga = s; lastFix = Gga.parse(s); });
    volatile File dir;
    volatile String error;
    volatile long startMs;
    private long offset;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent i, int flags, int id) {
        if (running || i == null) return START_NOT_STICKY;
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Logging", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle("Ice Cube Logger").setContentText("Recording GPS and sensors")
                .setSmallIcon(android.R.drawable.ic_menu_mylocation).setOngoing(true).setContentIntent(open).build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(1, n);
        current = this;
        try {
            begin(i);
        } catch (IOException e) {
            error = e.getMessage();
            finish();
            stopForeground(STOP_FOREGROUND_DETACH);
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    private void begin(Intent i) throws IOException {
        UsbManager mgr = (UsbManager) getSystemService(Context.USB_SERVICE);
        UsbDevice dev = mgr.getDeviceList().get(i.getStringExtra(EXTRA_DEVICE));
        if (dev == null) throw new IOException("USB device is gone");
        File[] media = getExternalMediaDirs();
        File root = new File(media.length > 0 && media[0] != null ? media[0] : getFilesDir(), "sessions");
        dir = new File(root, new SimpleDateFormat("yyyy-MM-dd_HH-mm-ss", Locale.US).format(new Date()));
        if (!dir.mkdirs()) throw new IOException("could not create " + dir);
        gpsOut = new BufferedOutputStream(new FileOutputStream(new File(dir, "gps.ubx")), 1 << 16);
        chunkOut = new BufferedWriter(new FileWriter(new File(dir, "usb_chunks.csv")), 1 << 14);
        chunkOut.write("elapsed_ns,byte_offset,length\n");
        eventOut = new BufferedWriter(new FileWriter(new File(dir, "events.csv")));
        eventOut.write("elapsed_ns,wall_ms,event\n");
        event("start " + String.format("%04x:%04x", dev.getVendorId(), dev.getProductId()));
        link = UsbLink.open(mgr, dev);
        byte[] cfg = UbxConfig.enableUsbLogging();
        link.write(cfg, cfg.length);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "icecube:logger");
        wake.acquire(8 * 3600 * 1000L);

        imu = new ImuLogger(this, new File(dir, "imu.csv"));
        imu.start();

        String host = i.getStringExtra(EXTRA_HOST);
        if (host != null && !host.isEmpty()) {
            ntrip = new NtripClient(host, i.getIntExtra(EXTRA_PORT, 2101), i.getStringExtra(EXTRA_MOUNT),
                    i.getStringExtra(EXTRA_USER), i.getStringExtra(EXTRA_PASS), this::sendRtcm, () -> latestGga);
            ntrip.start();
        }
        startMs = System.currentTimeMillis();
        running = true;
        reader = new Thread(this::readLoop, "usb-read");
        reader.setPriority(Thread.MAX_PRIORITY);
        reader.start();
    }

    private void sendRtcm(byte[] b, int n) {
        UsbLink l = link;
        if (l == null) return;
        try { l.write(b, n); } catch (IOException e) { error = e.getMessage(); }
    }

    private void readLoop() {
        byte[] buf = new byte[16384];
        long lastFlush = SystemClock.elapsedRealtime();
        int failures = 0;
        try {
            while (running) {
                int n = link.read(buf);
                long t = SystemClock.elapsedRealtimeNanos();
                if (n > 0) {
                    failures = 0;
                    gpsOut.write(buf, 0, n);
                    chunkOut.write(t + "," + offset + "," + n + "\n");
                    offset += n;
                    parser.feed(buf, n);
                } else if (++failures > 150) {
                    error = "no data from the GPS for 30 s. Check the cable and that it is the GPS port.";
                    failures = 0;
                    event("usb silent");
                }
                long now = SystemClock.elapsedRealtime();
                if (now - lastFlush > 1000) {
                    gpsOut.flush(); chunkOut.flush(); imu.flush();
                    lastFlush = now;
                }
            }
        } catch (IOException e) {
            error = "write failed: " + e.getMessage();
        }
    }

    private void event(String s) throws IOException {
        eventOut.write(SystemClock.elapsedRealtimeNanos() + "," + System.currentTimeMillis() + "," + s + "\n");
        eventOut.flush();
    }

    void mark(String s) {
        try { event(s); } catch (IOException e) { error = e.getMessage(); }
    }

    boolean isRunning() { return running; }
    Gga fix() { return lastFix; }
    NtripClient ntrip() { return ntrip; }
    long imuSamples() { return imu == null ? 0 : imu.samples; }

    private void finish() {
        running = false;
        if (ntrip != null) ntrip.stop();
        if (reader != null) { try { reader.join(1000); } catch (InterruptedException ignored) { } }
        if (imu != null) imu.stop();
        try {
            if (eventOut != null) {
                event(String.format(Locale.US, "stop bytes=%d rawx=%d rawx_lost=%d gga=%d gga_lost=%d acks=%d naks=%d",
                        parser.bytes, parser.rawxEpochs, parser.rawxMissed, parser.ggaCount, parser.ggaMissed,
                        parser.valsetAcks, parser.valsetNaks));
                eventOut.close();
            }
            if (chunkOut != null) chunkOut.close();
            if (gpsOut != null) gpsOut.close();
        } catch (IOException e) { error = e.getMessage(); }
        if (link != null) { link.close(); link = null; }
        if (wake != null && wake.isHeld()) wake.release();
    }

    @Override public void onDestroy() {
        if (running) finish();
        super.onDestroy();
    }
}
