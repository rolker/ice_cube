package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.HashSet;
import java.util.Set;

/** Coverage against nodes.grid_surface (generated vectors), plus the area and status rules. */
public class CoverageTest {
    private static Coverage.Params params(double cell, double radius, double reach) {
        return new Coverage.Params(cell, radius, reach, 0.03, 0.015, 0.015);
    }

    @Test public void constantsMatchNodesDefaults() throws Exception {
        JSONObject d = Vectors.load().getJSONObject("nodes_defaults");
        assertEquals(d.getDouble("slope"), Coverage.SLOPE, 0);
        assertEquals(d.getDouble("k_sigma"), Coverage.K_SIGMA, 0);
        assertEquals(d.getDouble("capture_radius"), Coverage.Params.defaults().captureRadius, 0);
        assertEquals(d.getInt("cap"), Coverage.CAP);
        assertEquals(d.getInt("outvote"), Coverage.OUTVOTE);
    }

    /** Feeds a vector case's soundings in the given order (reach 0: only the estimator). */
    private static Coverage feedCase(JSONObject k, int[] perm) throws Exception {
        String name = k.getString("name");
        Coverage cov = new Coverage(params(k.getDouble("cell"), k.getDouble("capture_radius"), 0));
        JSONArray ss = k.getJSONArray("soundings");
        for (int s : perm) {
            JSONObject o = ss.getJSONObject(s);
            double sz = o.getDouble("sz"), sh = o.getDouble("sh");
            if (o.has("sigma_input")) {   // the two parts chain: the budget gives sz and sh
                JSONObject in = o.getJSONObject("sigma_input");
                SoundingSigma.Terms t = SoundingSigma.of(SoundingSigmaTest.row(in), in.getDouble("ground"));
                assertEquals(name + " sz", sz, t.sigmaZ, 1e-12);
                assertEquals(name + " sh", sh, t.sigmaXy, 1e-12);
            }
            cov.add(o.getDouble("x"), o.getDouble("y"), o.getDouble("z"), sz, sh, o.getInt("id"));
        }
        return cov;
    }

    private static void checkNodes(String name, JSONObject k, Coverage cov) throws Exception {
        JSONArray ns = k.getJSONArray("nodes");
        Set<Long> expected = new HashSet<>();
        for (int i = 0; i < ns.length(); i++) {
            JSONObject e = ns.getJSONObject(i);
            String at = name + " node " + e.getInt("i") + "," + e.getInt("j");
            Coverage.Node nd = cov.node(e.getInt("i"), e.getInt("j"));
            assertNotNull(at, nd);
            expected.add(Coverage.key(nd.i, nd.j));
            assertEquals(at + " n", e.getInt("n"), nd.n);
            assertEquals(at + " n_in", e.getInt("n_in"), nd.nIn);
            assertEquals(at + " n_out", e.getInt("n_out"), nd.nOut);
            assertEquals(at + " n_alt", e.getInt("n_alt"), nd.nAlt);
            assertEquals(at + " z_alt", Vectors.num(e, "z_alt"), nd.zAlt, 1e-9);
            assertEquals(at + " strength", e.getDouble("strength"), nd.strength(), 1e-12);
            assertEquals(at + " z", e.getDouble("z"), nd.z(), 1e-9);
            assertEquals(at + " sigma", e.getDouble("sigma"), nd.sigma(), 1e-9);
            assertEquals(at + " max_nr", e.getDouble("max_nr"), nd.maxNr, 1e-9);
        }
        for (Coverage.Node nd : cov.nodes()) {
            if (!expected.contains(Coverage.key(nd.i, nd.j))) assertEquals(name + " extra node", 0, nd.n);
        }
    }

    private static int[] identity(int n) {
        int[] p = new int[n];
        for (int i = 0; i < n; i++) p[i] = i;
        return p;
    }

    @Test public void everyNodesVectorCase() throws Exception {
        JSONArray cases = Vectors.load().getJSONArray("nodes");
        assertTrue(cases.length() >= 15);
        for (int c = 0; c < cases.length(); c++) {
            JSONObject k = cases.getJSONObject(c);
            checkNodes(k.getString("name"), k, feedCase(k, identity(k.getJSONArray("soundings").length())));
        }
    }

