package rink.icecube.logger;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.Process;
import android.os.SystemClock;

import java.io.File;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import rink.icecube.logger.core.BaseCheck;
import rink.icecube.logger.core.BaseWatch;
import rink.icecube.logger.core.Epoch;
import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.ReferenceFrame;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.Rtcm3Parser;
import rink.icecube.logger.core.RtcmStats;
import rink.icecube.logger.core.Sourcetable;
import rink.icecube.logger.core.StationArp;
import rink.icecube.logger.core.StreamParser;
import rink.icecube.logger.core.SurveyMode;
import rink.icecube.logger.core.TrackPoint;
import rink.icecube.logger.core.UbxConfig;

/**
 * Foreground service that owns the session, the GPS link and the caster connection, so
 * logging continues with the screen off. The screen sends it one action per button and
 * reads its public state. Settings come from SharedPreferences at connect time.
 *
 * A session starts when the GPS or the caster is started (or Start session is pressed)
 * and ends with Stop. A GPS started with EXTRA_WAIT (autostart) opens the session only
 * once the link is up.
 *
 * It also keeps what the map and the warnings need: a 2-minute trail of GGA fixes, the
 * session's captures ({@link Survey}: MARK, refs, photos), the pole height in effect, the reference frame in use (from
 * the caster's sourcetable or the Frame setting), the base position from RTCM 1005/1006
 * and how it compares with a map layer's base point.
 */
public final class LoggerService extends Service {
    static final String ACTION_GPS_CONNECT = "rink.icecube.logger.GPS_CONNECT";
    static final String ACTION_GPS_DISCONNECT = "rink.icecube.logger.GPS_DISCONNECT";
    static final String ACTION_CASTER_CONNECT = "rink.icecube.logger.CASTER_CONNECT";
    static final String ACTION_CASTER_DISCONNECT = "rink.icecube.logger.CASTER_DISCONNECT";
    static final String ACTION_START_SESSION = "rink.icecube.logger.START_SESSION";
    static final String ACTION_STOP = "rink.icecube.logger.STOP";
    static final String EXTRA_WAIT = "wait";

    private static final String CHANNEL = "logger";
    private static final long BT_FIRST_RETRY_MS = 3000, BT_MAX_RETRY_MS = 30000;
    private static final long GGA_MAX_AGE_NS = 30_000_000_000L;
    private static final long TRAIL_MS = 120_000, FIX_FRESH_NS = 5_000_000_000L;

    /** Latest service instance, for the screen. */
    static volatile LoggerService current;

    private final Object lock = new Object();
    private volatile Session session;
    private volatile File lastDir;
    private volatile GpsWorker gps;
    private volatile GpsLink gpsLink;
    private volatile NtripClient caster;
    private volatile boolean logCorrections;
    private PowerManager.WakeLock wake;

    private volatile String latestGga;
    private volatile long latestGgaNanos;
    private volatile Gga lastFix;
    volatile StreamParser parser = newParser();
    volatile RtcmStats rtcmStats = new RtcmStats(30000);
    volatile Rtcm3Parser rtcmParser = newRtcmParser(rtcmStats);
    volatile long rtcmForwarded, rtcmWriteErrors;
    volatile String error;

    private volatile long lastFixNanos;
    private volatile TrackPoint firstFix;
    private final ArrayDeque<TrackPoint> trail = new ArrayDeque<>();
    private volatile Survey survey;
    private volatile double poleHeight;

