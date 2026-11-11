package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * One tilt capture in progress: the pole tip held on a point while the antenna is leaned in
 * several directions; each lean is a 1 s average of RTK fixed epochs plus the phone's
 * gravity vector over the same second. The tip comes from {@link TiltSolver}.
 *
 * Leans are worked in a local ENU frame about the first lean. A lean is refused when the
 * average is not RTK fixed, its horizontal scatter (sqrt(sdE^2 + sdN^2)) is over the lean
 * scatter gate, or, once 3 or more leans give a provisional tip, its tilt is outside the
 * tilt gates. {@link #finish} drops leans whose tilt ended up outside the gates, then, while
 * the IMU disagreement is over its gate, the lean whose removal leaves the others most
 * consistent (one at a time, only while at least 4 leans would remain), then lists the
 * gate warnings. Not thread-safe (Survey holds its lock).
 */
public final class TiltSet {
    public static final int MIN_LEANS = 4;
    public static final String OBS_HEADER = "set,n,lean,elapsed_ns,utc,lat,lon,h_ant_ellips,n_epochs,sd_e_m,sd_n_m,"
            + "sd_u_m,age_s,fix_age_s,fix_jump_m,fix_suspect,grav_x,grav_y,grav_z,grav_n,e_m,n_m,u_m,tilt_deg,"
            + "azimuth_deg,resid_m,imu_deg,used,why_dropped";

    /** The gates (Settings): metres and degrees. */
    public static final class Gates {
        public double minCoverDeg = Limit.TILT_COVER_DEG.def, maxRms = Limit.TILT_RMS_CM.def / 100,
                minTiltDeg = Limit.TILT_MIN_DEG.def, maxTiltDeg = Limit.TILT_MAX_DEG.def,
                maxSigma = Limit.TILT_SIGMA_CM.def / 100, imuDeg = Limit.TILT_IMU_DEG.def,
                maxLeanScatter = Limit.LEAN_SCATTER_CM.def / 100;
    }

    /** One lean. gravity is null when the phone gave none. */
    public static final class Lean {
        public final int k;
        public final long elapsedNs, phoneMs;
        public final EpochAverage avg;
        public final double[] enu, gravity;
        public final int gravityN;
        public final double fixAge, fixJump;
        public final boolean fixSuspect;
        // from the last solution
        public double tiltDeg = Double.NaN, azimuthDeg = Double.NaN, resid = Double.NaN, imuDeg = Double.NaN;
        public boolean used = true;
        public String whyDropped = "";

        Lean(int k, long elapsedNs, long phoneMs, EpochAverage avg, double[] enu, double[] gravity, int gravityN,
             double fixAge, double fixJump, boolean fixSuspect) {
            this.k = k; this.elapsedNs = elapsedNs; this.phoneMs = phoneMs; this.avg = avg; this.enu = enu;
            this.gravity = gravity; this.gravityN = gravityN; this.fixAge = fixAge; this.fixJump = fixJump;
            this.fixSuspect = fixSuspect;
        }
    }

    /** The answer to a lean: accepted or not, and a short text. */
    public static final class Added {
        public final boolean ok;
        public final String text;
        public final Lean lean;
        Added(boolean ok, String text, Lean lean) { this.ok = ok; this.text = text; this.lean = lean; }
    }

    /** A finished (or provisional) solve over the used leans. */
    public static final class Result {
        public final TiltSolver.Solution solution;   // null if fewer than 3 used leans or no convergence
        public final List<Lean> used = new ArrayList<>(), dropped = new ArrayList<>();
        public final List<String> warnings = new ArrayList<>();
        public double maxImuDeg = Double.NaN;
        /** The lean whose removal leaves the others most consistent with the IMU (null if no check). */
        public Lean imuCulprit;
        public double[] tipGeodetic;                 // lat, lon, h
        Result(TiltSolver.Solution s) { solution = s; }
        public boolean ok() { return solution != null && used.size() >= MIN_LEANS; }
    }

    public final String cls;
    public final double pole;
    public final Gates gates;
    public final long startNs;
    private LocalFrame frame;
    private final List<Lean> leans = new ArrayList<>();
    private Result provisional;

    public TiltSet(String cls, double pole, Gates gates, long startNs) {
        this.cls = cls; this.pole = pole; this.gates = gates; this.startNs = startNs;
    }

    public List<Lean> leans() { return new ArrayList<>(leans); }
    public int count() { return leans.size(); }
    public LocalFrame frame() { return frame; }
    /** The provisional solution after the latest lean (null before 3 leans). */
    public Result provisional() { return provisional; }

    /**
     * Adds a lean from a 1 s average (a), the gravity mean over that second (null if none),
     * and the fix stretch the epochs belong to (s; null if unknown).
     */
    public Added add(EpochAverage a, double[] gravity, int gravityN, FixGuard.Stretch s, long nowNs, long phoneMs) {
        int k = leans.size() + 1;
        if (a == null) return new Added(false, "Lean refused: no fix", null);
        if (a.quality != 4) return new Added(false, "Lean refused: not RTK fixed", null);
        if (Double.isNaN(a.h)) return new Added(false, "Lean refused: no height in the GGA", null);
        double scatter = Math.hypot(a.sdE, a.sdN);
        if (scatter > gates.maxLeanScatter) {
            return new Added(false, String.format(Locale.US, "Lean refused: moving (scatter %.1f cm > %.1f): hold still",
                    scatter * 100, gates.maxLeanScatter * 100), null);
        }
        if (frame == null) frame = new LocalFrame(a.lat, a.lon, Double.isNaN(a.h) ? 0 : a.h);
        double[] enu = frame.toEnu(a.lat, a.lon, a.h);
        Lean l = new Lean(k, nowNs, phoneMs, a, enu, gravity, gravityN,
                s == null ? Double.NaN : s.ageSeconds(a.lastNs), s == null ? Double.NaN : s.jump, s != null && s.suspect);
        leans.add(l);
        Result r = solveAll();
        if (r.solution != null) {
            double t = l.tiltDeg;
            if (t < gates.minTiltDeg || t > gates.maxTiltDeg) {
                leans.remove(leans.size() - 1);
                provisional = leans.size() >= 3 ? solveAll() : null;
                return new Added(false, String.format(Locale.US, "Lean refused: tilt %.0f° (keep %.0f-%.0f°)",
                        t, gates.minTiltDeg, gates.maxTiltDeg), null);
            }
        }
        provisional = r.solution != null ? r : null;
        return new Added(true, describe(l, r), l);
    }

    private String describe(Lean l, Result r) {
        if (r.solution == null) return String.format(Locale.US, "Lean %d (need 3 for directions)", l.k);
        TiltSolver.Solution s = r.solution;
        return String.format(Locale.US, "Lean %d  %.0f°  cover %.0f°  rms %.1f cm", l.k, l.tiltDeg, s.coverageDeg,
                s.rms * 100);
    }

    /** Solves over all leans, marks each lean's tilt, azimuth, residual and IMU disagreement. */
    private Result solveAll() {
        List<Lean> use = new ArrayList<>(leans);
        return solve(use, new ArrayList<Lean>());
    }

    private Result solve(List<Lean> use, List<Lean> dropped) {
        List<double[]> p = new ArrayList<>(), g = new ArrayList<>();
        for (Lean l : use) { p.add(l.enu); g.add(l.gravity); }
        TiltSolver.Solution s = TiltSolver.solve(p, pole);
        Result r = new Result(s);
        r.used.addAll(use);
        r.dropped.addAll(dropped);
        for (Lean l : leans) {
            l.used = use.contains(l);
            if (s == null) { l.tiltDeg = l.azimuthDeg = l.resid = l.imuDeg = Double.NaN; continue; }
            double[] v = {l.enu[0] - s.tip[0], l.enu[1] - s.tip[1], l.enu[2] - s.tip[2]};
            l.tiltDeg = TiltSolver.angleDeg(v, new double[] {0, 0, 1});
            l.azimuthDeg = TiltSolver.azimuth(v[0], v[1]);
            l.resid = Math.sqrt(TiltSolver.dot(v, v)) - pole;
            l.imuDeg = Double.NaN;
        }
        if (s != null) {
            TiltSolver.Imu imu = TiltSolver.imuCheck(p, g, pole);
            for (int i = 0; i < use.size(); i++) use.get(i).imuDeg = imu.disagreement[i];
            r.maxImuDeg = imu.max();
            int k = imu.culprit();
            r.imuCulprit = k < 0 ? null : use.get(k);
            r.tipGeodetic = frame.toGeodetic(s.tip[0], s.tip[1], s.tip[2]);
        }
        return r;
    }

    /**
     * The final solve: drops leans outside the tilt gates, then the worst IMU disagreement
     * over the gate, one at a time, while at least 4 would remain; then lists the warnings
     * (coverage, RMS, tilts, sigma, IMU). Not ok() with fewer than 4 leans.
     */
    public Result finish() {
        List<Lean> use = new ArrayList<>(leans), dropped = new ArrayList<>();
        for (Lean l : leans) l.whyDropped = "";
        Result r = solve(use, dropped);
        while (r.solution != null && use.size() > MIN_LEANS) {
            List<Lean> out = new ArrayList<>();
            for (Lean l : use) if (l.tiltDeg < gates.minTiltDeg || l.tiltDeg > gates.maxTiltDeg) out.add(l);
            if (!out.isEmpty() && use.size() - out.size() >= MIN_LEANS) {
                for (Lean l : out) l.whyDropped = String.format(Locale.US, "tilt %.1f deg", l.tiltDeg);
                use.removeAll(out);
                dropped.addAll(out);
                r = solve(use, dropped);
                continue;
            }
            Lean worst = r.imuCulprit;
            if (worst != null && r.maxImuDeg > gates.imuDeg) {
                worst.whyDropped = String.format(Locale.US, "IMU %.1f deg", worst.imuDeg);
                use.remove(worst);
                dropped.add(worst);
                r = solve(use, dropped);
                continue;
            }
            break;
        }
        warnings(r);
        return r;
    }

    private void warnings(Result r) {
        List<String> w = r.warnings;
        if (r.used.size() < MIN_LEANS) { w.add("need " + MIN_LEANS + " leans, have " + r.used.size()); return; }
        TiltSolver.Solution s = r.solution;
        if (s == null) { w.add("no solution (leans too close together?)"); return; }
        if (s.coverageDeg < gates.minCoverDeg) {
            w.add(String.format(Locale.US, "coverage %.0f° < %.0f°: lean toward the open side too", s.coverageDeg,
                    gates.minCoverDeg));
        }
        if (s.rms > gates.maxRms) w.add(String.format(Locale.US, "RMS %.1f cm > %.1f cm", s.rms * 100, gates.maxRms * 100));
        for (Lean l : r.used) {
            if (l.tiltDeg < gates.minTiltDeg || l.tiltDeg > gates.maxTiltDeg) {
                w.add(String.format(Locale.US, "lean %d tilt %.0f° outside %.0f-%.0f°", l.k, l.tiltDeg, gates.minTiltDeg,
                        gates.maxTiltDeg));
            }
        }
        if (s.maxSd() > gates.maxSigma) {
            w.add(String.format(Locale.US, "sigma E %.1f N %.1f U %.1f cm > %.1f cm", s.sd[0] * 100, s.sd[1] * 100,
                    s.sd[2] * 100, gates.maxSigma * 100));
        }
        if (!Double.isNaN(r.maxImuDeg) && r.maxImuDeg > gates.imuDeg) {
            w.add(String.format(Locale.US, "IMU disagrees %.1f° > %.1f° (tip slipped or bad fix?)", r.maxImuDeg, gates.imuDeg));
        }
        boolean suspect = false;
        for (Lean l : r.used) suspect |= l.fixSuspect;
        if (suspect) w.add("fix may be false (jumped " + jumpText(r) + ")");
    }

    private static String jumpText(Result r) {
        double j = maxJump(r.used);
        return Double.isNaN(j) ? "?" : String.format(Locale.US, "%.1f m", j);
    }

    static double maxJump(List<Lean> ls) {
        double j = Double.NaN;
        for (Lean l : ls) if (!Double.isNaN(l.fixJump)) j = Double.isNaN(j) ? l.fixJump : Math.max(j, l.fixJump);
        return j;
    }

    /**
     * The points.csv row for a finished set: the tip as lat/lon and h_ground_ellips, the mean
     * antenna height as h_ant_ellips, pole_m = L, fix 4, n_epochs summed over the used leans,
     * sd_* the RMS of the leans' own scatter, age the mean correction age, utc from the last
     * used lean; fix_age_s the smallest (youngest stretch), fix_jump_m the largest jump,
     * fix_suspect if any lean was in a suspect stretch; method tilt and the tilt_* columns.
     */
    public PointRow toRow(int n, long elapsedNs, Result r, long phoneMs) {
        PointRow row = new PointRow();
        row.n = n;
        row.cls = cls;
        row.elapsedNs = elapsedNs;
        row.pole = pole;
        row.method = PointRow.TILT;
        Lean lastUsed = r.used.get(r.used.size() - 1);
        row.utc = PointRow.utc(lastUsed.avg.tod, phoneMs);
        row.lat = r.tipGeodetic[0];
        row.lon = r.tipGeodetic[1];
        row.hGround = r.tipGeodetic[2];
        double sh = 0, se = 0, sn = 0, su = 0, sa = 0, age = Double.POSITIVE_INFINITY;
        int nh = 0, na = 0, ne = 0, nu = 0;
        boolean suspect = false;
        for (Lean l : r.used) {
            EpochAverage a = l.avg;
            if (!Double.isNaN(a.h)) { sh += a.h; nh++; }
            if (!Double.isNaN(a.age)) { sa += a.age; na++; }
            se += a.sdE * a.sdE;
            sn += a.sdN * a.sdN;
            if (!Double.isNaN(a.sdU)) { su += a.sdU * a.sdU; nu++; }
            ne += a.n;
            if (!Double.isNaN(l.fixAge)) age = Math.min(age, l.fixAge);
            suspect |= l.fixSuspect;
        }
        int k = r.used.size();
        row.hAnt = nh > 0 ? sh / nh : Double.NaN;
        row.fix = 4;
        row.nEpochs = ne;
        row.sdE = Math.sqrt(se / k);
        row.sdN = Math.sqrt(sn / k);
        row.sdU = nu > 0 ? Math.sqrt(su / nu) : Double.NaN;
        row.age = na > 0 ? sa / na : Double.NaN;
        row.fixAge = Double.isInfinite(age) ? Double.NaN : age;
        row.fixJump = maxJump(r.used);
        row.fixSuspect = suspect;
        TiltSolver.Solution s = r.solution;
        row.tiltN = k;
        row.tiltRms = s.rms;
        row.tiltCover = s.coverageDeg;
        row.tiltSdE = s.sd[0];
        row.tiltSdN = s.sd[1];
        row.tiltSdU = s.sd[2];
        return row;
    }

    /**
     * tilt_obs.csv rows (OBS_HEADER) for every lean of this set: set number, the points.csv
     * n (0 for a cancelled set: written empty), the lean's own average and gravity, its local
     * ENU about the first lean, and from the last solve its tilt, azimuth, residual, IMU
     * disagreement, used 0/1 and why it was dropped.
     */
    public List<String> obsRows(int set, int n) {
        List<String> out = new ArrayList<>();
        for (Lean l : leans) {
            EpochAverage a = l.avg;
            double[] g = l.gravity;
            out.add(set + "," + (n > 0 ? String.valueOf(n) : "") + "," + l.k + "," + l.elapsedNs + ","
                    + PointRow.utc(a.tod, l.phoneMs) + "," + PointRow.num(a.lat, 9) + "," + PointRow.num(a.lon, 9) + ","
                    + PointRow.num(a.h, 4) + "," + a.n + "," + PointRow.num(a.sdE, 4) + "," + PointRow.num(a.sdN, 4) + ","
                    + PointRow.num(a.sdU, 4) + "," + PointRow.num(a.age, 1) + "," + PointRow.num(l.fixAge, 1) + ","
                    + PointRow.num(l.fixJump, 3) + "," + (l.fixSuspect ? 1 : 0) + ","
                    + (g == null ? ",," : PointRow.num(g[0], 5) + "," + PointRow.num(g[1], 5) + "," + PointRow.num(g[2], 5))
                    + "," + l.gravityN + "," + PointRow.num(l.enu[0], 4) + "," + PointRow.num(l.enu[1], 4) + ","
                    + PointRow.num(l.enu[2], 4) + "," + PointRow.num(l.tiltDeg, 2) + "," + PointRow.num(l.azimuthDeg, 1)
                    + "," + PointRow.num(l.resid, 4) + "," + PointRow.num(l.imuDeg, 2) + "," + (n > 0 && l.used ? 1 : 0)
                    + "," + PointRow.text(l.whyDropped));
        }
        return out;
    }
}
