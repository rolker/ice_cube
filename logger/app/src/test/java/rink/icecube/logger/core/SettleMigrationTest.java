package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public class SettleMigrationTest {
    @Test public void oldDefaultMovesOnce() {
        assertEquals("120", SettleMigration.migrate("10", false));
        assertEquals("120", SettleMigration.migrate(" 10 ", false));
        // after the marker is set, a deliberate 10 stays
        assertEquals("10", SettleMigration.migrate("10", true));
    }

    @Test public void otherValuesAreKept() {
        assertEquals("30", SettleMigration.migrate("30", false));
        assertEquals("0", SettleMigration.migrate("0", false));
        assertNull(SettleMigration.migrate(null, false));
    }
}
