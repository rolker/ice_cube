package rink.icecube.logger;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.text.InputType;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.text.style.RelativeSizeSpan;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import rink.icecube.logger.core.Coverage;
import rink.icecube.logger.core.CoverageRaster;
import rink.icecube.logger.core.CoverageWorker;
import rink.icecube.logger.core.GeoJson;
import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.LinkState;
import rink.icecube.logger.core.LocalFrame;
import rink.icecube.logger.core.MapOrigin;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.PointRow;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.SurveyMode;
import rink.icecube.logger.core.TrackPoint;
import rink.icecube.logger.core.VoidRule;

/**
 * The main screen: the local map, full screen, for a phone on a survey pole used with gloves.
 *
 * - Status strip along the top (one line in landscape, two in portrait), every item a
 *   button: fix chip with correction age (opens Details), GPS and caster dots (tap to
 *   connect or disconnect; asks first while a session records), pole height (picker),
 *   recording dot with elapsed time and capture count (start, or stop after asking).
 * - Red warnings bar under the strip only when there is a warning (see Status.warnings),
 *   and a short green or red notice after a capture, a ref or a pole check.
 * - Four large buttons, a column on the right in landscape or a row at the bottom in
 *   portrait: MODE (Surface, Object, Ref), MARK, PHOTO and MENU (a panel from the right
 *   with the links, session, pole height, layers, share, sessions, Details and Settings).
 * - Volume down = MARK, volume up = MODE while this screen is in front (Settings).
 * - Tilt capture: long-press MARK (or hold volume down, or MENU > Tilt capture) starts a set
 *   in the current mode; then MODE reads LEAN (and volume down, or a long-press of MARK,
 *   takes a lean), MARK or volume up finishes (asking first if a gate warns), and back or
 *   MENU > Cancel tilt capture drops it. The map shows a compass rose of the leans.
 *
 * Map origin ({@link MapOrigin}): the first point of a kind="origin" feature in the
 * switched-on layers (the project frame, e.g. monument G); else of a kind="base" feature;
 * else the session's first GPS fix; else, so layers can still be viewed, the first vertex
 * of the first layer (the map moves to the fix origin once one arrives). The info box and
 * the capture notices give positions as x/y/z in that frame. The screen stays on.
 *
 * Coverage overlay (MENU > Coverage, on by default): a {@link Coverage} fed with this
 * session's RTK-fixed surface captures as they arrive (rows past the last one fed, on each
 * refresh), rebuilt from the session's rows when the map frame, the session or a Settings >
 * Coverage value changes. Earlier sessions are not loaded. The feeds and rebuilds run in
 * order on one "coverage" thread ({@link CoverageWorker}), which owns the Coverage; a new
 * rebuild cancels the one in flight, and MapView draws the frames it sends back. When a
 * rebuild starts with settings or an origin not yet logged for the session, events.csv gets
 * a `coverage ...` line with the settings and the origin (Coverage.Params.eventText).
 *
 * It is also the launcher and USB_DEVICE_ATTACHED activity, with the autostart options.
 */
public final class MainActivity extends Activity {
    private static final int REQ_PHOTO = 21, REQ_IMPORT_LAYERS = 22;
    private static final int STRIP_BG = 0xFFECEFF1, TEXT = 0xFF212121, AMBER = 0xFFFFA000;

    private MapView map;
    private TextView info, warnBar, noticeBar;
    private TextView fixChip, gpsItem, casterItem, poleItem, recItem;
    private Button modeB, markB, photoB, menuB, centerB, fitB, undoB;
    /** The notice bar and its UNDO button, side by side. */
    private LinearLayout noticeRow;
    /** The notice UNDO is shown for, and when it first showed (elapsedRealtime ns), for the arm delay. */
    private PointRow undoAt;   // the capture UNDO is shown for (a notice's row, by identity)
    private long undoShownNs;
    /** PHOTO's second line as last shown ("" for none). */
    private String photoTargetShown = "";
    private FrameLayout mapFrame, drawer;
    private LinearLayout drawerItems;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private List<Layers.LayerFile> files = new ArrayList<>();
    private String layerKey = "";
    private int layerErrors;
    private MapOrigin.Candidate originPoint, basePoint, firstVertex;   // from the switched-on layers
    private String originKey = "";
    /** The origin the map shows, for Details. */
    private static volatile MapOrigin shownOrigin;

    static MapOrigin shownOrigin() { return shownOrigin; }

