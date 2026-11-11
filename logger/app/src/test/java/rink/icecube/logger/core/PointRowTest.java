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

    @Test public void headerHasSeventeenColumns() {
        assertEquals(PointRow.COLUMNS, PointRow.split(PointRow.HEADER).size());
        assertEquals("session", PointRow.split(PointRow.REFS_HEADER).get(0));
    }

    @Test public void rowFormat() {
        EpochAverage a = EpochAverage.of(Arrays.asList(
                new Epoch(5, 50_000.25, 12.3456789012, -45.6789012345, 101.23456, 1.2, 4)));
        PointRow r = PointRow.of(7, PointRow.SURFACE, 123456789L, a, 1.8, MIDNIGHT + 50_000_000L);
        assertEquals("7,surface,123456789,2026-11-11T13:53:20.25Z,12.345678901,-45.678901235,101.2346,1.800,99.4346,4,1,"
                + "0.0000,0.0000,0.0000,1.2,,", r.toCsv());
        assertEquals(PointRow.COLUMNS, PointRow.split(r.toCsv()).size());
    }

    @Test public void noFixRowLeavesPositionEmpty() {
        PointRow r = PointRow.of(3, PointRow.PHOTO, 9, null, 1.7, MIDNIGHT);
        r.photo = "photo_3_x.jpg";
        assertEquals("3,photo,9,,,,,1.700,,0,0,,,,,photo_3_x.jpg,", r.toCsv());
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