    /** Below the cap the node state is a function of the set: any arrival order gives the vector (ids travel along). */
    @Test public void arrivalOrderDoesNotMatterBelowTheCap() throws Exception {
        JSONArray cases = Vectors.load().getJSONArray("nodes");
        java.util.Random rnd = new java.util.Random(24);
        int checked = 0;
        for (int c = 0; c < cases.length(); c++) {
            JSONObject k = cases.getJSONObject(c);
            int n = k.getJSONArray("soundings").length();
            if (n > Coverage.CAP) continue;   // folding past the cap depends on arrival order
            for (int t = 0; t < 3; t++) {
                int[] p = identity(n);
                for (int i = n - 1; i > 0; i--) { int r = rnd.nextInt(i + 1); int x = p[i]; p[i] = p[r]; p[r] = x; }
                checkNodes(k.getString("name") + " shuffled", k, feedCase(k, p));
            }
            checked++;
        }
        assertTrue(checked >= 12);
    }

    /** The status after every capture (vectors part `sequences`): clearing, flicker, gap and fringe. */
    @Test public void everyStatusSequence() throws Exception {
        JSONArray cases = Vectors.load().getJSONArray("sequences");
        assertTrue(cases.length() >= 4);
        for (int c = 0; c < cases.length(); c++) {
            JSONObject k = cases.getJSONObject(c);
            JSONObject p = k.getJSONObject("params");
            Coverage cov = new Coverage(new Coverage.Params(p.getDouble("cell"), p.getDouble("capture_radius"),
                    p.getDouble("reach"), p.getDouble("target"), p.getDouble("wander"), 0.015));
            JSONArray ss = k.getJSONArray("soundings"), steps = k.getJSONArray("steps");
            java.util.Map<Long, String> want = new java.util.HashMap<>();
            for (int s = 0; s < ss.length(); s++) {
                JSONObject o = ss.getJSONObject(s);
                cov.add(o.getDouble("x"), o.getDouble("y"), o.getDouble("z"), o.getDouble("sz"), o.getDouble("sh"),
                        o.getInt("id"));
                JSONArray st = steps.getJSONArray(s);
                for (int t = 0; t < st.length(); t++) {
                    String[] f = st.getString(t).split(" ");
                    want.put(Coverage.key(Integer.parseInt(f[0]), Integer.parseInt(f[1])), f[2]);
                }
                int inArea = 0;
                for (Coverage.Node nd : cov.nodes()) {
                    if (!nd.inArea) continue;
                    inArea++;
                    String at = k.getString("name") + " after " + s + " node " + nd.i + "," + nd.j;
                    assertEquals(at, want.get(Coverage.key(nd.i, nd.j)), cov.status(nd).name().toLowerCase(java.util.Locale.US));
                }
                assertEquals(k.getString("name") + " after " + s, want.size(), inArea);
            }
        }
    }

    @Test public void gapBetweenStripsIsNoData() {
        Coverage cov = new Coverage(Coverage.Params.defaults());
        for (int k = 0; k <= 20; k++) {
            cov.add(0.25 * k, 0.0, 0.0, 0.01, 0.01);
            cov.add(0.25 * k, 3.0, 0.0, 0.01, 0.01);
        }
        Coverage.Node mid = cov.node(4, 3);   // (2.0, 1.5): 1.5 m from each strip
        assertNotNull(mid);
        assertTrue(mid.inArea);
        assertEquals(Coverage.Status.NO_DATA, cov.status(mid));   // captures within reach in all four quadrants
        assertEquals(Coverage.Status.OK, cov.status(cov.node(4, 0)));
        Coverage.Node edge = cov.node(4, -3);   // (2.0, -1.5): only the south strip's captures, all north of it
        assertEquals(0, edge.n);
        assertEquals(Coverage.Status.FRINGE, cov.status(edge));
    }