    // The coverage overlay: a single worker thread owns the Coverage and its raster; frames
    // come back to the UI thread and are drawn when still current (see CoverageWorker).
    private final ExecutorService coverageThread = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "coverage");
        t.setPriority(Thread.NORM_PRIORITY - 1);
        return t;
    });
    private final CoverageWorker coverageWorker = new CoverageWorker(coverageThread, new CoverageWorker.Sink() {
        @Override public void frame(CoverageRaster.Frame f) { onCoverageFrame(f); }
        @Override public void failed(Throwable t) { onCoverageFailed(t); }
    });
    private boolean coverageOn = true;
    private Coverage.Params coverageParams = Coverage.Params.defaults();
    /** The last coverage settings line written to events.csv, and for which session. */
    private Object coverageEventSession;
    private String coverageEventLine;

    private int refsLoadedGen = -1;
    private String refsLoadedFor = "";
    private boolean refsWanted;

    // The photo being taken (also kept across a recreation while the camera is open).
    private String photoPath, photoUri;
    private int photoAttach;
    private long photoPressNs;

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            refresh();
            ui.postDelayed(this, 250);
        }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Screen.applyOrientation(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        int pad = Screen.dp(this, 8);

        map = new MapView(this);
        map.setId(R.id.map);   // lets the map save its view if the screen is ever recreated
        info = new TextView(this);
        info.setTypeface(Typeface.MONOSPACE);
        info.setTextSize(12);
        info.setTextColor(TEXT);
        info.setBackgroundColor(0xD9FFFFFF);
        info.setPadding(pad / 2, pad / 4, pad / 2, pad / 4);
        centerB = smallButton("Center", v -> map.recenter());
        fitB = smallButton("Fit", v -> map.fit());
        mapFrame = new FrameLayout(this);
        mapFrame.addView(map, new FrameLayout.LayoutParams(-1, -1));
        FrameLayout.LayoutParams ip = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        ip.setMargins(pad, pad, pad, pad);
        mapFrame.addView(info, ip);
        LinearLayout zoom = new LinearLayout(this);
        zoom.setOrientation(LinearLayout.VERTICAL);
        zoom.addView(centerB, new LinearLayout.LayoutParams(Screen.dp(this, 72), Screen.dp(this, 48)));
        zoom.addView(fitB, new LinearLayout.LayoutParams(Screen.dp(this, 72), Screen.dp(this, 48)));
        FrameLayout.LayoutParams zp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        zp.setMargins(pad, Screen.dp(this, 32), pad, pad);
        mapFrame.addView(zoom, zp);

        warnBar = bar(0xFFC62828);
        warnBar.setOnClickListener(v -> startActivity(new Intent(this, DetailsActivity.class)));
        noticeBar = bar(0xFF2E7D32);
        undoB = smallButton("UNDO", v -> onUndo());
        undoB.setTextSize(16);
        undoB.setTypeface(Typeface.DEFAULT_BOLD);
        undoB.setVisibility(View.GONE);
        noticeRow = new LinearLayout(this);
        noticeRow.setOrientation(LinearLayout.HORIZONTAL);
        noticeRow.setBackgroundColor(STRIP_BG);
        noticeRow.addView(noticeBar, new LinearLayout.LayoutParams(0, -1, 1));
        LinearLayout.LayoutParams up = new LinearLayout.LayoutParams(Screen.dp(this, 88), -1);
        up.setMargins(Screen.dp(this, 3), Screen.dp(this, 3), Screen.dp(this, 3), Screen.dp(this, 3));
        noticeRow.addView(undoB, up);
        undoB.setMinHeight(Screen.dp(this, 48));
        undoB.setMinimumHeight(Screen.dp(this, 48));

        fixChip = chip(v -> startActivity(new Intent(this, DetailsActivity.class)));
        gpsItem = chip(v -> onGpsDot());
        casterItem = chip(v -> onCasterDot());
        poleItem = chip(v -> pickPole());
        recItem = chip(v -> onRec());

        modeB = bigButton(v -> onMode());
        markB = bigButton(v -> onMark());
        markB.setOnLongClickListener(v -> { onMarkLong(); return true; });
        photoB = bigButton(v -> onPhoto());
        photoB.setText("PHOTO");
        style(photoB, MapView.PHOTO_COLOR, Color.WHITE);
        menuB = bigButton(v -> openDrawer());
        menuB.setText("☰\nMENU");
        style(menuB, 0xFF546E7A, Color.WHITE);

        drawer = new FrameLayout(this);
        drawer.setVisibility(View.GONE);
        View scrim = new View(this);
        scrim.setBackgroundColor(0x66000000);
        scrim.setOnClickListener(v -> closeDrawer());
        drawer.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        drawerItems = new LinearLayout(this);
        drawerItems.setOrientation(LinearLayout.VERTICAL);
        drawerItems.setPadding(pad, pad, pad, pad);
        ScrollView panel = new ScrollView(this);
        panel.setBackgroundColor(Color.WHITE);
        panel.setElevation(Screen.dp(this, 8));
        panel.addView(drawerItems);
        drawer.addView(panel, new FrameLayout.LayoutParams(Screen.dp(this, 300), -1, Gravity.END));

        if (b != null) {
            photoPath = b.getString("photoPath");
            photoUri = b.getString("photoUri");
            photoAttach = b.getInt("photoAttach");
            photoPressNs = b.getLong("photoPressNs");
        }
        arrange();
        updateModeButtons();
        Control.requestRuntimePermissions(this);
        if (b == null) Control.autostart(this, getIntent());
    }

    @Override protected void onSaveInstanceState(Bundle b) {
        super.onSaveInstanceState(b);
        b.putString("photoPath", photoPath);
        b.putString("photoUri", photoUri);
        b.putInt("photoAttach", photoAttach);
        b.putLong("photoPressNs", photoPressNs);
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        Control.autostart(this, i);
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        Control.permissionsAnswered(this);
    }

    /** Rotation does not recreate this screen (see Screen): the same views are rearranged. */
    @Override public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        arrange();
        refresh();
    }

    // ---- layout ----

    /**
     * Landscape: strip, warnings, notice and map on the left; the four buttons as a column
     * on the right edge over the full height; status bar hidden. Portrait: strip in two
     * rows, warnings, notice, map, then the buttons in a row along the bottom.
     */
    private void arrange() {
        boolean land = Screen.landscape(this);
        for (View v : new View[] {mapFrame, warnBar, noticeRow, fixChip, gpsItem, casterItem, poleItem, recItem,
                modeB, markB, photoB, menuB, drawer}) Screen.detach(v);
        showStatusBar(!land);
        int m = Screen.dp(this, 3);

        LinearLayout strip = new LinearLayout(this);
        strip.setOrientation(LinearLayout.VERTICAL);
        strip.setBackgroundColor(STRIP_BG);
        strip.setPadding(m, m, m, m);
        if (land) {
            strip.addView(row(m, fixChip, gpsItem, casterItem, poleItem, recItem));
        } else {
            strip.addView(row(m, fixChip, gpsItem, casterItem));
            strip.addView(row(m, poleItem, recItem));
        }

        LinearLayout left = new LinearLayout(this);
        left.setOrientation(LinearLayout.VERTICAL);
        left.addView(strip, new LinearLayout.LayoutParams(-1, -2));
        left.addView(warnBar, new LinearLayout.LayoutParams(-1, -2));
        left.addView(noticeRow, new LinearLayout.LayoutParams(-1, -2));
        left.addView(mapFrame, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(land ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        buttons.setBackgroundColor(STRIP_BG);
        buttons.setPadding(m, m, m, m);
        for (Button x : new Button[] {modeB, markB, photoB, menuB}) {
            LinearLayout.LayoutParams lp = land ? new LinearLayout.LayoutParams(-1, 0, 1)
                    : new LinearLayout.LayoutParams(0, Screen.dp(this, 76), 1);
            lp.setMargins(m, m, m, m);
            buttons.addView(x, lp);
        }

        LinearLayout content = new LinearLayout(this);
        if (land) {
            content.setOrientation(LinearLayout.HORIZONTAL);
            content.addView(left, new LinearLayout.LayoutParams(0, -1, 1));
            content.addView(buttons, new LinearLayout.LayoutParams(Screen.dp(this, 132), -1));
        } else {
            content.setOrientation(LinearLayout.VERTICAL);
            content.addView(left, new LinearLayout.LayoutParams(-1, 0, 1));
            content.addView(buttons, new LinearLayout.LayoutParams(-1, -2));
        }
        FrameLayout root = new FrameLayout(this);
        root.addView(content, new FrameLayout.LayoutParams(-1, -1));
        root.addView(drawer, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
    }

    private LinearLayout row(int m, View... items) {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        for (View v : items) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2,
                    v == recItem ? 1.4f : v == gpsItem ? 0.85f : 1f);
            lp.setMargins(m, m, m, m);
            r.addView(v, lp);
        }
        return r;
    }

    private void showStatusBar(boolean show) {
        if (Build.VERSION.SDK_INT < 30) return;
        WindowInsetsController w = getWindow().getInsetsController();
        if (w == null) return;
        if (show) w.show(WindowInsets.Type.statusBars());
        else {
            w.hide(WindowInsets.Type.statusBars());
            w.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        }
    }

    private static RippleDrawable pressable(int color, float radius) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(radius);
        return new RippleDrawable(ColorStateList.valueOf(0x40000000), g, null);
    }

    /** A strip item: at least 48 dp tall, bold, centred. */
    private TextView chip(View.OnClickListener l) {
        TextView t = new TextView(this);
        t.setTextSize(15);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(TEXT);
        t.setGravity(Gravity.CENTER);
        t.setSingleLine(true);
        t.setEllipsize(android.text.TextUtils.TruncateAt.END);
        t.setMinHeight(Screen.dp(this, 48));
        t.setPadding(Screen.dp(this, 6), 0, Screen.dp(this, 6), 0);
        t.setBackground(pressable(Color.WHITE, Screen.dp(this, 6)));
        t.setClickable(true);
        t.setOnClickListener(l);
        return t;
    }

    private TextView bar(int color) {
        TextView t = new TextView(this);
        int pad = Screen.dp(this, 8);
        t.setBackgroundColor(color);
        t.setTextColor(Color.WHITE);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextSize(15);
        t.setPadding(pad, pad / 2, pad, pad / 2);
        t.setVisibility(View.GONE);
        return t;
    }

    /** One of the four main buttons: at least 64 dp each way, large bold text. */
    private Button bigButton(View.OnClickListener l) {
        Button x = new Button(this);
        x.setMinHeight(Screen.dp(this, 64));
        x.setMinimumHeight(Screen.dp(this, 64));
        x.setMinWidth(Screen.dp(this, 64));
        x.setMinimumWidth(Screen.dp(this, 64));
        x.setTextSize(18);
        x.setTypeface(Typeface.DEFAULT_BOLD);
        x.setAllCaps(false);
        x.setStateListAnimator(null);
        x.setPadding(0, 0, 0, 0);
        x.setOnClickListener(l);
        return x;
    }

    private void style(Button b, int bg, int fg) {
        b.setBackground(pressable(bg, Screen.dp(this, 10)));
        b.setTextColor(fg);
    }

    private Button smallButton(String t, View.OnClickListener l) {
        Button x = new Button(this);
        x.setText(t);
        x.setTextSize(13);
        x.setAllCaps(false);
        x.setStateListAnimator(null);
        x.setPadding(0, 0, 0, 0);
        x.setBackground(pressable(0xE6FFFFFF, Screen.dp(this, 6)));
        x.setTextColor(TEXT);
        x.setOnClickListener(l);
        return x;
    }

    // ---- buttons ----

    private SurveyMode mode() { return Settings.mode(Settings.prefs(this)); }

    private String buttonState = "", chipState = "";

    /** MODE shows the mode in its colour; MARK says what it does (CANCEL REF while a ref runs). */
    private void updateModeButtons() {
        SurveyMode m = mode();
        Survey sv = survey();
        boolean running = sv != null && sv.refRunning();
        Survey.TiltView tv = sv == null ? null : sv.tiltView();
        String state = m.key + running + (tv == null ? "" : "tilt" + tv.count);
        if (state.equals(buttonState)) return;
        buttonState = state;
        if (tv != null) {
            modeB.setText("LEAN\n" + (tv.count + 1));
            style(modeB, 0xFF00897B, Color.WHITE);
            markB.setText(tv.count >= 4 ? "FINISH\nTILT" : "TILT\n" + tv.count + "/4");
            style(markB, tv.count >= 4 ? 0xFF2E7D32 : 0xFF263238, Color.WHITE);
            return;
        }
        modeB.setText(m.label);
        style(modeB, m.color, Color.WHITE);
        if (running) {
            markB.setText("CANCEL\nREF");
            style(markB, 0xFFFFA000, TEXT);
        } else {
            markB.setText("MARK\n" + m.key);
            style(markB, 0xFF263238, Color.WHITE);
        }
    }

    private static Survey survey() {
        LoggerService s = LoggerService.current;
        return s == null ? null : s.survey();
    }

    private void toast(String t) { Toast.makeText(this, t, Toast.LENGTH_SHORT).show(); }

    private void onMode() {
        Survey sv = survey();
        if (sv != null && sv.tiltRunning()) { tiltAction("lean"); return; }
        if (sv != null && sv.refRunning()) {
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("Ref running: MARK cancels it");
            return;
        }
        SurveyMode m = mode().next();
        Settings.prefs(this).edit().putString(Settings.MODE, m.key).apply();
        Feedback.play(this, m == SurveyMode.SURFACE ? Feedback.Cue.MODE_SURFACE
                : m == SurveyMode.OBJECT ? Feedback.Cue.MODE_OBJECT : Feedback.Cue.MODE_REF);
        updateModeButtons();
    }

    private void onMark() {
        LoggerService s = LoggerService.current;
        if (s == null) {
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("No session: tap REC to start one");
            return;
        }
        Survey.Outcome o = s.mark(mode());
        handle(o);
    }

    /** Long-press MARK: start a tilt capture, or take a lean while one runs. */
    private void onMarkLong() {
        Survey sv = survey();
        tiltAction(sv != null && sv.tiltRunning() ? "lean" : "start");
    }

    private void tiltAction(String action) {
        LoggerService s = LoggerService.current;
        if (s == null) {
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("No session: tap REC to start one");
            return;
        }
        handle(s.tilt(action, mode()));
    }

    /** Toasts an outcome's text; a tilt finish with warnings asks first. */
    private void handle(Survey.Outcome o) {
        if (o.confirm) {
            new AlertDialog.Builder(this).setTitle("Tilt capture: save anyway?").setMessage(o.text)
                    .setPositiveButton("Save", (d, w) -> tiltAction("finish_confirmed"))
                    .setNegativeButton("More leans", null)
                    .setNeutralButton("Cancel tilt", (d, w) -> tiltAction("cancel")).show();
        } else if (o.text != null) {
            Toast.makeText(this, o.text, o.ok ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show();
        }
        updateModeButtons();
        refresh();
    }

    /**
     * UNDO beside the notice: shown while the notice is a capture's own (Notice.undoable),
     * its row is not flagged and the 8 s window from the row's own time is open (one clock,
     * elapsedRealtime, as Survey checks). Keyed on the notice's row, not the notice: a newer
     * notice for the same capture (a ref's pole check after its save) keeps UNDO and its arm
     * delay; one for another capture replaces it. The first frame that shows it starts the
     * arm delay.
     */
    private void updateUndo(Survey.Notice nt) {
        long now = SystemClock.elapsedRealtimeNanos();
        boolean show = nt != null && nt.undoable && !nt.at.voided && VoidRule.undoOpen(nt.at.elapsedNs, now);
        if (show && nt.at != undoAt) {
            undoAt = nt.at;
            undoShownNs = now;
        } else if (!show) {
            undoAt = null;
        }
        undoB.setVisibility(show ? View.VISIBLE : View.GONE);
    }

    /**
     * A tap on UNDO: flags the notice's capture as accidental. A tap that is ignored (in the
     * first 750 ms, or the notice no longer names the capture UNDO was shown for) plays the
     * error cue, so it is never dropped silently.
     */
    private void onUndo() {
        Survey sv = survey();
        Survey.Notice nt = sv == null ? null : sv.notice();
        PointRow at = undoAt;
        if (nt == null || at == null || !nt.undoable || nt.at != at
                || !VoidRule.armed(undoShownNs, SystemClock.elapsedRealtimeNanos())) {
            Feedback.play(this, Feedback.Cue.ERROR);
            return;
        }
        Survey.Outcome o = sv.voidCapture(at.n, VoidRule.ACCIDENTAL, VoidRule.VIA_UNDO);
        Feedback.play(this, o.ok ? Feedback.Cue.REF_CANCEL : Feedback.Cue.ERROR);
        undoAt = null;
        handle(o);
    }

    /**
     * MENU > Recent captures: the last 10 capture rows, newest first. A live one can be
     * flagged with a reason (kept, never deleted); a flagged one can be un-flagged. Not while
     * a ref or tilt set runs (their cancel is the undo for those). Each step checks that the
     * session is still the current one (it can be stopped from the notification while the
     * dialog is open) and says so instead of acting on a closed session.
     */
    private void recentCaptures() {
        Survey sv = survey();
        if (sv == null) { toast("No session: tap REC to start one"); return; }
        if (sv.refRunning() || sv.tiltRunning()) { toast("Finish or cancel the running capture first"); return; }
        List<PointRow> rows = sv.recent(10);
        if (rows.isEmpty()) { toast("No captures yet"); return; }
        long now = SystemClock.elapsedRealtimeNanos();
        String[] lines = new String[rows.size()];
        for (int i = 0; i < lines.length; i++) lines[i] = VoidRule.recentLine(rows.get(i), now);
        new AlertDialog.Builder(this).setTitle("Recent captures").setItems(lines, (d, which) -> {
            if (!stillCurrent(sv)) return;
            PointRow r = rows.get(which);
            String name = Survey.label(r.cls) + r.n;
            if (r.voided) {
                confirm("Un-flag " + name + "?", "Flagged: " + r.voidReason + ". Its photos stay where they were moved.",
                        "Un-flag", () -> { if (stillCurrent(sv)) handle(sv.unvoidCapture(r.n)); });
            } else {
                pickReason(sv, r.n, name);
            }
        }).setNegativeButton("Close", null).show();
    }

    /** The reason for flagging capture n: one of VoidRule.REASONS or typed text. */
    private void pickReason(Survey sv, int n, String name) {
        String[] items = new String[VoidRule.REASONS.size() + 1];
        for (int i = 0; i < VoidRule.REASONS.size(); i++) items[i] = VoidRule.REASONS.get(i);
        items[items.length - 1] = "Type a reason…";
        new AlertDialog.Builder(this).setTitle("Flag " + name + ": why?").setItems(items, (d, which) -> {
            if (which < VoidRule.REASONS.size()) { flag(sv, n, VoidRule.REASONS.get(which)); return; }
            EditText e = new EditText(this);
            e.setSingleLine(true);
            new AlertDialog.Builder(this).setTitle("Flag " + name + ": reason").setView(e)
                    .setPositiveButton("Flag", (d2, w) -> flag(sv, n, e.getText().toString()))
                    .setNegativeButton("Cancel", null).show();
        }).setNegativeButton("Cancel", null).show();
    }

    /** True if sv is still the running session; else plays the error cue and says it ended. */
    private boolean stillCurrent(Survey sv) {
        if (survey() == sv) return true;
        Feedback.play(this, Feedback.Cue.ERROR);
        toast("That session has ended: nothing was changed");
        return false;
    }

    private void flag(Survey sv, int n, String reason) {
        if (!stillCurrent(sv)) return;
        Survey.Outcome o = sv.voidCapture(n, reason, VoidRule.VIA_LIST);
        Feedback.play(this, o.ok ? Feedback.Cue.REF_CANCEL : Feedback.Cue.ERROR);
        handle(o);
    }

    private void confirmCancelTilt() {
        confirm("Cancel tilt capture?", "The leans are kept in tilt_obs.csv but no point is written.", "Cancel tilt",
                () -> tiltAction("cancel"));
    }

    private void onPhoto() {
        Survey sv = survey();
        if (sv == null) {
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("No session: tap REC to start one");
            return;
        }
        // A camera result that never came back (we are in front again): drop that empty file first.
        if (photoPath != null) finishPhoto(RESULT_CANCELED, "abandoned");
        Survey.PhotoPlan p = sv.planPhoto();
        try {
            if (p == null || !p.file.createNewFile()) throw new IOException("cannot create the photo file");
        } catch (IOException | SecurityException e) {
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("Photo: " + e.getMessage());
            return;
        }
        Uri uri = PhotoProvider.expose(this, p.file);
        Intent i = new Intent(MediaStore.ACTION_IMAGE_CAPTURE).putExtra(MediaStore.EXTRA_OUTPUT, uri)
                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        i.setClipData(ClipData.newRawUri("photo", uri));
        photoPath = p.file.getAbsolutePath();
        photoUri = uri.toString();
        photoAttach = p.attachN;
        photoPressNs = p.pressNs;
        try {
            startActivityForResult(i, REQ_PHOTO);
        } catch (ActivityNotFoundException | SecurityException e) {
            finishPhoto(RESULT_CANCELED, "no camera app");
            Feedback.play(this, Feedback.Cue.ERROR);
            toast("No camera app found");
        }
    }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code == REQ_PHOTO) finishPhoto(result, null);
        else if (code == REQ_IMPORT_LAYERS) {
            // new layers show at once; the Layers dialog comes back when the report is closed
            LayerImport.run(this, result, data, () -> Layers.getAsync(this, l -> { files = l; applyLayers(); }),
                    this::pickLayers);
        }
    }

    /** The camera came back: keep the photo (onto a capture or its own row) or delete the empty file. */
    private void finishPhoto(int result, String failure) {
        if (photoPath == null) return;
        File f = new File(photoPath);
        Survey.PhotoPlan p = new Survey.PhotoPlan(f, photoAttach, photoPressNs);
        try {
            revokeUriPermission(Uri.parse(photoUri),
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (RuntimeException ignored) { }
        PhotoProvider.clear(this);
        photoPath = null;
        photoUri = null;
        boolean ok = result == RESULT_OK && f.length() > 0;
        Survey sv = survey();
        File photosDir = f.getParentFile();
        if (sv == null || photosDir == null || !photosDir.equals(sv.photos)) {
            // The session ended while the camera was open: keep a real photo where it is.
            if (!ok) { //noinspection ResultOfMethodCallIgnored
                f.delete();
            } else toast("Session had ended: photo kept in its folder, not in points.csv");
            return;
        }
        if (ok) sv.photoTaken(p);
        else sv.photoCancelled(p, failure != null ? failure : result == RESULT_OK ? "empty file" : "cancelled");
    }

    // ---- volume keys ----

    private static boolean isVolume(int code) {
        return code == KeyEvent.KEYCODE_VOLUME_DOWN || code == KeyEvent.KEYCODE_VOLUME_UP;
    }

    private boolean volumeDownLong;

    /**
     * Volume down acts on release (MARK, or a lean during a tilt capture); held about 1 s it
     * starts a tilt capture instead. Volume up acts at once: MODE, or finish during a tilt.
     */
    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if (isVolume(code) && Settings.volumeKeys(Settings.prefs(this))) {
            if (e.getRepeatCount() == 0) {
                if (code == KeyEvent.KEYCODE_VOLUME_DOWN) {
                    volumeDownLong = false;
                    e.startTracking();
                } else {
                    Survey sv = survey();
                    if (sv != null && sv.tiltRunning()) onMark(); else onMode();
                }
            }
            return true;   // consumed: the media volume does not change
        }
        return super.onKeyDown(code, e);
    }

    @Override public boolean onKeyLongPress(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_VOLUME_DOWN && Settings.volumeKeys(Settings.prefs(this))) {
            volumeDownLong = true;
            Survey sv = survey();
            if (sv == null || !sv.tiltRunning()) tiltAction("start");
            return true;
        }
        return super.onKeyLongPress(code, e);
    }

    @Override public boolean onKeyUp(int code, KeyEvent e) {
        if (isVolume(code) && Settings.volumeKeys(Settings.prefs(this))) {
            if (code == KeyEvent.KEYCODE_VOLUME_DOWN && !volumeDownLong && !e.isCanceled()) {
                Survey sv = survey();
                if (sv != null && sv.tiltRunning()) tiltAction("lean"); else onMark();
            }
            volumeDownLong = false;
            return true;
        }
        return super.onKeyUp(code, e);
    }

    // ---- strip actions ----

    private void confirm(String title, String message, String yes, Runnable r) {
        new AlertDialog.Builder(this).setTitle(title).setMessage(message)
                .setPositiveButton(yes, (d, w) -> r.run()).setNegativeButton("Cancel", null).show();
    }

    private void onGpsDot() {
        LoggerService s = Control.live();
        if (s != null && s.gpsActive()) {
            if (s.sessionOpen()) {
                confirm("Disconnect GPS?", "The session keeps recording, but without GPS data and captures.",
                        "Disconnect", () -> Control.disconnectGps(this));
            } else Control.disconnectGps(this);
            return;
        }
        String why = Control.connectGps(this);
        if (why != null) toast(why);
    }

    private void onCasterDot() {
        LoggerService s = Control.live();
        if (s != null && s.caster() != null) {
            if (s.sessionOpen()) {
                confirm("Disconnect caster?", "The session keeps recording, but without RTK corrections.",
                        "Disconnect", () -> Control.disconnectCaster(this));
            } else Control.disconnectCaster(this);
            return;
        }
        Control.connectCaster(this);
    }

    private void onRec() {
        LoggerService s = Control.live();
        if (s != null && s.sessionOpen()) {
            confirm("Stop the session?", "Stops recording and disconnects the GPS and the caster.", "Stop",
                    () -> Control.stopAll(this));
        } else Control.startSession(this);
    }

    /** 1.4 to 1.8 m or Other…, applied at once (and logged in a running session). */
    private void pickPole() {
        double[] presets = {1.4, 1.5, 1.6, 1.7, 1.8};
        String[] items = new String[presets.length + 1];
        double now = Settings.poleHeight(Settings.prefs(this));
        for (int i = 0; i < presets.length; i++) {
            items[i] = String.format(Locale.US, "%.1f m%s", presets[i], Math.abs(presets[i] - now) < 5e-4 ? "  (now)" : "");
        }
        items[presets.length] = "Other…" + (PoleHeight.presetIndex(now) < 0 ? "  (now " + PoleHeight.format(now) + ")" : "");
        new AlertDialog.Builder(this).setTitle("Pole height").setItems(items, (d, which) -> {
            if (which < presets.length) setPole(presets[which]);
            else poleOther(now);
        }).setNegativeButton("Cancel", null).show();
    }

    private void poleOther(double now) {
        EditText e = new EditText(this);
        e.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        e.setText(PoleHeight.format(now));
        e.setSelectAllOnFocus(true);
        new AlertDialog.Builder(this).setTitle("Pole height, m (0 to 5, up to 3 decimals)").setView(e)
                .setPositiveButton("Set", (d, w) -> {
                    try { setPole(PoleHeight.parse(e.getText().toString())); }
                    catch (IllegalArgumentException x) { toast("Not set: " + x.getMessage()); }
                }).setNegativeButton("Cancel", null).show();
    }

    private void setPole(double m) {
        Settings.prefs(this).edit().putString(Settings.POLE_HEIGHT, PoleHeight.format(m)).apply();
        refresh();
    }

    // ---- menu panel ----

    private void openDrawer() {
        drawer.setVisibility(View.VISIBLE);
        fillDrawer();
    }

    private void closeDrawer() { drawer.setVisibility(View.GONE); }

    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() {
        if (drawer.getVisibility() == View.VISIBLE) { closeDrawer(); return; }
        Survey sv = survey();
        if (sv != null && sv.tiltRunning()) { confirmCancelTilt(); return; }
        super.onBackPressed();
    }

    private void fillDrawer() {
        drawerItems.removeAllViews();
        LoggerService s = Control.live();
        boolean gps = s != null && s.gpsActive(), session = s != null && s.sessionOpen();
        NtripClient n = s == null ? null : s.caster();
        item(gps ? "Disconnect GPS (" + (s.gpsConnected() ? "connected" : s.gpsStatus()) + ")" : "Connect GPS",
                this::onGpsDot);
        item(n != null ? "Disconnect caster (" + (n.connected ? "connected" : n.status) + ")" : "Connect caster",
                this::onCasterDot);
        item(session ? "Stop session" : "Start session", this::onRec);
        item("Pole height (" + PoleHeight.format(Settings.poleHeight(Settings.prefs(this))) + " m)…", this::pickPole);
        Survey sv = survey();
        if (sv != null && sv.tiltRunning()) item("Cancel tilt capture", this::confirmCancelTilt);
        else item("Tilt capture (" + mode().key + ")", () -> tiltAction("start"));
        item("Recent captures…", this::recentCaptures);
        item("Layers…", this::pickLayers);
        item((coverageOn ? "☑" : "☐") + " Coverage overlay", this::toggleCoverage);
        item("Share last session", () -> {
            List<File> all = Storage.sessions(this);
            if (all.isEmpty()) toast("No sessions yet"); else Sharer.share(this, all.get(0));
        });
        item("Sessions", () -> startActivity(new Intent(this, SessionsActivity.class)));
        item("Details", () -> startActivity(new Intent(this, DetailsActivity.class)));
        item("Settings", () -> startActivity(new Intent(this, SettingsActivity.class)));
        item("Close menu", () -> { });
        TextView version = new TextView(this);
        version.setText(Settings.versionLine());
        version.setPadding(0, Screen.dp(this, 12), 0, 0);
        drawerItems.addView(version, new LinearLayout.LayoutParams(-1, -2));
    }

    private void item(String text, Runnable r) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(16);
        b.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        b.setMinHeight(Screen.dp(this, 56));
        b.setMinimumHeight(Screen.dp(this, 56));
        b.setOnClickListener(v -> { closeDrawer(); r.run(); });
        drawerItems.addView(b, new LinearLayout.LayoutParams(-1, -2));
    }

    private void toggleCoverage() {
        coverageOn = !coverageOn;
        Settings.prefs(this).edit().putBoolean(Settings.COVERAGE_ON, coverageOn).apply();
        refresh();
    }

    /**
     * Feeds the session's new rows to the coverage overlay, or rebuilds it from all of them
     * when the frame, the session or the settings changed; clears it when off or with no session.
     */
    private void updateCoverage(Survey sv, PointRow[] rows) {
        LocalFrame f = map.frame();
        if (!coverageOn || f == null || sv == null) {
            coverageWorker.clear();
            map.setCoverageOn(false);
            return;
        }
        map.setCoverageOn(true);
        if (coverageWorker.update(sv, f, coverageParams, rows, sv.voidGeneration())) {
            map.clearCoverage();
            // the settings and origin of each rebuild go to events.csv, so the session can be replayed
            String line = coverageParams.eventText(f, map.originNote());
            LoggerService s = LoggerService.current;
            if (s != null && (sv != coverageEventSession || !line.equals(coverageEventLine))) {
                s.event(line);
                coverageEventSession = sv;
                coverageEventLine = line;
            }
        }
    }

    /** On the coverage thread: hands a frame to the UI thread, drawn only if no newer rebuild started. */
    private void onCoverageFrame(CoverageRaster.Frame fr) {
        ui.post(() -> {
            if (!isDestroyed() && fr.gen == coverageWorker.generation()) map.applyCoverage(fr);
        });
    }

    /**
     * On the coverage thread: the worker stopped on an error (see CoverageWorker). Logged
     * (logcat, and the session's events when one is open); the overlay is dropped and the
     * legend says "coverage stopped: error". Logging and the rest of the app carry on.
     */
    private void onCoverageFailed(Throwable t) {
        android.util.Log.e("IceCubeLogger", "coverage stopped", t);
        ui.post(() -> {
            LoggerService s = LoggerService.current;
            if (s != null) s.event("coverage stopped: " + t);
            if (!isDestroyed()) map.setCoverageFailed();
        });
    }

    /** A checkbox per readable layer file, applied at once, and Import… (the same flow as Settings > Map). */
    private void pickLayers() {
        Layers.getAsync(this, l -> {
            if (isFinishing() || isDestroyed()) return;   // e.g. reached from the import report's dismiss
            List<Layers.LayerFile> ok = new ArrayList<>();
            for (Layers.LayerFile f : l) if (f.layer != null) ok.add(f);
            if (ok.isEmpty()) {
                new AlertDialog.Builder(this).setTitle("Layers").setMessage("No layer files yet.")
                        .setNeutralButton("Import…", (d, w) -> LayerImport.start(this, REQ_IMPORT_LAYERS))
                        .setPositiveButton("OK", null).show();
                return;
            }
            Set<String> off = Settings.layersOff(Settings.prefs(this));
            String[] names = new String[ok.size()];
            boolean[] on = new boolean[ok.size()];
            for (int i = 0; i < ok.size(); i++) { names[i] = ok.get(i).name(); on[i] = !off.contains(names[i]); }
            new AlertDialog.Builder(this).setTitle("Layers").setMultiChoiceItems(names, on, (d, which, checked) -> {
                Settings.setLayerOn(Settings.prefs(this), names[which], checked);
                files = l;
                applyLayers();
            }).setNeutralButton("Import…", (d, w) -> LayerImport.start(this, REQ_IMPORT_LAYERS))
                    .setPositiveButton("Done", null).show();
        });
    }

    // ---- lifecycle and map ----

    @Override protected void onResume() {
        super.onResume();
        Screen.applyOrientation(this);
        map.setLabels(Settings.labelCaptures(Settings.prefs(this)));
        refsLoadedGen = -1;   // settings may have changed
        coverageOn = Settings.coverageOn(Settings.prefs(this));
        coverageParams = Settings.coverage(Settings.prefs(this));   // a change rebuilds the overlay
        Layers.getAsync(this, l -> { files = l; applyLayers(); });
        updateModeButtons();
        ui.post(tick);
    }

    @Override protected void onPause() {
        super.onPause();
        ui.removeCallbacks(tick);
    }

    /** Stops the coverage thread: cancels the work in flight and drops frames still on their way. */
    @Override protected void onDestroy() {
        coverageWorker.close();
        coverageThread.shutdownNow();
        super.onDestroy();
    }

    /** Re-projects only when the set of switched-on layers changed. */
    private void applyLayers() {
        Set<String> off = Settings.layersOff(Settings.prefs(this));
        List<GeoJson.Feature> fs = new ArrayList<>();
        List<GeoJson.Layer> on = new ArrayList<>();
        List<String> names = new ArrayList<>();
        StringBuilder key = new StringBuilder().append(Layers.generation()).append('|');
        layerErrors = 0;
        for (Layers.LayerFile lf : files) {
            if (lf.layer == null) { layerErrors++; continue; }
            if (off.contains(lf.name())) continue;
            key.append(lf.name()).append('|');
            fs.addAll(lf.layer.features);
            on.add(lf.layer);
            names.add(lf.name());
        }
        originPoint = MapOrigin.find(on, names, "origin");
        basePoint = MapOrigin.find(on, names, "base");
        firstVertex = MapOrigin.firstVertex(on, names);
        if (!key.toString().equals(layerKey)) {
            layerKey = key.toString();
            originKey = "";   // forces the origin to be chosen again
            chooseOrigin(LoggerService.current);
            map.setFeatures(fs);
        }
    }

    private void chooseOrigin(LoggerService s) {
        TrackPoint first = s == null ? null : s.firstFix();
        MapOrigin o = MapOrigin.choose(originPoint, basePoint, first, firstVertex);
        if (o == null || o.key.equals(originKey)) return;
        originKey = o.key;
        shownOrigin = o;
        map.setFrame(o.frame, o.note);
    }

    /** Earlier refs from refs.csv (other sessions), when Settings > Survey asks for them. */
    private void maybeLoadRefs(Survey sv) {
        boolean want = Settings.showEarlierRefs(Settings.prefs(this));
        int gen = sv == null ? 0 : sv.refsGeneration();
        String session = sv == null ? "" : sv.dir.getName();
        if (want == refsWanted && gen == refsLoadedGen && session.equals(refsLoadedFor)) return;
        refsWanted = want;
        refsLoadedGen = gen;
        refsLoadedFor = session;
        if (!want) { map.setEarlierRefs(new ArrayList<PointRow>()); return; }
        android.content.Context app = getApplicationContext();
        new Thread(() -> {
            List<PointRow> out = new ArrayList<>();
            for (PointRow r : RefsStore.readAll(app)) if (!session.equals(r.session)) out.add(r);
            ui.post(() -> map.setEarlierRefs(out));
        }, "refs").start();
    }

    private static String age(Gga g) {
        return g == null || Double.isNaN(g.age) ? "–" : String.format(Locale.US, "%.1fs", g.age);
    }

    private CharSequence dot(int color, String label) {
        SpannableStringBuilder b = new SpannableStringBuilder("● " + label);
        b.setSpan(new ForegroundColorSpan(color), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        b.setSpan(new RelativeSizeSpan(1.4f), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        return b;
    }

    private void refresh() {
        LoggerService s = Control.live();
        LoggerService cur = LoggerService.current;
        chooseOrigin(cur);
        Control.askUsbPermissionIfNeeded(this);
        Survey sv = s == null ? null : s.survey();
        TrackPoint fix = s == null ? null : s.currentFix();
        PointRow[] rows = sv == null ? new PointRow[0] : sv.rows();
        updateCoverage(sv, rows);
        map.setLive(fix, s == null ? new TrackPoint[0] : s.trail(), rows,
                sv == null ? null : sv.refView(), sv == null ? null : sv.tiltView());
        if (s != null) s.layersMaybeChanged();
        maybeLoadRefs(sv);

        // fix chip
        Gga g = s == null ? null : s.fix();
        int chipColor;
        String chipText;
        if (s == null || !s.gpsConnected()) { chipColor = 0xFFB0BEC5; chipText = "NO GPS"; }
        else if (g == null) { chipColor = 0xFFB0BEC5; chipText = "NO GGA"; }
        else if (fix == null) { chipColor = MapView.qualityColor(0); chipText = "NO FIX"; }
        else {
            chipColor = MapView.qualityColor(g.quality);
            chipText = Survey.fixName(g.quality) + " " + age(g);
            Survey.FixState fs = g.quality == 4 && sv != null ? sv.fixState() : null;
            if (fs != null && fs.settleS > 0) { chipColor = AMBER; chipText = "FIX settling " + fs.settleS + "s"; }
            else if (fs != null && fs.suspect) {
                chipColor = AMBER;
                chipText = String.format(Locale.US, "FIXED? jump %.1fm", fs.jump);
            }
        }
        fixChip.setText(chipText);
        if (!chipState.equals(String.valueOf(chipColor))) {
            chipState = String.valueOf(chipColor);
            fixChip.setBackground(pressable(chipColor, Screen.dp(this, 6)));
            fixChip.setTextColor(chipColor == AMBER || chipColor == 0xFFB0BEC5 ? TEXT : Color.WHITE);
        }

        // link dots
        LinkState gs = s == null ? LinkState.OFF : LinkState.gps(s.gpsActive(), s.gpsConnected(), s.gpsStatus());
        NtripClient n = s == null ? null : s.caster();
        LinkState cs = n == null ? LinkState.OFF : LinkState.caster(true, n.connected, n.status);
        gpsItem.setText(dot(gs.color, "GPS"));
        casterItem.setText(dot(cs.color, "NTRIP"));
        double pole = cur != null ? cur.poleHeight() : Settings.poleHeight(Settings.prefs(this));
        poleItem.setText("pole " + PoleHeight.format(pole));

        // recording
        if (s != null && s.sessionOpen()) {
            long t = s.sessionSeconds();
            recItem.setText(dot(0xFFD32F2F, String.format(Locale.US, "%d:%02d:%02d · %d pts", t / 3600, t / 60 % 60,
                    t % 60, sv == null ? 0 : sv.captures())));
        } else {
            recItem.setText(dot(0xFF9E9E9E, "REC off"));
        }

        String w = Status.warnings(s);
        warnBar.setVisibility(w == null ? View.GONE : View.VISIBLE);
        if (w != null) warnBar.setText(w);
        Survey.Notice nt = sv == null ? null : sv.notice();
        noticeBar.setVisibility(nt == null ? View.GONE : View.VISIBLE);
        noticeRow.setVisibility(nt == null ? View.GONE : View.VISIBLE);
        updateUndo(nt);
        if (nt != null) {
            MapOrigin o = shownOrigin;
            String at = o == null || nt.at == null ? null : o.readout(nt.at.lat, nt.at.lon, nt.at.hGround);
            noticeBar.setText(at == null ? nt.text : nt.text + "\n" + at);
            noticeBar.setBackgroundColor(nt.warn ? AMBER : nt.ok ? 0xFF2E7D32 : 0xFFC62828);
            noticeBar.setTextColor(nt.warn ? TEXT : Color.WHITE);
        }

        // PHOTO's second line: where a photo would go now
        String target = sv == null ? "" : "→ " + sv.photoTarget();
        if (!target.equals(photoTargetShown)) {
            photoTargetShown = target;
            if (target.isEmpty()) photoB.setText("PHOTO");
            else {
                SpannableStringBuilder pb = new SpannableStringBuilder("PHOTO\n" + target);
                pb.setSpan(new RelativeSizeSpan(0.65f), 6, pb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                photoB.setText(pb);
            }
        }

        StringBuilder t = new StringBuilder();
        if (fix != null && g != null) {
            t.append(String.format(Locale.US, "sats %d", g.sats));
            if (!Double.isNaN(fix.h)) t.append(String.format(Locale.US, "  ground %.3f m", fix.h - pole));
            MapOrigin o = shownOrigin;
            String xyz = o == null ? null : o.readout(fix.lat, fix.lon, Double.isNaN(fix.h) ? Double.NaN : fix.h - pole);
            if (xyz != null) t.append('\n').append(xyz).append(" (ground)");
            t.append('\n');
        }
        t.append(map.frame() == null ? "origin: none yet" : map.originNote());
        int flagged = sv == null ? 0 : sv.flagged();
        if (flagged > 0) t.append('\n').append(flagged).append(" flagged");
        if (layerErrors > 0) t.append(String.format(Locale.US, "\n%d layer file(s) have errors (Settings)", layerErrors));
        String okLine = Status.layerOkText(s);
        if (okLine != null) t.append('\n').append(okLine);
        info.setText(t);
        updateModeButtons();
        if (drawer.getVisibility() == View.VISIBLE && drawerItems.getChildCount() > 0) {
            // keep the link and session labels current while the panel is open
            ViewGroup d = drawerItems;
            LoggerService ls = Control.live();
            boolean gps = ls != null && ls.gpsActive();
            NtripClient c = ls == null ? null : ls.caster();
            ((Button) d.getChildAt(0)).setText(gps ? "Disconnect GPS (" + (ls.gpsConnected() ? "connected" : ls.gpsStatus()) + ")"
                    : "Connect GPS");
            ((Button) d.getChildAt(1)).setText(c != null ? "Disconnect caster (" + (c.connected ? "connected" : c.status) + ")"
                    : "Connect caster");
            ((Button) d.getChildAt(2)).setText(ls != null && ls.sessionOpen() ? "Stop session" : "Start session");
        }
    }
}
