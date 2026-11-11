package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

public class PointRowTest {
    static final long DAY = 86_400_000L;
    // 2026-11-11T00:00:00Z
    static final long MIDNIGHT = 1_794_355_200_000L;

    @Test public void headerColumns() {
        assertEquals(PointRow.COLUMNS, PointRow.split(PointRow.HEADER).size());
        assertEquals("session", PointRow.split(PointRow.REFS_HEADER).get(0));
        List<String> h = PointRow.split(PointRow.HEADER);
        assertEquals(27, h.size());
        // 0.6 columns appended after note, so readers of the first 17 keep working
        assertEquals("note", h.get(PointRow.BASE_COLUMNS - 1));
        assertEquals(Arrays.asList("fix_age_s", "fix_jump_m", "fix_suspect", "method", "tilt_n", "tilt_rms_m",
                "tilt_cover_deg", "tilt_sd_e_m", "tilt_sd_n_m", "tilt_sd_u_m"), h.subList(17, 27));
    }

    @Test public void newColumnsWriteAndParse() {
        EpochAverage a = EpochAverage.of(Arrays.asList(new Epoch(5, 50_000.25, 12.3, -45.6, 101.2, 1.2, 4)));
        PointRow r = PointRow.of(8, PointRow.OBJECT, 1, a, 1.6, MIDNIGHT + 50_000_000L);
        r.method = PointRow.TILT;
        r.fixAge = 42.25;
        r.fixJump = 1.8234;
        r.fixSuspect = true;
        r.tiltN = 5;
        r.tiltRms = 0.009;
        r.tiltCover = 144.7;
        r.tiltSdE = 0.048;
        r.tiltSdN = 0.091;
        r.tiltSdU = 0.019;
        String csv = r.toCsv();
        assertTrue(csv, csv.endsWith(",,42.3,1.823,1,tilt,5,0.0090,145,0.0480,0.0910,0.0190"));
        assertEquals(PointRow.COLUMNS, PointRow.split(csv).size());
        PointRow b = PointRow.parse(PointRow.split(csv), 0);
        assertEquals("tilt", b.method);
        assertEquals(5, b.tiltN);
        assertTrue(b.fixSuspect);
        assertEquals(1.823, b.fixJump, 1e-9);
        assertEquals(0.091, b.tiltSdN, 1e-9);
        PointRow single = PointRow.of(9, PointRow.SURFACE, 1, a, 1.6, MIDNIGHT);
        single.method = PointRow.SINGLE;
        single.fixAge = 3;
        single.fixJump = 0.2;
        assertTrue(single.toCsv().endsWith(",3.0,0.200,0,single,,,,,,"));
    }

    @Test public void parsesRowsFromBefore06() {
        String old = "1,surface,81234567890123,2026-11-11T14:03:22.40Z,10.000000123,20.000000456,31.2345,1.800,29.4345,4,5,"
                + "0.0021,0.0018,0.0040,1.0,,";
        PointRow r = PointRow.parse(PointRow.split(old), 0);
        assertEquals(29.4345, r.hGround, 1e-9);
        assertEquals("", r.method);
        assertTrue(Double.isNaN(r.fixAge));
        assertEquals(0, r.tiltN);
    }

    @Test public void rowFormat() {
        EpochAverage a = EpochAverage.of(Arrays.asList(
                new Epoch(5, 50_000.25, 12.3456789012, -45.6789012345, 101.23456, 1.2, 4)));
        PointRow r = PointRow.of(7, PointRow.SURFACE, 123456789L, a, 1.8, MIDNIGHT + 50_000_000L);
        assertEquals("7,surface,123456789,2026-11-11T13:53:20.25Z,12.345678901,-45.678901235,101.2346,1.800,99.4346,4,1,"
                + "0.0000,0.0000,0.0000,1.2,,,,,0,,,,,,,", r.toCsv());
        assertEquals(PointRow.COLUMNS, PointRow.split(r.toCsv()).size());
    }

    @Test public void noFixRowLeavesPositionEmpty() {
        PointRow r = PointRow.of(3, PointRow.PHOTO, 9, null, 1.7, MIDNIGHT);
        r.photo = "photo_3_x.jpg";
        assertEquals("3,photo,9,,,,,1.700,,0,0,,,,,photo_3_x.jpg,,,,0,,,,,,,", r.toCsv());
    }

    @Test public void quotingAndRoundTrip() {
        PointRow r = PointRow.of(2, PointRow.REF, 1, null, 1.5, MIDNIGHT);
        r.note = "rink corner, \"NE\"";
        r.session = "2026-11-11_10-00-00";
        String line = r.toRefsCsv();
        assertTrue(line.contains("\"rink corner, \"\"NE\"\"\""));
        List<String> f = PointRow.split(line);
        assertEquals(PointRow.COLUMNS + 1, f.size());
        PointRow back = PointRow.parse(f, 1);
        assertEquals("rink corner, \"NE\"", back.note);
        assertEquals("2026-11-11_10-00-00", back.session);
        assertEquals(2, back.n);
        assertEquals(1.5, back.pole, 0);
        assertTrue(Double.isNaN(back.lat));
    }

    @Test public void utcDatesNearPhoneClock() {
        assertEquals("2026-11-11T00:00:01.00Z", PointRow.utc(1.0, MIDNIGHT + 2000));
        // GGA just before midnight, phone just after: previous day
        assertEquals("2026-11-10T23:59:59.50Z", PointRow.utc(86399.5, MIDNIGHT + 1000));
        // GGA just after midnight, phone just before: next day
        assertEquals("2026-11-11T00:00:00.20Z", PointRow.utc(0.2, MIDNIGHT - 500));
        assertEquals("", PointRow.utc(Double.NaN, MIDNIGHT));
        assertEquals("20261111T135320Z", PointRow.utcCompact(50_000.25, MIDNIGHT + 50_000_000L));
        assertEquals("20261111T000000Z", PointRow.utcCompact(Double.NaN, MIDNIGHT));
    }
}
