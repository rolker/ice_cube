package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Arrays;
import java.util.Random;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

public class ShareZipTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    @Test public void fileNameEndsInZip() {
        assertEquals("IceCubeLogger_2026-11-11_14-02-28.zip", ShareZip.fileName("2026-11-11_14-02-28", false));
        assertEquals("IceCubeLogger_2026-11-11_14-02-28_snapshot.zip", ShareZip.fileName("2026-11-11_14-02-28", true));
    }

    @Test public void methodByExtension() {
        assertEquals(ZipEntry.STORED, ShareZip.method("s/photos/photo_1_20261111T140322Z.jpg"));
        assertEquals(ZipEntry.STORED, ShareZip.method("s/photos/IMG.JPEG"));
        assertEquals(ZipEntry.STORED, ShareZip.method("x.png"));
        assertEquals(ZipEntry.DEFLATED, ShareZip.method("s/points.csv"));
        assertEquals(ZipEntry.DEFLATED, ShareZip.method("refs.csv"));
        assertEquals(ZipEntry.DEFLATED, ShareZip.method("s/rover.ubx"));
        assertEquals(ZipEntry.DEFLATED, ShareZip.method("s/events.txt"));
        assertFalse(ShareZip.isCompressed("jpg.csv"));
        assertTrue(ShareZip.isCompressed("a.gz"));
    }

    @Test public void crcOverPrefixAndShortInput() throws IOException {
        byte[] d = "hello world".getBytes("UTF-8");
        CRC32 c = new CRC32();
        c.update(d, 0, 5);
        assertEquals(c.getValue(), ShareZip.crc(new ByteArrayInputStream(d), 5, new byte[3]));
        try {
            ShareZip.crc(new ByteArrayInputStream(d), 20, new byte[4]);
            throw new AssertionError("expected EOF");
        } catch (java.io.EOFException expected) { /* ok */ }
    }

    private File write(String name, byte[] data) throws IOException {
        File f = tmp.newFile(name);
        try (OutputStream o = new FileOutputStream(f)) { o.write(data); }
        return f;
    }

    /** STORED and DEFLATED entries, each cut at a length shorter than the file (a snapshot), read back intact. */
    @Test public void snapshotZipRoundTrip() throws IOException {
        byte[] jpg = new byte[200_000];
        new Random(1).nextBytes(jpg);
        byte[] log = new byte[150_000];
        for (int i = 0; i < log.length; i++) log[i] = (byte) ("line\n".charAt(i % 5));
        File fj = write("photo_1.jpg", jpg);
        File fl = write("rover.ubx", log);
        long nj = 123_457, nl = 99_999;

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        long[] seen = {0};
        try (ZipOutputStream z = new ZipOutputStream(bytes)) {
            byte[] buf = new byte[4096];
            ShareZip.addFile(z, "s/photos/photo_1.jpg", fj, nj, buf, n -> seen[0] += n);
            ShareZip.addFile(z, "s/rover.ubx", fl, nl, buf, n -> seen[0] += n);
        }
        assertEquals(nj + nl, seen[0]);

        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(bytes.toByteArray()))) {
            ZipEntry e = in.getNextEntry();
            assertEquals("s/photos/photo_1.jpg", e.getName());
            assertEquals(ZipEntry.STORED, e.getMethod());
            assertEquals(nj, e.getSize());
            assertArrayEquals(Arrays.copyOf(jpg, (int) nj), readAll(in));
            e = in.getNextEntry();
            assertEquals("s/rover.ubx", e.getName());
            assertEquals(ZipEntry.DEFLATED, e.getMethod());
            assertArrayEquals(Arrays.copyOf(log, (int) nl), readAll(in));
            assertNull(in.getNextEntry());
        }
    }

    private static byte[] readAll(ZipInputStream in) throws IOException {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        int n;
        while ((n = in.read(b)) > 0) o.write(b, 0, n);
        return o.toByteArray();
    }
}