    @Test public void nothingBeyondReach() {
        Coverage cov = new Coverage(Coverage.Params.defaults());
        cov.add(0.1, 0.1, 0, 0.01, 0.01);
        for (Coverage.Node nd : cov.nodes()) {
            assertTrue(Math.hypot(nd.i * 0.5 - 0.1, nd.j * 0.5 - 0.1) <= 2.0);
        }
        assertNull(cov.node(5, 0));   // 2.4 m away
        assertEquals(Coverage.Status.NONE, cov.status(null));
        assertTrue(cov.minI >= -4 && cov.maxI <= 4);
    }

    @Test public void statusThresholdIncludesWander() {
        // at the defaults (target 3 cm, wander 1.5 cm) the edge is sqrt(3^2 - 1.5^2) = 2.598 cm
        for (double shown : new double[] {0.0259, 0.0261}) {
            Coverage cov = new Coverage(Coverage.Params.defaults());
            cov.add(0, 0, 0, shown, 0);   // one sounding on the node, sh 0: fused sigma = sz
            Coverage.Node nd = cov.node(0, 0);
            assertEquals(shown, nd.sigma(), 1e-12);
            assertEquals(Math.hypot(shown, 0.015), cov.displaySigma(nd), 1e-12);
            // 2.59 cm before wander is 2.99 cm with it: clear; 2.61 cm is 3.01 cm: amber
            assertEquals(shown < 0.026 ? Coverage.Status.OK : Coverage.Status.THIN, cov.status(nd));
        }
    }

    @Test public void disagreementTurnsRedAndAgreeingCapturesClearIt() {
        Coverage cov = new Coverage(Coverage.Params.defaults());
        cov.add(0, 0, 0.0, 0.01, 0.0);
        cov.add(0, 0, 0.2, 0.01, 0.0);
        Coverage.Node nd = cov.node(0, 0);
        assertEquals(Coverage.Status.DISAGREE, cov.status(nd));
        assertEquals(0.0, nd.z(), 0);   // 1:1, equal weights: the lower one is the height
        cov.add(0, 0, 0.001, 0.01, 0.0);
        assertEquals(2, nd.nIn);
        assertEquals(1, nd.nOut);
        assertEquals(Coverage.Status.OK, cov.status(nd));
        assertEquals(0.0005, nd.z(), 1e-12);
        assertEquals(0.2, nd.zAlt, 0);
    }

    /** One bad capture among three good ones (in any order) does not turn a node red. */
    @Test public void oneOutlierAmongThreeIsNotRed() {
        double[] zs = {0.30, 0.302, 0.45, 0.298};
        for (int first = 0; first < zs.length; first++) {
            Coverage cov = new Coverage(Coverage.Params.defaults());
            for (int k = 0; k < zs.length; k++) cov.add(0.05, 0.05, zs[(first + k) % zs.length], 0.01, 0.01, k);
            Coverage.Node nd = cov.node(0, 0);
            assertEquals(1, nd.nOut);
            assertEquals(Coverage.Status.OK, cov.status(nd));
            assertEquals(0.30, nd.z(), 0.002);
        }
    }

    private static PointRow surface(LocalFrame f, double e, double n, double z) {
        double[] g = f.toGeodetic(e, n, z);
        PointRow r = new PointRow();
        r.cls = PointRow.SURFACE;
        r.method = PointRow.SINGLE;
        r.fix = 4;
        r.lat = g[0]; r.lon = g[1]; r.hGround = g[2]; r.pole = 1.8;
        r.sdU = 0.005; r.sdE = 0.004; r.sdN = 0.004; r.age = 1; r.fixAge = 200;
        return r;
    }

    @Test public void rowsFedOnlyWhenFixedSurfaceCaptures() {
        LocalFrame f = new LocalFrame(0, 0, 0);   // made-up origin at the equator
        Coverage cov = new Coverage(Coverage.Params.defaults());
        PointRow ok = surface(f, 1, 1, 0.2);
        assertTrue(cov.add(ok, f));
        PointRow flt = surface(f, 1, 1, 0.2); flt.fix = 5;
        PointRow sus = surface(f, 1, 1, 0.2); sus.fixSuspect = true;
        PointRow obj = surface(f, 1, 1, 0.2); obj.cls = PointRow.OBJECT;
        PointRow ref = surface(f, 1, 1, 0.2); ref.method = PointRow.OCCUPATION;
        for (PointRow r : new PointRow[] {flt, sus, obj, ref}) assertFalse(cov.add(r, f));
        assertEquals(1, cov.fed());
        Coverage.Node nd = cov.node(2, 2);
        assertEquals(1, nd.n);
        assertEquals(0.2, nd.z(), 1e-6);
        SoundingSigma.Terms t = SoundingSigma.of(ok, 0.015);
        double slopeTerm = Coverage.SLOPE * t.sigmaXy;   // on the node: v = sz^2 + (slope * sh)^2
        assertEquals(Math.hypot(t.sigmaZ, slopeTerm), nd.sigma(), 1e-6);
    }

