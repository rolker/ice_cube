package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/** SoundingSigma against soundings.sigma_terms, through the generated vectors. */
public class SoundingSigmaTest {
    private static final double TOL = 1e-12;

    @Test public void constantsMatchSoundingsDefaults() throws Exception {
        JSONObject d = Vectors.load().getJSONObject("sigma_defaults");
        assertEquals(d.getDouble("floor"), SoundingSigma.FLOOR, 0);
        assertEquals(d.getDouble("floor_h"), SoundingSigma.FLOOR_H, 0);
        assertEquals(d.getDouble("age_free_s"), SoundingSigma.AGE_FREE_S, 0);
        assertEquals(d.getDouble("age_rate"), SoundingSigma.AGE_RATE, 0);
        assertEquals(d.getDouble("settle_extra"), SoundingSigma.SETTLE_EXTRA, 0);
        assertEquals(d.getDouble("tip_sink"), SoundingSigma.TIP_SINK, 0);
        assertEquals(d.getDouble("tilt_bubble_deg"), SoundingSigma.TILT_BUBBLE_DEG, 0);
        assertEquals(d.getDouble("settle_s"), SoundingSigma.SETTLE_S, 0);
    }

    /** A PointRow carrying a vector case's inputs. */
    static PointRow row(JSONObject in) throws Exception {
        PointRow r = new PointRow();
        r.cls = PointRow.SURFACE;
        r.fix = 4;
        r.method = in.getString("method");
        r.sdU = Vectors.num(in, "sd_u");
        r.sdE = Vectors.num(in, "sd_e");
        r.sdN = Vectors.num(in, "sd_n");
        r.age = Vectors.num(in, "age_s");
        r.fixAge = Vectors.num(in, "fix_age_s");
        r.pole = Vectors.num(in, "pole_m");
        r.tiltSdE = Vectors.num(in, "tilt_sd_e");
        r.tiltSdN = Vectors.num(in, "tilt_sd_n");
        r.tiltSdU = Vectors.num(in, "tilt_sd_u");
        return r;
    }

    @Test public void everyVectorCase() throws Exception {
        JSONArray cases = Vectors.load().getJSONArray("sigma");
        assertTrue(cases.length() >= 10);
        for (int i = 0; i < cases.length(); i++) {
            JSONObject c = cases.getJSONObject(i);
            String name = c.getString("name");
            JSONObject in = c.getJSONObject("input"), ex = c.getJSONObject("expected");
            SoundingSigma.Terms t = SoundingSigma.of(row(in), in.getDouble("ground"));
            assertEquals(name + " rtk", ex.getDouble("sigma_rtk"), t.rtk, TOL);
            assertEquals(name + " age", ex.getDouble("sigma_age"), t.age, TOL);
            assertEquals(name + " settle", ex.getDouble("sigma_settle"), t.settle, TOL);
            assertEquals(name + " pole", ex.getDouble("sigma_pole"), t.pole, TOL);
            assertEquals(name + " ground", ex.getDouble("sigma_ground"), t.ground, TOL);
            assertEquals(name + " sigma_z", ex.getDouble("sigma_z"), t.sigmaZ, TOL);
            assertEquals(name + " sigma_xy", ex.getDouble("sigma_xy"), t.sigmaXy, TOL);
        }
    }

    @Test public void lawnShotAtDefaults() {
        // 1 cm floor, 1.8 m pole on a bubble, 1.5 cm ground: about 2.1 cm vertical, 3.3 cm horizontal
        SoundingSigma.Terms t = SoundingSigma.terms(0.004, 0.003, 1, 200, 1.8, SoundingSigma.TILT_BUBBLE_DEG, 0.015);
        assertEquals(0.0207, t.sigmaZ, 1e-4);
        assertEquals(0.0330, t.sigmaXy, 1e-4);
    }
}