    private volatile BaseWatch baseWatch = new BaseWatch();
    private volatile ReferenceFrame sourcetableFrame;
    private volatile String sourcetableNote;           // why the sourcetable gave no frame, or null
    private volatile ReferenceFrame.Resolved frame;
    private int casterGeneration;
    private volatile BaseCheck layerCheck;
    private volatile String layerCheckFile;
    private int layerCheckGeneration = -1;
    private final ExecutorService background = Executors.newSingleThreadExecutor();
    private SharedPreferences prefs;
    private final SharedPreferences.OnSharedPreferenceChangeListener prefListener = (p, key) -> {
        if (Settings.POLE_HEIGHT.equals(key)) {
            double v = Settings.poleHeight(p);
            if (v != poleHeight) {
                poleHeight = v;
                event("pole_height=" + PoleHeight.format(v) + " changed");
            }
        } else if (Settings.FRAME.equals(key) || Settings.FRAME_EPOCH.equals(key)) {
            updateFrame();
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        prefs = Settings.prefs(this);
        poleHeight = Settings.poleHeight(prefs);
        prefs.registerOnSharedPreferenceChangeListener(prefListener);
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent i, int flags, int id) {
        goForeground();
        current = this;
        String a = i == null ? null : i.getAction();
        if (ACTION_GPS_CONNECT.equals(a)) startGps(i.getBooleanExtra(EXTRA_WAIT, false));
        else if (ACTION_GPS_DISCONNECT.equals(a)) stopGps("gps disconnected by user");
        else if (ACTION_CASTER_CONNECT.equals(a)) startCaster();
        else if (ACTION_CASTER_DISCONNECT.equals(a)) stopCaster("caster disconnected by user");
        else if (ACTION_START_SESSION.equals(a)) ensureSession();
        else if (ACTION_STOP.equals(a)) stopAll();
        stopIfIdle();
        return START_NOT_STICKY;
    }

    /** Called on every start command, so each startForegroundService() is answered. */
    private void goForeground() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Logging", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle("Ice Cube Logger").setContentText("Logging GPS, corrections and sensors")
                .setSmallIcon(android.R.drawable.ic_menu_mylocation).setOngoing(true).setContentIntent(open).build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(1, n);
    }

    private void stopIfIdle() {
        if (session == null && gps == null && caster == null) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
    }

    // ---- session ----

    private StreamParser newParser() {
        return new StreamParser(s -> {
            latestGga = s;
            latestGgaNanos = System.nanoTime();
            Gga g = Gga.parse(s);
            lastFix = g;
            Survey sv = survey;
            if (sv != null) sv.onEpoch(Epoch.from(g, SystemClock.elapsedRealtimeNanos()));
            onFix(TrackPoint.from(g, SystemClock.elapsedRealtime()));
        });
    }

    private void onFix(TrackPoint p) {
        if (p == null) return;
        lastFixNanos = System.nanoTime();
        if (firstFix == null) firstFix = p;
        synchronized (trail) {
            trail.addLast(p);
            while (trail.size() > 2000 || trail.peekFirst().elapsedMs < p.elapsedMs - TRAIL_MS) trail.removeFirst();
        }
    }

    private Rtcm3Parser newRtcmParser(RtcmStats stats) {
        Rtcm3Parser r = new Rtcm3Parser((type, len) -> stats.add(type, SystemClock.elapsedRealtime()));
        r.payloads = (type, b, off, len) -> {
            if (type != 1005 && type != 1006) return;
            StationArp a = StationArp.decode(b, off, len);
            if (a == null) return;
            for (String e : baseWatch.onStation(a)) event(e);
            runInBackground(this::checkLayers);
        };
        return r;
    }

    // ---- frame and base checks ----

    /** Re-resolves the frame in use and logs it (and any mismatch warning) when it changes. */
    private synchronized void updateFrame() {
        ReferenceFrame.Resolved r = ReferenceFrame.resolve(Settings.frame(Settings.prefs(this)), sourcetableFrame);
        if (r.sameAs(frame)) return;
        frame = r;
        event(r.eventText());
        if (r.warning != null) event(r.warning);
    }