    @Test public void voidedRowDoesNotFeed() {
        LocalFrame f = new LocalFrame(0, 0, 0);   // made-up origin at the equator
        PointRow r = surface(f, 1, 1, 0.2);
        assertTrue(Coverage.feeds(r));
        r.voided = true;
        assertFalse(Coverage.feeds(r));
        assertFalse(new Coverage(Coverage.Params.defaults()).add(r, f));
    }

    /** A node one bad capture turned red is OK again in a rebuild without it (#27). */
    @Test public void rebuildWithoutVoidedCaptureClearsRed() {
        LocalFrame f = new LocalFrame(0, 0, 0);
        PointRow good = surface(f, 0, 0, 0.0), bad = surface(f, 0, 0, 0.2);
        good.n = 1; bad.n = 2;
        Coverage a = new Coverage(Coverage.Params.defaults());
        a.add(good, f);
        a.add(bad, f);
        assertEquals(Coverage.Status.DISAGREE, a.status(a.node(0, 0)));
        bad.voided = true;
        Coverage b = new Coverage(Coverage.Params.defaults());
        for (PointRow r : new PointRow[] {good, bad}) b.add(r, f);
        assertEquals(1, b.node(0, 0).n);
        assertFalse(b.status(b.node(0, 0)) == Coverage.Status.DISAGREE);
    }

    @Test public void incrementalEqualsRebuild() {
        Coverage a = new Coverage(Coverage.Params.defaults()), b = new Coverage(Coverage.Params.defaults());
        double[][] s = {{0.1, 0.2, 0.3}, {0.9, 0.4, 0.31}, {1.7, -0.3, 0.29}, {0.6, 1.1, 0.5}};
        for (double[] p : s) { a.add(p[0], p[1], p[2], 0.02, 0.03); a.drainTouched(); }
        for (double[] p : s) b.add(p[0], p[1], p[2], 0.02, 0.03);
        assertEquals(a.size(), b.size());
        for (Coverage.Node x : a.nodes()) {
            Coverage.Node y = b.node(x.i, x.j);
            assertEquals(x.n, y.n);
            assertEquals(x.nOut, y.nOut);
            assertEquals(x.w, y.w, 0);
            assertEquals(x.wz, y.wz, 0);
            assertEquals(x.inArea, y.inArea);
        }
        assertEquals(b.size(), b.drainTouched().size());
        assertTrue(b.drainTouched().isEmpty());
    }

    @Test public void onlyChangedNodesAreTouched() {
        Coverage c = new Coverage(params(0.5, 0.75, 2.0));
        c.add(0, 0, 0.1, 0.02, 0.03);
        assertEquals(c.size(), c.drainTouched().size());   // all new: fed or newly in the area
        c.add(0, 0, 0.1, 0.02, 0.03);                      // the same spot again: only the fed nodes change
        Set<Coverage.Node> touched = new HashSet<>(c.drainTouched());
        int fed = 0;
        for (Coverage.Node nd : c.nodes()) {
            boolean feeds = Math.hypot(nd.i * 0.5, nd.j * 0.5) <= 0.75;
            if (feeds) fed++;
            assertEquals(feeds, touched.contains(nd));
        }
        assertEquals(fed, touched.size());
        assertTrue(fed < c.size());
        c.add(1.0, 0, 0.1, 0.02, 0.03);                    // 1 m east: the new fringe column is touched too
        for (Coverage.Node nd : c.drainTouched()) {
            assertTrue(Math.hypot(nd.i * 0.5 - 1.0, nd.j * 0.5) <= 2.0 + 1e-9);
        }
    }

