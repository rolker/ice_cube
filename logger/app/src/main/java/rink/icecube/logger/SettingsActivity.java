package rink.icecube.logger;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.text.Editable;
import android.text.TextWatcher;
import android.widget.AdapterView;
import android.widget.Button;
import android.widget.Toast;
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

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import rink.icecube.logger.core.GeoJson;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.ReferenceFrame;
import rink.icecube.logger.core.ScreenOrientation;
import rink.icecube.logger.core.SurveyLimits;

/**
 * Display, caster, GPS link, survey and map settings, saved to SharedPreferences when the
 * screen is left (layer checkboxes and Screen orientation save at once). Survey also holds
 * the capture settings: ref duration, pole check tolerance, volume keys, sound, vibration,
 * earlier refs and capture labels on the map. Caster and link changes take effect at the next
 * Connect; pole height and Frame changes apply at once and are logged in a running session.
 * The password is only shown as dots.
 */
public final class SettingsActivity extends Activity {
    private EditText host, port, mount, user, pass;
    private CheckBox logCorrections, casterAtLaunch, configure, logOnGpsConnect;
    private RadioButton usb, bluetooth;
    private Spinner device;
    private TextView deviceNote;
    private final List<String> addresses = new ArrayList<>(), names = new ArrayList<>();
    private boolean configureUsb, configureBt;
    private Spinner frameSpinner, poleSpinner;
    private EditText frameEpoch, poleOther;
    private TextView frameError, poleError, layersHeader;
    private LinearLayout layerList;
    private Spinner orientationSpinner;
    private EditText refSeconds, poleTolerance;
    private TextView refError, toleranceError;
    private CheckBox volumeKeys, sound, vibration, earlierRefs, labelCaptures;
    private static final int IMPORT_LAYERS = 7;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Screen.applyOrientation(this);
        setTitle("Settings");
        SharedPreferences p = Settings.prefs(this);
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        c.setPadding(pad, pad, pad, pad);

