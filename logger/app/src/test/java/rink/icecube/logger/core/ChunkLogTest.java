package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import java.io.ByteArrayOutputStream;
import java.io.StringWriter;

import org.junit.Test;

public class ChunkLogTest {
    @Test public void bytesUnchangedAndOffsetsContinue() throws Exception {
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        StringWriter csv = new StringWriter();
        ChunkLog c = new ChunkLog(data, csv);
        byte[] a = {1, 2, 3}, b = {(byte) 0xD3, 0, 4, 5, 6};
        c.write(100, a, 3);
        c.write(250, b, 2);
        c.write(300, b, 5);
        c.flush();
        assertArrayEquals(new byte[] {1, 2, 3, (byte) 0xD3, 0, (byte) 0xD3, 0, 4, 5, 6}, data.toByteArray());
        assertEquals(ChunkLog.HEADER + "100,0,3\n250,3,2\n300,5,5\n", csv.toString());
        assertEquals(10, c.offset());
    }
}