    @Test public void nodeCapStopsGrowthButKeepsUpdating() {
        Coverage cov = new Coverage(params(0.1, 0.3, 20));
        cov.add(0, 0, 0, 0.01, 0.01);        // about 126k nodes
        cov.add(100, 0, 0, 0.01, 0.01);      // another 126k: hits the cap
        assertTrue(cov.full());
        assertEquals(Coverage.MAX_NODES, cov.size());
        int before = cov.node(0, 0).n;
        cov.add(0, 0, 0, 0.01, 0.01);
        assertEquals(before + 1, cov.node(0, 0).n);
        assertEquals(Coverage.MAX_NODES, cov.size());
    }

    /** Near the cap the nodes a sounding feeds are created before the reach fringe (#6 review). */
    @Test public void nearTheCapFeedNodesComeFirst() {
        Coverage cov = new Coverage(params(0.5, 0.75, 20));   // reach 40 cells: ~5000 nodes per sounding
        double x = 0;
        cov.add(x, 0, 0, 0.01, 0.01);
        while (Coverage.MAX_NODES - cov.size() > 100) {   // walk east: each step adds new columns
            x += Coverage.MAX_NODES - cov.size() > 3000 ? 10 : 0.5;
            cov.add(x, 0, 0, 0.01, 0.01);
        }
        assertFalse(cov.full());
        int left = Coverage.MAX_NODES - cov.size();
        assertTrue(left >= 9);   // the 9 nodes within 0.75 m of a node-centred sounding fit; the fringe does not
        cov.add(-1000, -1000, 1.0, 0.01, 0.01);
        assertTrue(cov.full());
        for (int i = -1; i <= 1; i++) {
            for (int j = -1; j <= 1; j++) {
                Coverage.Node nd = cov.node(-2000 + i, -2000 + j);
                assertNotNull(i + "," + j, nd);
                assertEquals(1, nd.n);
            }
        }
    }

    /**
     * 1000 captures into one 3 m patch: every node stays at CAP stored entries or fewer,
     * counts add up, and it runs well under a second on a desktop JVM (the 5 s bound is a
     * smoke bound for loaded CI runners, not a benchmark).
     */
    @Test public void manyCapturesStayBounded() {
        Coverage cov = new Coverage(Coverage.Params.defaults());
        java.util.Random rnd = new java.util.Random(6);
        long t0 = System.nanoTime();
        for (int k = 0; k < 1000; k++) {
            double z = 0.3 + 0.004 * rnd.nextGaussian() + (k % 9 == 4 ? 0.12 : 0.0);
            cov.add(3 * rnd.nextDouble(), 3 * rnd.nextDouble(), z, 0.01, 0.01, k);
        }
        double s = (System.nanoTime() - t0) / 1e9;
        assertTrue("took " + s + " s", s < 5.0);
        int fed = 0;
        for (Coverage.Node nd : cov.nodes()) {
            assertTrue(nd.stored() <= Coverage.CAP);
            assertEquals(nd.n, nd.stored() + nd.folded() + nd.dropped());
            assertEquals(nd.n, nd.nIn + nd.nOut);
            if (nd.n > Coverage.CAP) {
                fed++;
                assertEquals(0.3, nd.z(), 0.01);
                assertFalse(cov.status(nd) == Coverage.Status.DISAGREE);
            }
        }
        assertTrue(fed > 10);
    }

    @Test public void settingsEventLine() {
        Coverage.Params p = Coverage.Params.defaults();
        LocalFrame f = new LocalFrame(10.5, -20.25, 3.0);   // made-up origin
        String t = p.eventText(f, "origin: \"X\" in test.geojson");
        assertEquals("coverage cell=0.5 radius=0.75 reach=2.0 target=0.03 wander=0.015 ground=0.015 slope=0.05 "
                + "k_sigma=3.0 cap=32 outvote=2 origin_lat=10.500000000 origin_lon=-20.250000000 origin_h=3.0000 "
                + "origin_source=\"origin: 'X' in test.geojson\"", t);
        assertTrue(p.eventText(null, null).contains("origin=none"));
    }
}