        header(c, "Display", 0);
        label(c, "Screen orientation (lock it to how the phone sits on the pole; Auto follows the system)");
        orientationSpinner = spinner(c, ScreenOrientation.LABELS);
        orientationSpinner.setSelection(ScreenOrientation.index(p.getString(Settings.ORIENTATION, ScreenOrientation.DEFAULT)));
        orientationSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> a, View v, int pos, long id) {
                Settings.prefs(SettingsActivity.this).edit().putString(Settings.ORIENTATION, ScreenOrientation.KEYS[pos]).apply();
                Screen.applyOrientation(SettingsActivity.this);   // at once, on this screen too
            }
            @Override public void onNothingSelected(AdapterView<?> a) { }
        });

        header(c, "Caster (NTRIP)", pad);
        host = field(c, "Host", p.getString(Settings.HOST, ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        port = field(c, "Port", p.getString(Settings.PORT, "2101"), InputType.TYPE_CLASS_NUMBER);
        mount = field(c, "Mount point", p.getString(Settings.MOUNT, ""), InputType.TYPE_CLASS_TEXT);
        user = field(c, "User", p.getString(Settings.USER, ""), InputType.TYPE_CLASS_TEXT);
        pass = field(c, "Password", p.getString(Settings.PASS, ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        logCorrections = check(c, "Log corrections", Settings.logCorrections(p));
        casterAtLaunch = check(c, "Connect caster at app launch", p.getBoolean(Settings.CASTER_AT_LAUNCH, false));
        label(c, "Frame of the base coordinates (used when the sourcetable names none)");
        frameSpinner = spinner(c, ReferenceFrame.SETTING_NAMES);
        int fi = java.util.Arrays.asList(ReferenceFrame.SETTING_NAMES).indexOf(p.getString(Settings.FRAME, ReferenceFrame.SETTING_NAMES[0]));
        frameSpinner.setSelection(Math.max(0, fi));
        frameEpoch = field(c, "Frame epoch (decimal year, optional)", p.getString(Settings.FRAME_EPOCH, ""),
                InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        frameError = error(c);
        frameEpoch.addTextChangedListener(watcher(() -> frameError.setText(epochProblem())));
        frameError.setText(epochProblem());

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

        header(c, "Survey", pad);
        label(c, "Pole height (ground mark to antenna reference point)");
        String[] poles = new String[PoleHeight.PRESETS.length + 1];
        for (int i = 0; i < PoleHeight.PRESETS.length; i++) poles[i] = String.format(Locale.US, "%.1f m", PoleHeight.PRESETS[i]);
        poles[poles.length - 1] = "Other\u2026";
        poleSpinner = spinner(c, poles);
        double pole = Settings.poleHeight(p);
        int pi = PoleHeight.presetIndex(pole);
        poleOther = field(c, "Pole height, m (0 to 5, up to 3 decimals)", pi >= 0 ? "" : PoleHeight.format(pole),
                InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        poleError = error(c);
        poleSpinner.setSelection(pi >= 0 ? pi : poles.length - 1);
        poleSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> a, View v, int pos, long id) { updatePoleVisibility(); }
            @Override public void onNothingSelected(AdapterView<?> a) { }
        });
        poleOther.addTextChangedListener(watcher(this::updatePoleVisibility));
        updatePoleVisibility();
        refSeconds = field(c, "Ref duration, s (10 to 300): fixed epochs averaged for a Ref capture",
                p.getString(Settings.REF_SECONDS, String.valueOf(SurveyLimits.REF_SECONDS_DEFAULT)), InputType.TYPE_CLASS_NUMBER);
        refError = error(c);
        refSeconds.addTextChangedListener(watcher(() -> refError.setText(problem(true))));
        poleTolerance = field(c, "Pole check tolerance, cm (0.5 to 20): ground height difference to the last visit",
                p.getString(Settings.POLE_TOLERANCE_CM, "3"), InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        toleranceError = error(c);
        poleTolerance.addTextChangedListener(watcher(() -> toleranceError.setText(problem(false))));
        volumeKeys = check(c, "Volume keys: Mark / Mode (down = MARK, up = MODE on the map)", Settings.volumeKeys(p));
        sound = check(c, "Sound for captures, modes, refs and errors", Settings.sound(p));
        vibration = check(c, "Vibration for captures, modes, refs and errors", Settings.vibration(p));
        earlierRefs = check(c, "Show earlier refs (refs.csv) on the map", Settings.showEarlierRefs(p));
        labelCaptures = check(c, "Number captures on the map (S12, O13, R14)", Settings.labelCaptures(p));
        TextView soundHelp = new TextView(this);
        soundHelp.setText("Sounds play on the media volume, which silent mode does not mute. Set it before going out: "
                + "the volume keys do not change it while the map is in front and Volume keys is on.");
        c.addView(soundHelp);

        header(c, "Map", pad);
        layersHeader = new TextView(this);
        c.addView(layersHeader);
        layerList = new LinearLayout(this);
        layerList.setOrientation(LinearLayout.VERTICAL);
        c.addView(layerList);
        LinearLayout row = new LinearLayout(this);
        Button imp = new Button(this);
        imp.setText("Import layer files\u2026");
        imp.setOnClickListener(v -> pickLayers());
        row.addView(imp, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        Button reload = new Button(this);
        reload.setText("Reload layers");
        reload.setOnClickListener(v -> { Layers.reload(); loadLayers(); });
        row.addView(reload, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        c.addView(row);
        loadLayers();

        g.setOnCheckedChangeListener((grp, id) -> {
            // Each link remembers its own Configure choice.
            boolean nowBt = id == bluetooth.getId();
            if (nowBt) { configureUsb = configure.isChecked(); configure.setChecked(configureBt); }
            else { configureBt = configure.isChecked(); configure.setChecked(configureUsb); }
            updateDeviceVisibility();
        });
        // Rotation keeps this screen (configChanges, see Screen), so typed values are not lost.
        // On a wide landscape screen the fields stay at a readable width, centred.
        Screen.MaxWidth m = new Screen.MaxWidth(this);
        m.addView(c, new android.widget.FrameLayout.LayoutParams(android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.WRAP_CONTENT));
        ScrollView s = new ScrollView(this);
        s.addView(m, new android.widget.FrameLayout.LayoutParams(android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.WRAP_CONTENT, android.view.Gravity.CENTER_HORIZONTAL));
        setContentView(s);
        if (Build.VERSION.SDK_INT >= 31 && !Control.bluetoothAllowed(this)) {
            requestPermissions(new String[] {Manifest.permission.BLUETOOTH_CONNECT}, 2);
        }
        loadDevices();
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) { loadDevices(); }

    private void label(LinearLayout c, String text) {
        TextView l = new TextView(this);
        l.setText(text);
        c.addView(l);
    }

    private Spinner spinner(LinearLayout c, String[] items) {
        Spinner sp = new Spinner(this);
        ArrayAdapter<String> ad = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, items);
        ad.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        sp.setAdapter(ad);
        c.addView(sp);
        return sp;
    }

    private TextView error(LinearLayout c) {
        TextView t = new TextView(this);
        t.setTextColor(0xFFC62828);
        c.addView(t);
        return t;
    }

    private static TextWatcher watcher(Runnable r) {
        return new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int n) { }
            @Override public void onTextChanged(CharSequence s, int a, int b, int n) { }
            @Override public void afterTextChanged(Editable e) { r.run(); }
        };
    }

    private String epochProblem() {
        try { ReferenceFrame.parseEpoch(frameEpoch.getText().toString()); return ""; }
        catch (IllegalArgumentException e) { return e.getMessage() + " (not saved)"; }
    }

    /** The ref duration (ref) or tolerance problem, or "". */
    private String problem(boolean ref) {
        try {
            if (ref) SurveyLimits.parseRefSeconds(refSeconds.getText().toString());
            else SurveyLimits.parseToleranceCm(poleTolerance.getText().toString());
            return "";
        } catch (IllegalArgumentException e) { return e.getMessage() + " (not saved)"; }
    }

    private boolean poleIsOther() { return poleSpinner.getSelectedItemPosition() >= PoleHeight.PRESETS.length; }

    private void updatePoleVisibility() {
        boolean other = poleIsOther();
        poleOther.setVisibility(other ? View.VISIBLE : View.GONE);
        String err = "";
        if (other) {
            try { PoleHeight.parse(poleOther.getText().toString()); }
            catch (IllegalArgumentException e) { err = e.getMessage() + " (not saved)"; }
        }
        poleError.setText(err);
        poleError.setVisibility(err.isEmpty() ? View.GONE : View.VISIBLE);
    }

    // ---- map layers ----

    private void loadLayers() {
        layersHeader.setText("Loading layers\u2026");
        Layers.getAsync(this, this::showLayers);
    }

    private void showLayers(List<Layers.LayerFile> files) {
        layerList.removeAllViews();
        layersHeader.setText("Layer files (*.geojson, *.json) in " + Layers.importDir(this)
                + (files.isEmpty() ? "\nNone found. Use Import to copy files here." : ""));
        Set<String> off = Settings.layersOff(Settings.prefs(this));
        for (Layers.LayerFile lf : files) {
            CheckBox cb = new CheckBox(this);
            cb.setText(lf.name());
            cb.setChecked(!off.contains(lf.name()));
            cb.setEnabled(lf.layer != null);
            cb.setOnCheckedChangeListener((b, on) -> Settings.setLayerOn(Settings.prefs(this), lf.name(), on));
            layerList.addView(cb);
            TextView t = new TextView(this);
            t.setText("  " + lf.summary());
            if (lf.layer == null) t.setTextColor(0xFFC62828);
            layerList.addView(t);
        }
    }

    /** Opens the system file picker (Drive and Downloads appear there); picked files are copied to layers/. */
    private void pickLayers() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try { startActivityForResult(i, IMPORT_LAYERS); }
        catch (android.content.ActivityNotFoundException e) { Toast.makeText(this, "No file picker", Toast.LENGTH_LONG).show(); }
    }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code != IMPORT_LAYERS || result != RESULT_OK || data == null) return;
        List<Uri> uris = new ArrayList<>();
        if (data.getClipData() != null) {
            for (int i = 0; i < data.getClipData().getItemCount(); i++) uris.add(data.getClipData().getItemAt(i).getUri());
        } else if (data.getData() != null) uris.add(data.getData());
        File dir = Layers.importDir(this);
        new Thread(() -> {
            StringBuilder report = new StringBuilder();
            for (Uri u : uris) report.append(importOne(u, dir)).append('\n');
            Layers.reload();
            runOnUiThread(() -> {
                if (isFinishing()) return;
                new AlertDialog.Builder(this).setTitle("Import layers").setMessage(report.toString().trim())
                        .setPositiveButton("OK", null).show();
                loadLayers();
            });
        }, "import-layers").start();
    }

    /** Copies one picked file after checking that it parses. Returns a line for the report. */
    private String importOne(Uri u, File dir) {
        String name = displayName(u);
        try {
            String text;
            try (InputStream in = getContentResolver().openInputStream(u)) {
                if (in == null) throw new IOException("cannot open");
                text = Layers.readText(in, Layers.MAX_BYTES);
            }
            GeoJson.Layer l = GeoJson.parse(text);
            String safe = name.replaceAll("[^A-Za-z0-9._-]", "_");
            if (!Layers.isLayerName(safe)) safe = safe + ".geojson";
            File out = new File(dir, safe);
            try (OutputStream o = new FileOutputStream(out)) { o.write(text.getBytes(StandardCharsets.UTF_8)); }
            return String.format(Locale.US, "%s: %d features%s", safe, l.features.size(),
                    l.warnings.isEmpty() ? "" : ", " + l.warnings.size() + " skipped");
        } catch (IOException | SecurityException e) {
            return name + ": not imported (" + e.getMessage() + ")";
        } catch (GeoJson.GeoJsonException e) {
            return name + ": not imported, not a GeoJSON layer (" + e.getMessage() + ")";
        }
    }

    private String displayName(Uri u) {
        try (Cursor c = getContentResolver().query(u, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getString(0);
        } catch (RuntimeException ignored) { }
        String last = u.getLastPathSegment();
        return last == null ? "layer.geojson" : last.substring(last.lastIndexOf('/') + 1);
    }

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
        // In landscape, keep the screen visible while typing instead of a full-screen editor.
        e.setImeOptions(e.getImeOptions() | android.view.inputmethod.EditorInfo.IME_FLAG_NO_EXTRACT_UI);
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
        else if (!Control.bluetoothAllowed(this)) note = "Allow the Nearby devices permission to list paired devices.";
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

    @Override protected void onResume() {
        super.onResume();
        Screen.applyOrientation(this);
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
                .putBoolean(Settings.LOG_ON_GPS_CONNECT, logOnGpsConnect.isChecked())
                .putString(Settings.FRAME, ReferenceFrame.SETTING_NAMES[Math.max(0, frameSpinner.getSelectedItemPosition())]);
        if (epochProblem().isEmpty()) e.putString(Settings.FRAME_EPOCH, text(frameEpoch));
        else Toast.makeText(this, "Frame epoch not saved: " + epochProblem(), Toast.LENGTH_LONG).show();
        int pi = poleSpinner.getSelectedItemPosition();
        try {
            double pole = pi < PoleHeight.PRESETS.length ? PoleHeight.PRESETS[pi] : PoleHeight.parse(poleOther.getText().toString());
            e.putString(Settings.POLE_HEIGHT, PoleHeight.format(pole));
        } catch (IllegalArgumentException x) {
            Toast.makeText(this, "Pole height not saved: " + x.getMessage(), Toast.LENGTH_LONG).show();
        }
        e.putBoolean(Settings.VOLUME_KEYS, volumeKeys.isChecked()).putBoolean(Settings.SOUND, sound.isChecked())
                .putBoolean(Settings.VIBRATION, vibration.isChecked())
                .putBoolean(Settings.SHOW_EARLIER_REFS, earlierRefs.isChecked())
                .putBoolean(Settings.LABEL_CAPTURES, labelCaptures.isChecked());
        if (problem(true).isEmpty()) e.putString(Settings.REF_SECONDS, text(refSeconds));
        else Toast.makeText(this, "Ref duration not saved: " + problem(true), Toast.LENGTH_LONG).show();
        if (problem(false).isEmpty()) e.putString(Settings.POLE_TOLERANCE_CM, text(poleTolerance).replace(',', '.'));
        else Toast.makeText(this, "Pole check tolerance not saved: " + problem(false), Toast.LENGTH_LONG).show();
        int i = device.getSelectedItemPosition();
        if (i >= 0 && i < addresses.size()) {
            e.putString(Settings.BT_ADDRESS, addresses.get(i)).putString(Settings.BT_NAME, names.get(i).replace(" (saved)", ""));
        }
        e.apply();
    }

    private static String text(EditText e) { return e.getText().toString().trim(); }
}
