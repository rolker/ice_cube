package rink.icecube.logger;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import rink.icecube.logger.core.GeoJson;
import rink.icecube.logger.core.LocalFrame;
import rink.icecube.logger.core.TrackPoint;

/**
 * North-up map in local metres (east/north on the ENU plane of {@link LocalFrame}), drawn
 * on a Canvas with no basemap. Layer features are projected once per layer load or origin
 * change into Paths in map units (x = east, y = -north); each frame only transforms them
 * to the screen. Pinch zooms, drag pans (and stops following), double tap recenters.
 */
final class MapView extends View {
    /** A projected feature with its resolved style. */
    private static final class Drawn {
        GeoJson.Feature f;
        Path path;           // lines and polygons, map units
        float[] pts;         // points, map units
        final RectF bounds = new RectF();
        float lx, ly;        // label anchor
        int stroke, fill;
    }

    private final float dp;
    private LocalFrame frame;
    private String originNote = "";
    private List<GeoJson.Feature> features = new ArrayList<>();
    private final List<Drawn> drawn = new ArrayList<>();
    private TrackPoint fix;
    private TrackPoint[] trail = new TrackPoint[0], marks = new TrackPoint[0];

    private double cx, cy;               // view centre, map units
    private float ppm;                   // pixels per metre, 0 until first layout
    private boolean follow = true;

