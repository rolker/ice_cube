package rink.icecube.logger;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.Locale;

import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.StreamParser;

/** One screen: NTRIP settings, Start and Stop, and live status. */
public final class MainActivity extends Activity {
    private EditText host, port, mount, user, pass;
    private TextView status, big;
    private Button start, tap;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private UsbDevice pending;

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            refresh();
            ui.postDelayed(this, 500);
        }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1);
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        c.setPadding(pad, pad, pad, pad);
        SharedPreferences p = prefs();
        host = field(c, "NTRIP host (blank for no corrections)", p.getString("host", ""), InputType.TYPE_CLASS_TEXT);
        port = field(c, "Port", p.getString("port", "2101"), InputType.TYPE_CLASS_NUMBER);
        mount = field(c, "Mount point", p.getString("mount", ""), InputType.TYPE_CLASS_TEXT);
        user = field(c, "User", p.getString("user", ""), InputType.TYPE_CLASS_TEXT);
        pass = field(c, "Password", p.getString("pass", ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        start = new Button(this);
        start.setOnClickListener(v -> onStartStop());
        c.addView(start);
        tap = new Button(this);
        tap.setText("Mark control point");
        tap.setOnClickListener(v -> {
            LoggerService s = LoggerService.current;
            if (s != null && s.isRunning()) s.mark("control");
        });
        c.addView(tap);
        big = new TextView(this);
        big.setTextSize(30);
        big.setTypeface(Typeface.DEFAULT_BOLD);
        big.setPadding(0, pad, 0, pad / 2);
        c.addView(big);
        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextIsSelectable(true);
        c.addView(status);
        ScrollView s = new ScrollView(this);
        s.addView(c);
        setContentView(s);
    }

    private EditText field(LinearLayout c, String hint, String value, int type) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setText(value);
        e.setInputType(type);
        e.setSingleLine(true);
        c.addView(e);
        return e;
    }

    private SharedPreferences prefs() { return getSharedPreferences("settings", Context.MODE_PRIVATE); }

    @Override protected void onResume() { super.onResume(); ui.post(tick); }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }

    private UsbDevice findGps(UsbManager m) {
        UsbDevice any = null;
        for (UsbDevice d : m.getDeviceList().values()) {
            if (d.getVendorId() == 0x1546) return d;
            if (UsbLink.isCdc(d)) any = d;
        }
        return any;
    }

    private void onStartStop() {
        LoggerService s = LoggerService.current;
        if (s != null && s.isRunning()) {
            stopService(new Intent(this, LoggerService.class));
            return;
        }
        prefs().edit().putString("host", text(host)).putString("port", text(port)).putString("mount", text(mount))
                .putString("user", text(user)).putString("pass", text(pass)).apply();
        UsbManager m = (UsbManager) getSystemService(Context.USB_SERVICE);
        UsbDevice d = findGps(m);
        if (d == null) { big.setText("No GPS on USB"); return; }
        if (m.hasPermission(d)) { launch(d); return; }
        pending = d;
        Intent i = new Intent("rink.icecube.logger.USB_PERMISSION").setPackage(getPackageName());
        m.requestPermission(d, PendingIntent.getBroadcast(this, 0, i, PendingIntent.FLAG_MUTABLE));
    }

    private void launch(UsbDevice d) {
        pending = null;
        int portNum = 2101;
        try { portNum = Integer.parseInt(text(port)); } catch (NumberFormatException ignored) { }
        Intent i = new Intent(this, LoggerService.class)
                .putExtra(LoggerService.EXTRA_DEVICE, d.getDeviceName())
                .putExtra(LoggerService.EXTRA_HOST, text(host)).putExtra(LoggerService.EXTRA_PORT, portNum)
                .putExtra(LoggerService.EXTRA_MOUNT, text(mount)).putExtra(LoggerService.EXTRA_USER, text(user))
                .putExtra(LoggerService.EXTRA_PASS, text(pass));
        startForegroundService(i);
    }

    private static String text(EditText e) { return e.getText().toString().trim(); }

    private void refresh() {
        UsbManager m = (UsbManager) getSystemService(Context.USB_SERVICE);
        if (pending != null && m.hasPermission(pending)) launch(pending);
        LoggerService s = LoggerService.current;
        boolean on = s != null && s.isRunning();
        start.setText(on ? "Stop" : "Start");
        tap.setEnabled(on);
        if (s == null) {
            big.setText("Ready");
            UsbDevice d = findGps(m);
            status.setText(d == null ? "Connect the GPS USB port to the phone." :
                    String.format(Locale.US, "USB device %04x:%04x found.", d.getVendorId(), d.getProductId()));
            return;
        }
        Gga g = s.fix();
        StreamParser p = s.parser;
        NtripClient n = s.ntrip();
        big.setText(!on ? "Stopped" : g == null ? "Waiting for GPS" : g.qualityName());
        StringBuilder t = new StringBuilder();
        long secs = s.startMs == 0 ? 0 : (System.currentTimeMillis() - s.startMs) / 1000;
        t.append(String.format(Locale.US, "time      %d:%02d\n", secs / 60, secs % 60));
        if (g != null) t.append(String.format(Locale.US, "sats %d  age %.1f s\n", g.sats, g.age));
        t.append(String.format(Locale.US, "gps bytes %.1f MB\n", p.bytes / 1e6));
        t.append(String.format(Locale.US, "RAWX      %d  lost %.2f%%  max gap %.2f s\n", p.rawxEpochs, p.rawxLossPct(), p.rawxMaxGap));
        t.append(String.format(Locale.US, "GGA       %d  lost %.2f%%  max gap %.2f s\n", p.ggaCount, p.ggaLossPct(), p.ggaMaxGap));
        t.append(String.format(Locale.US, "bad UBX %d  bad NMEA %d\n", p.ubxBad, p.nmeaBad));
        t.append(String.format(Locale.US, "config acks %d  naks %d\n", p.valsetAcks, p.valsetNaks));
        if (n != null) t.append(String.format(Locale.US, "ntrip     %s\nrtcm      %.1f kB\n", n.status, n.rtcmBytes / 1e3));
        t.append(String.format(Locale.US, "imu       %d samples\n", s.imuSamples()));
        if (s.dir != null) t.append("folder    ").append(s.dir).append('\n');
        if (s.error != null) t.append("ERROR     ").append(s.error).append('\n');
        status.setText(t);
    }
}
