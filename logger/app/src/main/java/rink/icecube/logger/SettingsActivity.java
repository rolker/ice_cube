package rink.icecube.logger;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
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

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import rink.icecube.logger.core.Limit;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.ReferenceFrame;
import rink.icecube.logger.core.ScreenOrientation;
import rink.icecube.logger.core.SurveyLimits;

/**
 * Display, caster, GPS link, survey and map settings, saved to SharedPreferences when the
 * screen is left (layer checkboxes and Screen orientation save at once). Survey also holds
 * the capture settings: ref duration, pole check tolerance, volume keys, sound, vibration,
 * earlier refs and capture labels on the map. Coverage holds the live coverage overlay's
 * sigma target, grid and estimator settings (the overlay rebuilds when they change). Caster and link changes take effect at the next
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
    private EditText refSeconds, poleTolerance, gpsWait;
    private TextView refError, toleranceError, gpsWaitError;
    private final EditText[] limitFields = new EditText[Settings.LIMITS.length];
    private final TextView[] limitErrors = new TextView[Settings.LIMITS.length];
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
        casterAtLaunch = check(c, "Connect caster when the GPS connects", Settings.casterWithGps(p));
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
        Limit w = Limit.GPS_WAIT_MIN;
        gpsWait = field(c, w.name + ", " + w.unit + " (" + w.range() + ", default " + w.format(w.def)
                        + "): with no session, stop waiting for the GPS after this", p.getString(Settings.GPS_WAIT_MIN, w.format(w.def)),
                InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        gpsWaitError = error(c);
        gpsWait.addTextChangedListener(watcher(() -> gpsWaitError.setText(gpsWaitProblem())));
        TextView help = new TextView(this);
        help.setText("Configure sends a RAM-only UBX-CFG-VALSET (UBX+NMEA out, RTCM3 in, GGA/RMC/GST/RAWX/SFRBX) "
                + "to the receiver's USB port, or to UART1 over Bluetooth. Leave it off for the RTK Express over "
                + "Bluetooth. Start logging when the GPS connects: for USB, when Android opens the app on attach; "
                + "for Bluetooth, when the chosen device connects after the app is opened; with no session the wait ends "
                + "after Wait for the rover. Connect caster when the GPS connects: when a GPS started by Connect or "
                + "on its own first connects. Changes apply at the next Connect.");
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
        label(c, "False-fix guard: after a new RTK fix (or a jump inside one) captures wait for the settle time;"
                + " a fix that began with a jump over the warning is flagged (amber, fix_suspect).");
        for (int i = 0; i < Settings.COVERAGE_FIRST; i++) {
            if (i == 2) label(c, "Tilt capture gates (see README, Tilt capture): leans outside the tilt range are refused;"
                    + " the others warn before saving.");
            limitField(c, p, i);
        }
        volumeKeys = check(c, "Volume keys: Mark / Mode (down = MARK, up = MODE on the map)", Settings.volumeKeys(p));
        sound = check(c, "Sound for captures, modes, refs and errors", Settings.sound(p));
        vibration = check(c, "Vibration for captures, modes, refs and errors", Settings.vibration(p));
        earlierRefs = check(c, "Show earlier refs (refs.csv) on the map", Settings.showEarlierRefs(p));
        labelCaptures = check(c, "Number captures on the map (S12, O13, R14)", Settings.labelCaptures(p));
        TextView soundHelp = new TextView(this);
        soundHelp.setText("Sounds play on the media volume, which silent mode does not mute. Set it before going out: "
                + "the volume keys do not change it while the map is in front and Volume keys is on.");
        c.addView(soundHelp);

        header(c, "Coverage", pad);
        label(c, "Live coverage overlay (MENU > Coverage): RTK-fixed surface captures of this session feed a grid;"
                + " red = no capture within the capture radius, or captures that disagree; amber = node sigma above the"
                + " target; clear = at or below it. Nodes within reach of a capture are drawn. Capture radius and reach:"
                + " at most 50 grid spacings, and the radius at least 0.71 (so every capture feeds a node).");
        for (int i = Settings.COVERAGE_FIRST; i < Settings.LIMITS.length; i++) {
            if (Settings.LIMIT_KEYS[i].equals(Settings.COV_GROUND_CM)) {
                label(c, "Ground sigma: how well one shot represents the surface around it (lawn 1.5 cm,"
                        + " about 0 on ice or a hard surface). Pass wander: the RTK wander between passes that the"
                        + " live view cannot solve, added to each node's sigma before the target test.");
            }
            limitField(c, p, i);
        }
        for (int i = Settings.COVERAGE_FIRST; i < Settings.LIMITS.length; i++) {   // e.g. values saved before the bounds
            if (isCoverageGeometry(i)) limitErrors[i].setText(limitProblem(i));
        }

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

    /** The Wait for the rover problem, or "". */
    private String gpsWaitProblem() {
        try { Limit.GPS_WAIT_MIN.parse(gpsWait.getText().toString()); return ""; }
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

    /** The labelled text field for numeric setting i, with its live parse check. */
    private void limitField(LinearLayout c, SharedPreferences p, int i) {
        Limit l = Settings.LIMITS[i];
        limitFields[i] = field(c, l.name + ", " + l.unit + " (" + l.range() + ", default " + l.format(l.def) + ")",
                p.getString(Settings.LIMIT_KEYS[i], l.format(l.def)),
                InputType.TYPE_CLASS_NUMBER | (l.decimals > 0 ? InputType.TYPE_NUMBER_FLAG_DECIMAL : 0));
        limitErrors[i] = error(c);
        limitFields[i].addTextChangedListener(watcher(() -> {
            limitErrors[i].setText(limitProblem(i));
            if (isCoverageGeometry(i)) {   // the grid spacing, radius and reach are checked together
                for (int k = Settings.COVERAGE_FIRST; k < Settings.LIMITS.length; k++) {
                    if (k != i && isCoverageGeometry(k) && limitErrors[k] != null) limitErrors[k].setText(limitProblem(k));
                }
            }
        }));
    }

    private static boolean isCoverageGeometry(int i) {
        String key = Settings.LIMIT_KEYS[i];
        return key.equals(Settings.COV_CELL_M) || key.equals(Settings.COV_RADIUS_M) || key.equals(Settings.COV_REACH_M);
    }

    /**
     * The problem with limit field i, or "". The grid spacing, capture radius and reach also
     * carry Limit.coverageProblem for the three together, so none of them is saved while the
     * combination is out of bounds. A field of the three that does not parse counts with the
     * value that stays stored for it (Settings.limit), so the other two are still checked.
     */
    private String limitProblem(int i) {
        try { Settings.LIMITS[i].parse(limitFields[i].getText().toString()); }
        catch (IllegalArgumentException e) { return e.getMessage() + " (not saved)"; }
        if (!isCoverageGeometry(i)) return "";
        SharedPreferences p = Settings.prefs(this);
        String cross = Limit.coverageProblem(fieldText(Settings.COV_CELL_M), fieldText(Settings.COV_RADIUS_M),
                fieldText(Settings.COV_REACH_M), Settings.limit(p, Settings.COV_CELL_M, Limit.COV_CELL_M),
                Settings.limit(p, Settings.COV_RADIUS_M, Limit.COV_RADIUS_M),
                Settings.limit(p, Settings.COV_REACH_M, Limit.COV_REACH_M));
        return cross.isEmpty() ? "" : cross + " (not saved)";
    }

    /** The text of the limit field for `key`, or null before it is built. */
    private String fieldText(String key) {
        int k = java.util.Arrays.asList(Settings.LIMIT_KEYS).indexOf(key);
        return limitFields[k] == null ? null : limitFields[k].getText().toString();
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

    /** The shared import flow (LayerImport): picker, checks, copy to layers/, report. */
    private void pickLayers() { LayerImport.start(this, IMPORT_LAYERS); }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code == IMPORT_LAYERS) LayerImport.run(this, result, data, this::loadLayers, null);
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
        if (gpsWaitProblem().isEmpty()) {
            e.putString(Settings.GPS_WAIT_MIN, Limit.GPS_WAIT_MIN.format(Limit.GPS_WAIT_MIN.parse(text(gpsWait))));
        } else {
            Toast.makeText(this, Limit.GPS_WAIT_MIN.name + " not saved: " + gpsWaitProblem(), Toast.LENGTH_LONG).show();
        }
        if (problem(true).isEmpty()) e.putString(Settings.REF_SECONDS, text(refSeconds));
        else Toast.makeText(this, "Ref duration not saved: " + problem(true), Toast.LENGTH_LONG).show();
        if (problem(false).isEmpty()) e.putString(Settings.POLE_TOLERANCE_CM, text(poleTolerance).replace(',', '.'));
        else Toast.makeText(this, "Pole check tolerance not saved: " + problem(false), Toast.LENGTH_LONG).show();
        for (int k = 0; k < Settings.LIMITS.length; k++) {
            if (limitProblem(k).isEmpty()) {
                e.putString(Settings.LIMIT_KEYS[k], Settings.LIMITS[k].format(Settings.LIMITS[k].parse(text(limitFields[k]))));
            } else {
                Toast.makeText(this, Settings.LIMITS[k].name + " not saved: " + limitProblem(k), Toast.LENGTH_LONG).show();
            }
        }
        int i = device.getSelectedItemPosition();
        if (i >= 0 && i < addresses.size()) {
            e.putString(Settings.BT_ADDRESS, addresses.get(i)).putString(Settings.BT_NAME, names.get(i).replace(" (saved)", ""));
        }
        e.apply();
    }

    private static String text(EditText e) { return e.getText().toString().trim(); }
}
