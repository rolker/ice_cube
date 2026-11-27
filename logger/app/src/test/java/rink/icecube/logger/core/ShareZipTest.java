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
import java.util.zip.ZipFile;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

public class ShareZipTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    @Test public void fileNameEndsInZip() {
        assertEquals("IceCubeLogger_2026-11-11_14-02-28.zip", ShareZip.fileName("2026-11-11_14-02-28", false));
        assertEquals("IceCubeLogger_2026-11-11_14-02-28_snapshot.zip", ShareZip.fileName("2026-11-11_14-02-28", true));
    }

    @Test public void extendedTimestampBytes() {
        // 2026-11-26T12:00:00Z = 1795694400 = 0x6B081F40
        assertArrayEquals(new byte[] {0x55, 0x54, 5, 0, 1, 0x40, 0x1F, 0x08, 0x6B},
                ShareZip.extendedTimestamp(1795694400L));
        assertArrayEquals(new byte[] {0x55, 0x54, 5, 0, 1, 0, 0, 0, 0}, ShareZip.extendedTimestamp(0));
    }

    /** The mtime of the 0x5455 field in an extra block, or -1. */
    static long extendedMtime(byte[] extra) {
        if (extra == null) return -1;
        for (int i = 0; i + 4 <= extra.length; ) {
            int id = (extra[i] & 0xFF) | (extra[i + 1] & 0xFF) << 8, size = (extra[i + 2] & 0xFF) | (extra[i + 3] & 0xFF) << 8;
            if (id == 0x5455 && size >= 5 && (extra[i + 4] & 1) != 0) {
                return (extra[i + 5] & 0xFF) | (extra[i + 6] & 0xFF) << 8 | (extra[i + 7] & 0xFF) << 16 | (long) extra[i + 8] << 24;
            }
            i += 4 + size;
        }
        return -1;
    }

    @Test public void entriesCarryTheExtendedTimestamp() throws IOException {
        File f = tmp.newFile("refs.csv");
        try (OutputStream o = new FileOutputStream(f)) { o.write("session,n\n".getBytes("UTF-8")); }
        long mtimeMs = 1795694400_000L + 1234;
        assertTrue(f.setLastModified(mtimeMs));
        mtimeMs = f.lastModified();   // the file system's resolution
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (ZipOutputStream z = new ZipOutputStream(bytes)) {
            ShareZip.addFile(z, "refs.csv", f, f.length(), new byte[64], null);
            ShareZip.addFile(z, "p.jpg", f, f.length(), new byte[64], null);   // STORED too
        }
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(bytes.toByteArray()))) {
            for (int k = 0; k < 2; k++) {
                ZipEntry e = in.getNextEntry();
                assertEquals(Math.floorDiv(mtimeMs, 1000L), extendedMtime(e.getExtra()));
                assertEquals(1, extendedCount(e.getExtra()));   // one 0x5455, not a copy per writer
            }
        }
        // the importer (Python zipfile) reads the central directory's extra field, which
        // ZipFile returns, not the local header's above
        try (ZipFile zf = new ZipFile(writeZip(bytes))) {
            for (String n : new String[] {"refs.csv", "p.jpg"}) {
                byte[] extra = zf.getEntry(n).getExtra();
                assertEquals(Math.floorDiv(mtimeMs, 1000L), extendedMtime(extra));
                assertEquals(1, extendedCount(extra));
            }
        }
    }

    private File writeZip(ByteArrayOutputStream bytes) throws IOException {
        File z = tmp.newFile();
        try (OutputStream o = new FileOutputStream(z)) { bytes.writeTo(o); }
        return z;
    }

    /** How many 0x5455 fields an extra block holds. */
    static int extendedCount(byte[] extra) {
        int n = 0;
        for (int i = 0; extra != null && i + 4 <= extra.length; ) {
            int id = (extra[i] & 0xFF) | (extra[i + 1] & 0xFF) << 8, size = (extra[i + 2] & 0xFF) | (extra[i + 3] & 0xFF) << 8;
            if (id == 0x5455) n++;
            i += 4 + size;
        }
        return n;
    }

    @Test public void unknownMtimeGetsNoExtendedTimestamp() throws IOException {
        File f = tmp.newFile("refs.csv");
        try (OutputStream o = new FileOutputStream(f)) { o.write("session,n\n".getBytes("UTF-8")); }
        assertTrue(f.setLastModified(0));
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (ZipOutputStream z = new ZipOutputStream(bytes)) {
            ShareZip.addFile(z, "refs.csv", f, f.length(), new byte[64], null);
        }
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(bytes.toByteArray()))) {
            ZipEntry e = in.getNextEntry();
            assertEquals(0, extendedCount(e.getExtra()));   // no claim of an exact 1970 time
            assertEquals(ShareZip.DOS_EPOCH_MS, e.getTime());
        }
        try (ZipFile zf = new ZipFile(writeZip(bytes))) {   // the central directory too
            assertEquals(0, extendedCount(zf.getEntry("refs.csv").getExtra()));
        }
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
