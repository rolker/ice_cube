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
    }

    @Test public void everyNodesVectorCase() throws Exception {
        JSONArray cases = Vectors.load().getJSONArray("nodes");
        assertTrue(cases.length() >= 5);
        for (int c = 0; c < cases.length(); c++) {
            JSONObject k = cases.getJSONObject(c);
            String name = k.getString("name");
            double rad = k.getDouble("capture_radius");
            // reach 0: only the estimator, no area marking
            Coverage cov = new Coverage(params(k.getDouble("cell"), rad, 0));
            JSONArray ss = k.getJSONArray("soundings");
            for (int s = 0; s < ss.length(); s++) {
                JSONObject o = ss.getJSONObject(s);
                double sz = o.getDouble("sz"), sh = o.getDouble("sh");
                if (o.has("sigma_input")) {   // the two parts chain: the budget gives sz and sh
                    JSONObject in = o.getJSONObject("sigma_input");
                    SoundingSigma.Terms t = SoundingSigma.of(SoundingSigmaTest.row(in), in.getDouble("ground"));
                    assertEquals(name + " sz", sz, t.sigmaZ, 1e-12);
                    assertEquals(name + " sh", sh, t.sigmaXy, 1e-12);
                }
                cov.add(o.getDouble("x"), o.getDouble("y"), o.getDouble("z"), sz, sh);
            }
            JSONArray ns = k.getJSONArray("nodes");
            Set<Long> expected = new HashSet<>();
            for (int i = 0; i < ns.length(); i++) {
                JSONObject e = ns.getJSONObject(i);
                String at = name + " node " + e.getInt("i") + "," + e.getInt("j");
                Coverage.Node nd = cov.node(e.getInt("i"), e.getInt("j"));
                assertNotNull(at, nd);
                expected.add(Coverage.key(nd.i, nd.j));
                assertEquals(at + " n", e.getInt("n"), nd.n);
                assertEquals(at + " flags", e.getInt("flags"), nd.flags);
                assertEquals(at + " z", e.getDouble("z"), nd.z(), 1e-9);
                assertEquals(at + " sigma", e.getDouble("sigma"), nd.sigma(), 1e-9);
                assertEquals(at + " max_nr", e.getDouble("max_nr"), nd.maxNr, 1e-9);
            }
            for (Coverage.Node nd : cov.nodes()) {
                if (!expected.contains(Coverage.key(nd.i, nd.j))) assertEquals(name + " extra node", 0, nd.n);
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
        assertEquals(Coverage.Status.NO_DATA, cov.status(mid));
        assertEquals(Coverage.Status.OK, cov.status(cov.node(4, 0)));
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

    @Test public void disagreementTurnsRed() {
        Coverage cov = new Coverage(Coverage.Params.defaults());
        cov.add(0, 0, 0.0, 0.01, 0.0);
        cov.add(0, 0, 0.2, 0.01, 0.0);
        assertEquals(Coverage.Status.DISAGREE, cov.status(cov.node(0, 0)));
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

    @Test public void incrementalEqualsRebuild() {
        Coverage a = new Coverage(Coverage.Params.defaults()), b = new Coverage(Coverage.Params.defaults());
        double[][] s = {{0.1, 0.2, 0.3}, {0.9, 0.4, 0.31}, {1.7, -0.3, 0.29}, {0.6, 1.1, 0.5}};
        for (double[] p : s) { a.add(p[0], p[1], p[2], 0.02, 0.03); a.drainTouched(); }
        for (double[] p : s) b.add(p[0], p[1], p[2], 0.02, 0.03);
        assertEquals(a.size(), b.size());
        for (Coverage.Node x : a.nodes()) {
            Coverage.Node y = b.node(x.i, x.j);
            assertEquals(x.n, y.n);
            assertEquals(x.flags, y.flags);
            assertEquals(x.w, y.w, 0);
            assertEquals(x.wz, y.wz, 0);
            assertEquals(x.inArea, y.inArea);
        }
        assertEquals(b.size(), b.drainTouched().size());
        assertTrue(b.drainTouched().isEmpty());
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
}