    /** Fetches the sourcetable on its own thread; never holds up the corrections connection. */
    private void fetchSourcetable(String host, int port, String mount, String user, String pass) {
        final int gen;
        synchronized (this) { gen = ++casterGeneration; }
        sourcetableFrame = null;
        sourcetableNote = "fetching";
        new Thread(() -> {
            ReferenceFrame f = null;
            String why;
            try {
                String misc = Sourcetable.misc(Sourcetable.fetch(host, port, user, pass, 8000), mount);
                if (misc == null) why = "mount point not in sourcetable";
                else if ((f = ReferenceFrame.parse(misc)) == null) why = misc.isEmpty() ? "misc field empty" : "no frame name in misc field: " + misc;
                else why = null;
            } catch (IOException | RuntimeException e) {
                why = String.valueOf(e.getMessage());
            }
            synchronized (this) { if (gen != casterGeneration) return; }
            sourcetableNote = why;
            if (f == null) event("frame source=sourcetable: none (" + why + ")");
            else {
                sourcetableFrame = f;
                event("frame=" + f.name + " epoch=" + f.epochText() + " source=sourcetable");
            }
            updateFrame();
        }, "sourcetable").start();
    }

    /** Runs on the background executor: compares the latest ARP with a layer base point. */
    private void checkLayers() {
        StationArp arp = baseWatch.latest();
        if (arp == null) return;
        Set<String> off = Settings.layersOff(Settings.prefs(this));
        double[] base = null;
        String file = null;
        for (int pass = 0; pass < 2 && base == null; pass++) {   // switched-on layers first, then any
            for (Layers.LayerFile lf : Layers.get(this)) {
                if (lf.layer == null || (pass == 0 && off.contains(lf.name()))) continue;
                base = lf.layer.basePosition();
                if (base != null) { file = lf.name(); break; }
            }
        }
        synchronized (this) { layerCheckGeneration = Layers.generation(); }
        if (base == null) { layerCheck = null; layerCheckFile = null; return; }
        BaseCheck c = BaseCheck.compare(base[0], base[1], base[2], arp);
        BaseCheck old = layerCheck;
        layerCheck = c;
        layerCheckFile = file;
        boolean changed = old == null || old.ok != c.ok || Math.abs(old.dE - c.dE) > 0.01 || Math.abs(old.dN - c.dN) > 0.01
                || (Double.isNaN(old.dU) != Double.isNaN(c.dU)) || Math.abs(old.dU - c.dU) > 0.01;
        if (changed) event("layer base check: " + c.describe() + " layer=" + file + " station=" + arp.stationId);
    }

    /** The screens call this; it re-runs the base check after the layers were reloaded. */
    void layersMaybeChanged() {
        synchronized (this) { if (layerCheckGeneration == Layers.generation()) return; }
        runInBackground(this::checkLayers);
    }

    private void runInBackground(Runnable r) {
        try { background.execute(r); } catch (java.util.concurrent.RejectedExecutionException ignored) { }   // service ending
    }

