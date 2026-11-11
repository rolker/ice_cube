package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class EpochAverageTest {
    // Made-up origin; one metre north is about 1/111132 degree.
    static final double LAT = 10.0, LON = 20.0, M_LAT = 1 / 110_600.0;
    static final long S = 1_000_000_000L;

    static Epoch ep(long ns, double dn, double h, int q) {
        return new Epoch(ns, 3600 + ns / 1e9, LAT + dn * M_LAT, LON, h, 1.0, q);
    }

    @Test public void meanAndSampleScatter() {
        List<Epoch> es = Arrays.asList(ep(0, -0.01, 100.00, 4), ep(1, 0.01, 100.02, 4), ep(2, 0.0, 100.01, 4));
        EpochAverage a = EpochAverage.of(es);
        assertEquals(3, a.n);
        assertEquals(100.01, a.h, 1e-9);
        assertEquals(LAT, a.lat, 1e-10);
        assertEquals(0.01, a.sdN, 1e-4);        // sample sd of -1, +1, 0 cm = 1 cm
        assertEquals(0.0, a.sdE, 1e-6);
        assertEquals(0.01, a.sdU, 1e-4);
        assertEquals(1.0, a.age, 1e-12);
        assertEquals(4, a.quality);
    }

    @Test public void singleEpochHasZeroScatter() {
        EpochAverage a = EpochAverage.of(Arrays.asList(ep(0, 0, 50, 4)));
        assertEquals(0, a.sdE, 0);
        assertEquals(0, a.sdU, 0);
        assertTrue(Double.isNaN(EpochAverage.of(Arrays.asList(ep(0, 0, Double.NaN, 4))).h));
    }

    @Test public void recentUsesLastSecondAndBestQuality() {
        List<Epoch> es = new ArrayList<>();
        for (int i = 0; i < 10; i++) es.add(ep(i * S / 5, 0, 100, 4));     // 5 Hz fixed for 2 s
        es.add(ep(10 * S / 5, 0, 200, 5));                                  // latest is float
        EpochAverage a = EpochAverage.recent(es, 2 * S, S, 2 * S);
        // window: (1.0 s, 2.0 s] -> epochs at 1.2..1.8 fixed (4) plus the float one; fixed wins
        assertEquals(4, a.n);
        assertEquals(4, a.quality);
        assertEquals(100, a.h, 1e-9);
    }

    @Test public void recentFallsBackToBestAvailable() {
        List<Epoch> es = Arrays.asList(ep(0, 0, 10, 1), ep(S / 2, 0, 12, 5), ep(S, 0, 14, 5));
        EpochAverage a = EpochAverage.recent(es, S, S, 2 * S);
        assertEquals(2, a.n);
        assertEquals(5, a.quality);
        assertEquals(13, a.h, 1e-9);
    }

    @Test public void recentRefusesStaleOrNoFix() {
        List<Epoch> es = Arrays.asList(ep(0, 0, 10, 4));
        assertNull(EpochAverage.recent(es, 3 * S, S, 2 * S));
        assertNull(EpochAverage.recent(Arrays.asList(ep(0, 0, 10, 0)), 0, S, 2 * S));
        assertNull(EpochAverage.recent(new ArrayList<Epoch>(), 0, S, 2 * S));
    }

    @Test public void rankOrder() {
        assertTrue(EpochAverage.rank(4) > EpochAverage.rank(5));
        assertTrue(EpochAverage.rank(5) > EpochAverage.rank(2));
        assertTrue(EpochAverage.rank(2) > EpochAverage.rank(1));
        assertTrue(EpochAverage.rank(1) > EpochAverage.rank(6));
        assertEquals(0, EpochAverage.rank(0));
    }
}