    private final Matrix matrix = new Matrix();
    private final Path tmpPath = new Path();
    private float[] tmpPts = new float[64];
    private final double[] enu = new double[3];
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG), fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG), text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint halo = new Paint(Paint.ANTI_ALIAS_FLAG), box = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final List<RectF> labelRects = new ArrayList<>();
    private final ScaleGestureDetector scaler;
    private final GestureDetector gestures;

    static final int[] LEGEND_Q = {1, 2, 5, 4};
    static final String[] LEGEND_NAMES = {"single", "DGPS", "float", "fixed"};

    static int qualityColor(int q) {
        switch (q) {
            case 1: return 0xFFD32F2F;   // single: red
            case 2: return 0xFF1976D2;   // DGPS: blue
            case 5: return 0xFFFFA000;   // float: amber
            case 4: return 0xFF2E7D32;   // fixed: green
            default: return 0xFF757575;  // other (e.g. dead reckoning): grey
        }
    }

    MapView(Context c) {
        super(c);
        dp = getResources().getDisplayMetrics().density;
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeJoin(Paint.Join.ROUND);
        stroke.setStrokeCap(Paint.Cap.ROUND);
        fill.setStyle(Paint.Style.FILL);
        text.setTextSize(12 * getResources().getDisplayMetrics().scaledDensity);
        text.setColor(0xFF212121);
        halo.set(text);
        halo.setStyle(Paint.Style.STROKE);
        halo.setStrokeWidth(3 * dp);
        halo.setColor(0xCCFFFFFF);
        box.setColor(0xD9FFFFFF);
        scaler = new ScaleGestureDetector(c, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override public boolean onScale(ScaleGestureDetector d) {
                zoomAt(d.getScaleFactor(), d.getFocusX(), d.getFocusY());
                return true;
            }
        });
        gestures = new GestureDetector(c, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent e) { return true; }
            @Override public boolean onScroll(MotionEvent a, MotionEvent b, float dx, float dy) {
                if (ppm <= 0) return false;
                follow = false;
                cx += dx / ppm;
                cy += dy / ppm;
                invalidate();
                return true;
            }
            @Override public boolean onDoubleTap(MotionEvent e) { recenter(); return true; }
        });
    }

    // ---- inputs ----

    LocalFrame frame() { return frame; }

    /** Changes the origin, keeping the same ground point at the view centre. */
    void setFrame(LocalFrame f, String note) {
        if (frame != null && f != null) {
            double[] g = frame.toGeodetic(cx, -cy, 0);
            f.toEnu(g[0], g[1], g[2], enu);
            cx = enu[0];
            cy = -enu[1];
        }
        frame = f;
        originNote = note;
        project();
    }

    String originNote() { return originNote; }

    void setFeatures(List<GeoJson.Feature> fs) {
        features = fs;
        project();
    }

    void setLive(TrackPoint fix, TrackPoint[] trail, TrackPoint[] marks) {
        this.fix = fix;
        this.trail = trail;
        this.marks = marks;
        invalidate();
    }

    void recenter() {
        follow = true;
        if (fix == null && trail.length > 0) center(trail[trail.length - 1]);
        invalidate();
    }

    /** Fits the layers, trail and marks on screen and stops following. */
    void fit() {
        RectF r = new RectF();
        boolean any = false;
        for (Drawn d : drawn) { if (any) r.union(d.bounds); else { r.set(d.bounds); any = true; } }
        for (TrackPoint[] ps : new TrackPoint[][] {trail, marks}) {
            for (TrackPoint p : ps) {
                if (!local(p)) continue;
                float x = (float) enu[0], y = (float) -enu[1];
                if (any) r.union(x, y); else { r.set(x, y, x, y); any = true; }
            }
        }
        if (!any || getWidth() == 0) return;
        follow = false;
        cx = r.centerX();
        cy = r.centerY();
        float w = Math.max(r.width(), 5), h = Math.max(r.height(), 5);
        ppm = clampScale(0.85f * Math.min(getWidth() / w, getHeight() / h));
        invalidate();
    }

    private void center(TrackPoint p) {
        if (local(p)) { cx = enu[0]; cy = -enu[1]; }
    }

    private boolean local(TrackPoint p) {
        if (frame == null || p == null) return false;
        frame.toEnu(p.lat, p.lon, p.h, enu);
        return true;
    }

    private static float clampScale(float s) { return Math.max(0.002f, Math.min(2000f, s)); }

    private void zoomAt(float factor, float fx, float fy) {
        if (ppm <= 0) return;
        double wx = cx + (fx - getWidth() / 2f) / ppm, wy = cy + (fy - getHeight() / 2f) / ppm;
        ppm = clampScale(ppm * factor);
        if (!follow) {
            cx = wx - (fx - getWidth() / 2f) / ppm;
            cy = wy - (fy - getHeight() / 2f) / ppm;
        }
        invalidate();
    }

    // ---- projection, once per load ----

    private void project() {
        drawn.clear();
        if (frame != null) {
            for (GeoJson.Feature f : features) drawn.add(project(f));
        }
        invalidate();
    }

    private Drawn project(GeoJson.Feature f) {
        Drawn d = new Drawn();
        d.f = f;
        boolean first = true;
        switch (f.shape) {
            case POINTS: {
                double[] p = f.parts.get(0);
                d.pts = new float[p.length / 3 * 2];
                for (int i = 0, k = 0; i < p.length; i += 3, k += 2) {
                    frame.toEnu(p[i + 1], p[i], p[i + 2], enu);
                    d.pts[k] = (float) enu[0];
                    d.pts[k + 1] = (float) -enu[1];
                    if (first) { d.bounds.set(d.pts[k], d.pts[k + 1], d.pts[k], d.pts[k + 1]); first = false; }
                    else d.bounds.union(d.pts[k], d.pts[k + 1]);
                }
                d.lx = d.pts[0];
                d.ly = d.pts[1];
                d.stroke = f.stroke != null ? f.stroke : f.isBase() ? 0xFFC62828 : 0xFF1565C0;
                d.fill = f.fill != null ? f.fill : d.stroke;
                break;
            }
            default: {
                d.path = new Path();
                boolean poly = f.shape == GeoJson.Shape.POLYGONS;
                if (poly) d.path.setFillType(Path.FillType.EVEN_ODD);
                for (int part = 0; part < f.parts.size(); part++) {
                    double[] p = f.parts.get(part);
                    for (int i = 0; i < p.length; i += 3) {
                        frame.toEnu(p[i + 1], p[i], p[i + 2], enu);
                        float x = (float) enu[0], y = (float) -enu[1];
                        if (i == 0) d.path.moveTo(x, y); else d.path.lineTo(x, y);
                        if (first) { d.bounds.set(x, y, x, y); first = false; } else d.bounds.union(x, y);
                    }
                    if (poly) d.path.close();
                }
                if (poly) {
                    d.lx = d.bounds.centerX();
                    d.ly = d.bounds.centerY();
                    d.fill = f.fill != null ? f.fill : (f.stroke != null ? (f.stroke & 0x00FFFFFF) | 0x33000000 : 0x332E7D32);
                    d.stroke = f.stroke != null ? f.stroke : (f.fill != null ? f.fill | 0xFF000000 : 0xFF2E7D32);
                } else {
                    double[] p = f.parts.get(0);
                    int mid = (p.length / 3) / 2 * 3;
                    frame.toEnu(p[mid + 1], p[mid], p[mid + 2], enu);
                    d.lx = (float) enu[0];
                    d.ly = (float) -enu[1];
                    d.stroke = f.stroke != null ? f.stroke : 0xFF424242;
                }
            }
        }
        return d;
    }

    // ---- drawing ----

    @SuppressLint("DrawAllocation")
    @Override protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        if (ppm <= 0) ppm = clampScale(w / 60f);   // about 60 m across at first
        if (follow && fix != null) center(fix);
        c.drawColor(0xFFF4F4EF);
        if (frame == null) {
            c.drawText("No position yet and no layers: waiting for the first GPS fix.", 12 * dp, h / 2f, text);
            drawLegend(c);
            return;
        }
        matrix.reset();
        matrix.postTranslate((float) -cx, (float) -cy);
        matrix.postScale(ppm, ppm);
        matrix.postTranslate(w / 2f, h / 2f);
        float halfW = w / 2f / ppm, halfH = h / 2f / ppm;
        RectF view = new RectF((float) cx - halfW, (float) cy - halfH, (float) cx + halfW, (float) cy + halfH);

        stroke.setStrokeWidth(2 * dp);
        for (int pass = 0; pass < 2; pass++) {                 // polygons below lines
            for (Drawn d : drawn) {
                if (d.path == null || (d.f.shape == GeoJson.Shape.POLYGONS) != (pass == 0)) continue;
                if (!RectF.intersects(view, d.bounds) && !view.contains(d.bounds)) continue;
                d.path.transform(matrix, tmpPath);
                if (pass == 0) { fill.setColor(d.fill); c.drawPath(tmpPath, fill); }
                stroke.setColor(d.stroke);
                c.drawPath(tmpPath, stroke);
            }
        }
        for (Drawn d : drawn) {
            if (d.pts == null) continue;
            if (tmpPts.length < d.pts.length) tmpPts = new float[d.pts.length];
            matrix.mapPoints(tmpPts, 0, d.pts, 0, d.pts.length / 2);
            dot.setColor(d.fill);
            for (int k = 0; k < d.pts.length; k += 2) {
                float x = tmpPts[k], y = tmpPts[k + 1];
                if (x < -20 || y < -20 || x > w + 20 || y > h + 20) continue;
                if (d.f.isBase()) drawTriangle(c, x, y, 7 * dp, d.fill);
                else c.drawCircle(x, y, 4 * dp, dot);
            }
        }
        drawOrigin(c);
        drawTrail(c);
        drawMarks(c);
        drawLabels(c, w, h);
        drawFix(c);
        drawLegend(c);
        drawScale(c, w, h);
    }

    private void drawTriangle(Canvas c, float x, float y, float r, int color) {
        tmpPath.reset();
        tmpPath.moveTo(x, y - r);
        tmpPath.lineTo(x + r * 0.87f, y + r * 0.5f);
        tmpPath.lineTo(x - r * 0.87f, y + r * 0.5f);
        tmpPath.close();
        dot.setColor(color);
        c.drawPath(tmpPath, dot);
        stroke.setColor(Color.WHITE);
        stroke.setStrokeWidth(1.5f * dp);
        c.drawPath(tmpPath, stroke);
    }

    private float[] screen(double e, double n) {
        float[] p = {(float) e, (float) -n};
        matrix.mapPoints(p);
        return p;
    }

    private void drawOrigin(Canvas c) {
        float[] o = screen(0, 0);
        stroke.setColor(0x99000000);
        stroke.setStrokeWidth(1 * dp);
        float r = 6 * dp;
        c.drawLine(o[0] - r, o[1], o[0] + r, o[1], stroke);
        c.drawLine(o[0], o[1] - r, o[0], o[1] + r, stroke);
    }

    private void drawTrail(Canvas c) {
        for (TrackPoint p : trail) {
            if (!local(p)) continue;
            float[] s = screen(enu[0], enu[1]);
            dot.setColor(qualityColor(p.quality) & 0x00FFFFFF | 0xB0000000);
            c.drawCircle(s[0], s[1], 2.5f * dp, dot);
        }
    }

    private void drawMarks(Canvas c) {
        for (TrackPoint p : marks) {
            if (!local(p)) continue;
            float[] s = screen(enu[0], enu[1]);
            float r = 5 * dp;
            dot.setColor(0xFF6A1B9A);
            c.drawRect(s[0] - r, s[1] - r, s[0] + r, s[1] + r, dot);
            stroke.setColor(Color.WHITE);
            stroke.setStrokeWidth(1.5f * dp);
            c.drawRect(s[0] - r, s[1] - r, s[0] + r, s[1] + r, stroke);
            if (p.label != null) label(c, p.label, s[0] + r + 2 * dp, s[1] - r, true);
        }
    }

    private void drawFix(Canvas c) {
        if (!local(fix)) return;
        float[] s = screen(enu[0], enu[1]);
        dot.setColor(Color.WHITE);
        c.drawCircle(s[0], s[1], 10 * dp, dot);
        dot.setColor(qualityColor(fix.quality));
        c.drawCircle(s[0], s[1], 7.5f * dp, dot);
    }

    /** Named features, skipping any label that would overlap one already drawn. */
    private void drawLabels(Canvas c, int w, int h) {
        labelRects.clear();
        int drawnCount = 0;
        for (Drawn d : drawn) {
            if (d.f.name == null) continue;
            float[] s = screen(d.lx, -d.ly);
            if (s[0] < 0 || s[1] < 0 || s[0] > w || s[1] > h) continue;
            if (label(c, d.f.name, s[0] + 6 * dp, s[1] - 4 * dp, false) && ++drawnCount >= 80) break;
        }
    }

    private boolean label(Canvas c, String t, float x, float y, boolean force) {
        float tw = text.measureText(t), th = text.getTextSize();
        RectF r = new RectF(x - 2, y - th, x + tw + 2, y + 4);
        if (!force) for (RectF o : labelRects) if (RectF.intersects(o, r)) return false;
        labelRects.add(r);
        c.drawText(t, x, y, halo);
        c.drawText(t, x, y, text);
        return true;
    }

    private void drawLegend(Canvas c) {
        float pad = 6 * dp, row = 16 * dp, x = 8 * dp, y = 8 * dp;
        float width = 0;
        for (String n : LEGEND_NAMES) width = Math.max(width, text.measureText(n));
        c.drawRoundRect(x, y, x + width + 26 * dp, y + row * LEGEND_Q.length + pad, 4 * dp, 4 * dp, box);
        for (int i = 0; i < LEGEND_Q.length; i++) {
            float cy0 = y + pad + row * i + row / 2 - 2 * dp;
            dot.setColor(qualityColor(LEGEND_Q[i]));
            c.drawCircle(x + 10 * dp, cy0, 5 * dp, dot);
            c.drawText(LEGEND_NAMES[i], x + 20 * dp, cy0 + text.getTextSize() / 3, text);
        }
    }

    private void drawScale(Canvas c, int w, int h) {
        double maxM = (w / 3.0) / ppm;
        double step = Math.pow(10, Math.floor(Math.log10(maxM)));
        double len = 5 * step <= maxM ? 5 * step : 2 * step <= maxM ? 2 * step : step;
        float px = (float) (len * ppm), x = 12 * dp, y = h - 14 * dp;
        String t = len >= 1 ? String.format(Locale.US, "%.0f m", len)
                : len >= 0.01 ? String.format(Locale.US, "%.0f cm", len * 100) : String.format(Locale.US, "%.0f mm", len * 1000);
        c.drawRect(x - 4 * dp, y - 22 * dp, x + Math.max(px, text.measureText(t)) + 8 * dp, y + 8 * dp, box);
        stroke.setColor(0xFF212121);
        stroke.setStrokeWidth(2 * dp);
        c.drawLine(x, y, x + px, y, stroke);
        c.drawLine(x, y - 5 * dp, x, y + 3 * dp, stroke);
        c.drawLine(x + px, y - 5 * dp, x + px, y + 3 * dp, stroke);
        c.drawText(t, x, y - 8 * dp, text);
        String n = "N↑";
        c.drawText(n, w - text.measureText(n) - 12 * dp, 22 * dp, text);
    }

    @SuppressLint("ClickableViewAccessibility")
    @Override public boolean onTouchEvent(MotionEvent e) {
        scaler.onTouchEvent(e);
        if (!scaler.isInProgress()) gestures.onTouchEvent(e);
        return true;
    }
}