    private Session ensureSession() {
        synchronized (lock) {
            if (session != null) return session;
            parser = newParser();
            rtcmStats = new RtcmStats(30000);
            rtcmParser = newRtcmParser(rtcmStats);
            rtcmForwarded = 0;
            rtcmWriteErrors = 0;
            lastFix = null;
            firstFix = null;
            synchronized (trail) { trail.clear(); }
            baseWatch = new BaseWatch();
            layerCheck = null;
            layerCheckFile = null;
            frame = null;
            error = null;
            try {
                Session s = Session.open(this);
                s.event("session start app=" + BuildConfig.VERSION_NAME + " android_sdk=" + Build.VERSION.SDK_INT
                        + " device=" + Build.MODEL);
                survey = new Survey(this, s.dir);
                session = s;
                lastDir = s.dir;
                s.event("pole_height=" + PoleHeight.format(poleHeight));
                updateFrame();
                PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
                wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "icecube:logger");
                wake.acquire(24 * 3600 * 1000L);
                return s;
            } catch (IOException e) {
                error = "could not open session: " + e.getMessage();
                return null;
            }
        }
    }

    void event(String text) {
        Session s = session;
        if (s != null) s.event(text);
    }

    /** MARK in the given mode (see Survey). Refused with an error cue when no session is open. */
    Survey.Outcome mark(SurveyMode mode) {
        Survey sv = survey;
        if (sv == null) {
            Feedback.play(this, Feedback.Cue.ERROR);
            return new Survey.Outcome(false, "No session: tap REC to start one");
        }
        return sv.mark(mode);
    }

    /** The open session's captures, or null. */
    Survey survey() { return survey; }

    /** Flushes the open session's files, if any, for a snapshot. */
    void flushSession() { Session s = session; if (s != null) s.flushAll(); }

    private void stopAll() {
        stopCaster("caster stopped (session stop)");
        stopGps("gps stopped (session stop)");
        synchronized (lock) {
            Session s = session;
            Survey sv = survey;
            if (sv != null) sv.close();
            survey = null;
            session = null;
            if (s != null) {
                StreamParser p = parser;
                Rtcm3Parser r = rtcmParser;
                s.close(String.format(Locale.US,
                        "stop gps_bytes=%d rawx=%d rawx_lost=%d gga=%d gga_lost=%d acks=%d naks=%d"
                                + " rtcm_bytes=%d rtcm_frames=%d rtcm_crc_errors=%d rtcm_forwarded=%d rtcm_write_errors=%d",
                        p.bytes, p.rawxEpochs, p.rawxMissed, p.ggaCount, p.ggaMissed, p.valsetAcks, p.valsetNaks,
                        r.bytes, r.frames, r.crcErrors, rtcmForwarded, rtcmWriteErrors));
            }
            if (wake != null && wake.isHeld()) wake.release();
            wake = null;
        }
    }

    // ---- caster ----

    private void startCaster() {
        SharedPreferences p = Settings.prefs(this);
        String host = p.getString(Settings.HOST, "").trim();
        if (host.isEmpty()) { error = "set the caster in Settings first"; return; }
        stopCaster("caster restarted");
        if (ensureSession() == null) return;
        logCorrections = Settings.logCorrections(p);
        String mount = p.getString(Settings.MOUNT, "").trim();
        fetchSourcetable(host, Settings.port(p), mount, p.getString(Settings.USER, "").trim(), p.getString(Settings.PASS, ""));
        NtripClient c = new NtripClient(host, Settings.port(p), mount, p.getString(Settings.USER, "").trim(),
                p.getString(Settings.PASS, ""), this::onRtcm, this::ggaForCaster, this::event);
        caster = c;
        event("caster start mount=" + mount + " log_corrections=" + logCorrections);
        c.start();
    }

    private void stopCaster(String why) {
        NtripClient c = caster;
        caster = null;
        if (c == null) return;
        c.stop();
        try { c.join(1000); } catch (InterruptedException ignored) { }
        event(why);
        synchronized (this) { casterGeneration++; }   // a fetch still running is ignored
        sourcetableFrame = null;
        sourcetableNote = null;
        if (session != null) updateFrame();
    }

    /** Runs on the caster thread. Logs, counts and forwards each received chunk unchanged. */
    private void onRtcm(byte[] b, int n) {
        long t = SystemClock.elapsedRealtimeNanos();
        Session s = session;
        if (logCorrections && s != null) {
            try { s.writeCorrections(t, b, n); } catch (IOException e) { error = "corrections write: " + e.getMessage(); }
        }
        rtcmParser.feed(b, n);
        GpsLink l = gpsLink;
        if (l == null) return;
        try {
            l.write(b, n);
            rtcmForwarded += n;
        } catch (IOException e) {
            rtcmWriteErrors++;
            error = "RTCM to GPS: " + e.getMessage();
        }
    }

    /** The latest GGA while the GPS is connected and the sentence is under 30 s old, else null. */
    private String ggaForCaster() {
        if (gpsLink == null) return null;
        String g = latestGga;
        return g != null && System.nanoTime() - latestGgaNanos < GGA_MAX_AGE_NS ? g : null;
    }

    // ---- GPS ----

    private void startGps(boolean wait) {
        GpsWorker old = gps;
        if (old != null && wait) return;          // autostart never replaces a running link
        if (!wait && ensureSession() == null) return;
        SharedPreferences p = Settings.prefs(this);
        boolean bt = Settings.bluetooth(p);
        String address = p.getString(Settings.BT_ADDRESS, null);
        boolean configure = Settings.configure(p, bt);
        if (old != null) stopGps("gps restarted");
        GpsWorker w = new GpsWorker(bt, address, configure, wait);
        gps = w;
        event("gps start link=" + (bt ? "bluetooth " + address : "usb") + " configure=" + configure
                + (wait ? " session_on_connect" : ""));
        w.start();
    }

    private void stopGps(String why) {
        GpsWorker w = gps;
        gps = null;
        if (w == null) return;
        w.stop();
        event(why);
    }

    /** Runs on the GPS thread. */
    private void onGpsBytes(long t, byte[] b, int n) {
        Session s = session;
        if (s != null) {
            try { s.writeGps(t, b, n); } catch (IOException e) { error = "gps write: " + e.getMessage(); }
        }
        parser.feed(b, n);
    }

    /** One GPS connection attempt loop on a dedicated high-priority thread. Bytes are never altered. */
    private final class GpsWorker implements Runnable {
        final boolean bluetooth, configure, waitForLink;
        final String address;
        volatile boolean active = true, connected, everConnected, usbNeedsPermission;
        volatile String status = "starting";
        volatile long lastDataNanos;
        private volatile GpsLink link;
        private Thread thread;

        GpsWorker(boolean bluetooth, String address, boolean configure, boolean waitForLink) {
            this.bluetooth = bluetooth; this.address = address; this.configure = configure; this.waitForLink = waitForLink;
        }

        void start() {
            thread = new Thread(this, "gps-read");
            thread.setPriority(Thread.MAX_PRIORITY);
            thread.start();
        }

        void stop() {
            active = false;
            GpsLink l = link;
            if (l instanceof BluetoothLink) l.close();   // unblocks the read; USB reads time out by themselves
            thread.interrupt();
            try { thread.join(1500); } catch (InterruptedException ignored) { }
        }

        @Override public void run() {
            try { Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO); } catch (RuntimeException ignored) { }
            byte[] buf = new byte[1 << 16];
            long retry = BT_FIRST_RETRY_MS;
            String lastFailure = null;
            while (active) {
                GpsLink l;
                try {
                    status = "connecting";
                    l = open();
                } catch (IOException | RuntimeException e) {     // SecurityException if a permission is missing
                    String msg = String.valueOf(e.getMessage());
                    status = "waiting: " + msg;
                    if (!msg.equals(lastFailure)) event("gps connect failed: " + msg);
                    lastFailure = msg;
                    if (!sleep(bluetooth ? retry : 1000)) break;
                    if (bluetooth) retry = Math.min(BT_MAX_RETRY_MS, retry * 2);
                    continue;
                }
                if (!active) { l.close(); break; }
                lastFailure = null;
                retry = BT_FIRST_RETRY_MS;
                if (waitForLink) ensureSession();
                link = l;
                gpsLink = l;
                connected = true;
                everConnected = true;
                status = "connected";
                Session s = session;
                event("gps connected " + l.describe() + " gps_offset=" + (s == null ? 0 : s.gps.offset()));
                if (configure) sendConfig(l);
                String why = readLoop(l, buf);
                connected = false;
                if (gpsLink == l) gpsLink = null;
                link = null;
                l.close();
                if (active) { event("gps lost: " + why); status = "lost: " + why; }
            }
            status = "off";
        }

        private void sendConfig(GpsLink l) {
            byte[] cfg = bluetooth ? UbxConfig.enableUart1Logging() : UbxConfig.enableUsbLogging();
            try {
                l.write(cfg, cfg.length);
                event("gps config sent to " + (bluetooth ? "UART1" : "USB") + " (RAM only)");
            } catch (IOException e) {
                event("gps config write failed: " + e.getMessage());
            }
        }

        private String readLoop(GpsLink l, byte[] buf) {
            int idle = 0;
            try {
                while (active) {
                    int n = l.read(buf);
                    long t = SystemClock.elapsedRealtimeNanos();
                    if (n > 0) {
                        idle = 0;
                        lastDataNanos = t;
                        onGpsBytes(t, buf, n);
                    } else if (++idle == 150) {
                        event("gps silent for 30 s");
                    }
                }
                return "stopped";
            } catch (IOException e) {
                return String.valueOf(e.getMessage());
            }
        }

        private GpsLink open() throws IOException {
            if (bluetooth) {
                BluetoothManager bm = (BluetoothManager) getSystemService(Context.BLUETOOTH_SERVICE);
                BluetoothAdapter a = bm == null ? null : bm.getAdapter();
                if (a == null) throw new IOException("this phone has no Bluetooth");
                if (address == null || address.isEmpty()) throw new IOException("choose the Bluetooth device in Settings");
                if (Build.VERSION.SDK_INT >= 31
                        && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                    throw new IOException("Bluetooth permission not granted");
                }
                if (!a.isEnabled()) throw new IOException("Bluetooth is off");
                return BluetoothLink.open(a.getRemoteDevice(address));
            }
            UsbManager m = (UsbManager) getSystemService(Context.USB_SERVICE);
            UsbDevice d = UsbLink.findGps(m);
            if (d == null) { usbNeedsPermission = false; throw new IOException("no USB GPS attached"); }
            if (!m.hasPermission(d)) { usbNeedsPermission = true; throw new IOException("waiting for USB permission"); }
            usbNeedsPermission = false;
            return UsbLink.open(m, d);
        }

        private boolean sleep(long ms) {
            try { Thread.sleep(ms); return active; } catch (InterruptedException e) { return false; }
        }
    }

    // ---- state for the screen ----

    boolean sessionOpen() { return session != null; }
    File folder() { Session s = session; return s != null ? s.dir : lastDir; }
    long sessionSeconds() {
        Session s = session;
        return s == null ? 0 : (SystemClock.elapsedRealtimeNanos() - s.startNanos) / 1_000_000_000L;
    }
    long imuSamples() { Session s = session; return s == null ? 0 : s.imu.samples; }
    boolean gpsActive() { return gps != null; }
    boolean gpsConnected() { GpsWorker w = gps; return w != null && w.connected; }
    /** The GPS link was up at some point since Connect and is now down (it keeps retrying). */
    boolean gpsLost() { GpsWorker w = gps; return w != null && w.everConnected && !w.connected; }
    boolean usbNeedsPermission() { GpsWorker w = gps; return w != null && !w.bluetooth && w.usbNeedsPermission; }
    String gpsStatus() { GpsWorker w = gps; return w == null ? "off" : w.status; }
    String gpsLinkName() { GpsLink l = gpsLink; return l == null ? null : l.describe(); }
    double gpsSecondsSinceData() {
        GpsWorker w = gps;
        if (w == null || w.lastDataNanos == 0) return -1;
        return (SystemClock.elapsedRealtimeNanos() - w.lastDataNanos) / 1e9;
    }
    NtripClient caster() { return caster; }
    boolean loggingCorrections() { return logCorrections && caster != null; }
    Gga fix() { return lastFix; }
    /** The latest fix if it is under 5 s old, else null. */
    TrackPoint currentFix() {
        if (System.nanoTime() - lastFixNanos > FIX_FRESH_NS) return null;
        return TrackPoint.from(lastFix, SystemClock.elapsedRealtime());
    }
    TrackPoint firstFix() { return firstFix; }
    TrackPoint[] trail() { synchronized (trail) { return trail.toArray(new TrackPoint[0]); } }
    double poleHeight() { return poleHeight; }
    ReferenceFrame.Resolved frame() { return frame; }
    String sourcetableNote() { return sourcetableNote; }
    BaseWatch baseWatch() { return baseWatch; }
    BaseCheck layerCheck() { return layerCheck; }
    String layerCheckFile() { return layerCheckFile; }

    @Override public void onDestroy() {
        prefs.unregisterOnSharedPreferenceChangeListener(prefListener);
        stopAll();
        background.shutdownNow();
        super.onDestroy();
    }
}
