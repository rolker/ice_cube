package rink.icecube.logger;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/**
 * Caster and GPS link settings, saved to SharedPreferences when the screen is left.
 * Changes take effect at the next Connect. The password is only shown as dots.
 */
public final class SettingsActivity extends Activity {
    private EditText host, port, mount, user, pass;
    private CheckBox logCorrections, casterAtLaunch, configure, logOnGpsConnect;
    private RadioButton usb, bluetooth;
    private Spinner device;
    private TextView deviceNote;
    private final List<String> addresses = new ArrayList<>(), names = new ArrayList<>();
    private boolean configureUsb, configureBt;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        setTitle("Settings");
        SharedPreferences p = Settings.prefs(this);
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        c.setPadding(pad, pad, pad, pad);

        header(c, "Caster (NTRIP)", 0);
        host = field(c, "Host", p.getString(Settings.HOST, ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        port = field(c, "Port", p.getString(Settings.PORT, "2101"), InputType.TYPE_CLASS_NUMBER);
        mount = field(c, "Mount point", p.getString(Settings.MOUNT, ""), InputType.TYPE_CLASS_TEXT);
        user = field(c, "User", p.getString(Settings.USER, ""), InputType.TYPE_CLASS_TEXT);
        pass = field(c, "Password", p.getString(Settings.PASS, ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        logCorrections = check(c, "Log corrections", Settings.logCorrections(p));
        casterAtLaunch = check(c, "Connect caster at app launch", p.getBoolean(Settings.CASTER_AT_LAUNCH, false));

        header(c, "GPS link", pad);
        RadioGroup g = new RadioGroup(this);
        usb = new RadioButton(this);
        usb.setText("USB (receiver's own USB port)");
        usb.setId(View.generateViewId());
        bluetooth = new RadioButton(this);
        bluetooth.setText("Bluetooth (paired device)");
        bluetooth.setId(View.generateViewId());
        g.addView(usb);
        g.addView(bluetooth);
        c.addView(g);
        device = new Spinner(this);
        c.addView(device);
        deviceNote = new TextView(this);
        c.addView(deviceNote);
        configureUsb = Settings.configure(p, false);
        configureBt = Settings.configure(p, true);
        boolean bt = Settings.bluetooth(p);
        (bt ? bluetooth : usb).setChecked(true);
        configure = check(c, "Configure receiver (RAM only)", bt ? configureBt : configureUsb);
        logOnGpsConnect = check(c, "Start logging when the GPS connects", p.getBoolean(Settings.LOG_ON_GPS_CONNECT, false));
        TextView help = new TextView(this);
        help.setText("Configure sends a RAM-only UBX-CFG-VALSET (UBX+NMEA out, RTCM3 in, GGA/RMC/GST/RAWX/SFRBX) "
                + "to the receiver's USB port, or to UART1 over Bluetooth. Leave it off for the RTK Express over "
                + "Bluetooth. Start logging when the GPS connects: for USB, when Android opens the app on attach; "
                + "for Bluetooth, when the chosen device connects after the app is opened. Changes apply at the next Connect.");
        help.setPadding(0, pad / 2, 0, 0);
        c.addView(help);

        g.setOnCheckedChangeListener((grp, id) -> {
            // Each link remembers its own Configure choice.
            boolean nowBt = id == bluetooth.getId();
            if (nowBt) { configureUsb = configure.isChecked(); configure.setChecked(configureBt); }
            else { configureBt = configure.isChecked(); configure.setChecked(configureUsb); }
            updateDeviceVisibility();
        });
        ScrollView s = new ScrollView(this);
        s.addView(c);
        setContentView(s);
        if (Build.VERSION.SDK_INT >= 31 && !MainActivity.bluetoothAllowed(this)) {
            requestPermissions(new String[] {Manifest.permission.BLUETOOTH_CONNECT}, 2);
        }
        loadDevices();
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) { loadDevices(); }

    private void header(LinearLayout c, String text, int top) {
        TextView h = new TextView(this);
        h.setText(text);
        h.setTextSize(20);
        h.setTypeface(Typeface.DEFAULT_BOLD);
        h.setPadding(0, top, 0, 0);
        c.addView(h);
    }

    private EditText field(LinearLayout c, String label, String value, int type) {
        TextView l = new TextView(this);
        l.setText(label);
        c.addView(l);
        EditText e = new EditText(this);
        e.setHint(label);
        e.setText(value);
        e.setInputType(type);
        e.setSingleLine(true);
        c.addView(e);
        return e;
    }

    private CheckBox check(LinearLayout c, String text, boolean on) {
        CheckBox x = new CheckBox(this);
        x.setText(text);
        x.setChecked(on);
        c.addView(x);
        return x;
    }

    /** Lists bonded devices. No discovery: pair the receiver in Android settings first. */
    @SuppressLint("MissingPermission")
    private void loadDevices() {
        addresses.clear();
        names.clear();
        String saved = Settings.prefs(this).getString(Settings.BT_ADDRESS, null);
        String note = null;
        BluetoothManager bm = (BluetoothManager) getSystemService(Context.BLUETOOTH_SERVICE);
        BluetoothAdapter a = bm == null ? null : bm.getAdapter();
        if (a == null) note = "This phone has no Bluetooth.";
        else if (!MainActivity.bluetoothAllowed(this)) note = "Allow the Nearby devices permission to list paired devices.";
        else if (!a.isEnabled()) note = "Bluetooth is off. Turn it on, then reopen Settings.";
        else {
            for (BluetoothDevice d : a.getBondedDevices()) {
                String n = d.getName();
                addresses.add(d.getAddress());
                names.add(n == null ? d.getAddress() : n);
            }
            if (addresses.isEmpty()) note = "No paired devices. Pair the receiver in Android Bluetooth settings.";
        }
        if (saved != null && !addresses.contains(saved)) {
            addresses.add(0, saved);
            names.add(0, Settings.prefs(this).getString(Settings.BT_NAME, saved) + " (saved)");
        }
        List<String> labels = new ArrayList<>();
        for (int i = 0; i < names.size(); i++) labels.add(names.get(i) + "  " + addresses.get(i));
        ArrayAdapter<String> ad = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
        ad.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        device.setAdapter(ad);
        int sel = saved == null ? -1 : addresses.indexOf(saved);
        if (sel >= 0) device.setSelection(sel);
        deviceNote.setText(note == null ? "" : note);
        updateDeviceVisibility();
    }

    private void updateDeviceVisibility() {
        int v = bluetooth.isChecked() ? View.VISIBLE : View.GONE;
        device.setVisibility(v);
        deviceNote.setVisibility(v);
    }

    @Override protected void onPause() {
        super.onPause();
        boolean bt = bluetooth.isChecked();
        if (bt) configureBt = configure.isChecked(); else configureUsb = configure.isChecked();
        SharedPreferences.Editor e = Settings.prefs(this).edit()
                .putString(Settings.HOST, text(host)).putString(Settings.PORT, text(port))
                .putString(Settings.MOUNT, text(mount)).putString(Settings.USER, text(user))
                .putString(Settings.PASS, pass.getText().toString())
                .putBoolean(Settings.LOG_CORRECTIONS, logCorrections.isChecked())
                .putBoolean(Settings.CASTER_AT_LAUNCH, casterAtLaunch.isChecked())
                .putString(Settings.TRANSPORT, bt ? Settings.BLUETOOTH : Settings.USB)
                .putBoolean(Settings.CONFIGURE_USB, configureUsb).putBoolean(Settings.CONFIGURE_BT, configureBt)
                .putBoolean(Settings.LOG_ON_GPS_CONNECT, logOnGpsConnect.isChecked());
        int i = device.getSelectedItemPosition();
        if (i >= 0 && i < addresses.size()) {
            e.putString(Settings.BT_ADDRESS, addresses.get(i)).putString(Settings.BT_NAME, names.get(i).replace(" (saved)", ""));
        }
        e.apply();
    }

    private static String text(EditText e) { return e.getText().toString().trim(); }
}
