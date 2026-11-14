package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public class ReferenceFrameTest {
    private static void check(String text, String name, double epoch) {
        ReferenceFrame f = ReferenceFrame.parse(text);
        assertEquals(text, name, f.name);
        if (Double.isNaN(epoch)) assertTrue(text, Double.isNaN(f.epoch));
        else assertEquals(text, epoch, f.epoch, 1e-9);
    }

    @Test public void parsesFrameNamesAndEpochs() {
        check("ITRF2020 epoch 2026.85", "ITRF2020", 2026.85);
        check("itrf 2014 @2010.0", "ITRF2014", 2010.0);
        check("ITRF-08", "ITRF2008", Double.NaN);
        check("frame: NAD83(2011) epoch=2010.00", "NAD83(2011)", 2010.0);
        check("nad 83 (2011)", "NAD83(2011)", Double.NaN);
        check("NAD83", "NAD83", Double.NaN);
        check("WGS84 (G2139) 2024.5", "WGS84(G2139)", 2024.5);
        check("wgs-84", "WGS84", Double.NaN);
        check("Base app: ITRF2020, epoch: 2026.850, ant ARP", "ITRF2020", 2026.85);
    }

    @Test public void noFrameIsNull() {
        assertNull(ReferenceFrame.parse(""));
        assertNull(ReferenceFrame.parse("none"));
        assertNull(ReferenceFrame.parse("2026.85"));
        assertNull(ReferenceFrame.parse(null));
    }

    @Test public void epochSetting() {
        assertTrue(Double.isNaN(ReferenceFrame.parseEpoch(" ")));
        assertEquals(2010.0, ReferenceFrame.parseEpoch("2010"), 0);
        assertEquals(2026.85, ReferenceFrame.parseEpoch("2026,85"), 1e-9);
        for (String s : new String[] {"abc", "1900", "3000.5"}) {
            try { ReferenceFrame.parseEpoch(s); fail(s); } catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void resolveOrderAndWarnings() {
        ReferenceFrame table = ReferenceFrame.parse("ITRF2020 epoch 2026.85");
        ReferenceFrame setting = ReferenceFrame.fromSetting("NAD83(2011)", Double.NaN);
        ReferenceFrame.Resolved r = ReferenceFrame.resolve(setting, table);
        assertEquals("sourcetable", r.source);
        assertEquals("ITRF2020", r.frame.name);
        assertTrue(r.warning.startsWith("frame mismatch: setting NAD83(2011)"));
        assertEquals("frame=ITRF2020 epoch=2026.85 source=sourcetable", r.eventText());

        ReferenceFrame.Resolved s = ReferenceFrame.resolve(setting, null);
        assertEquals("setting", s.source);
        assertNull(s.warning);

        ReferenceFrame.Resolved none = ReferenceFrame.resolve(ReferenceFrame.fromSetting("Unknown", 2020), null);
        assertNull(none.frame);
        assertEquals("frame unknown", none.describe());
        assertEquals("frame=unknown source=none", none.eventText());

        assertNull(ReferenceFrame.resolve(ReferenceFrame.fromSetting("ITRF2020", Double.NaN), table).warning);
        assertTrue(ReferenceFrame.resolve(ReferenceFrame.fromSetting("ITRF2020", 2020.0), table).warning != null);
        assertTrue(ReferenceFrame.compatible(new ReferenceFrame("WGS84", Double.NaN), new ReferenceFrame("WGS84(G2139)", Double.NaN)));
        assertFalse(ReferenceFrame.compatible(new ReferenceFrame("ITRF2014", Double.NaN), new ReferenceFrame("ITRF2020", Double.NaN)));
    }
}
