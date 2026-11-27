package rink.icecube.logger.core;

import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Locale;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Pure zip logic for the session share: the shared file name, which entries are STORED
 * (JPEG photos and other already-compressed files) or DEFLATED (text and binary logs), and
 * writing one file into a zip cut at a given length (so a snapshot of a growing log works
 * with STORED too: the CRC and sizes are computed over exactly that many bytes first).
 */
public final class ShareZip {
    private ShareZip() { }

    /** Name prefix of the shared zip. */
    public static final String PREFIX = "IceCubeLogger_";

    /** IceCubeLogger_<session>.zip, or IceCubeLogger_<session>_snapshot.zip. Also the share subject/title. */
    public static String fileName(String session, boolean snapshot) {
        return PREFIX + session + (snapshot ? "_snapshot" : "") + ".zip";
    }

    private static final String[] COMPRESSED = {
        ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif",
        ".mp4", ".m4a", ".3gp", ".zip", ".gz", ".bz2", ".xz", ".7z", ".zst",
    };

    /** True for files that are already compressed (photos, archives), which are stored, not deflated. */
    public static boolean isCompressed(String name) {
        String n = name.toLowerCase(Locale.ROOT);
        for (String ext : COMPRESSED) if (n.endsWith(ext)) return true;
        return false;
    }

    /** ZipEntry.STORED for already-compressed files, ZipEntry.DEFLATED otherwise. */
    public static int method(String name) { return isCompressed(name) ? ZipEntry.STORED : ZipEntry.DEFLATED; }

    public interface Progress { void add(long bytes); }

    /** CRC-32 of the first {@code length} bytes of the stream; EOFException if it is shorter. */
    static long crc(InputStream in, long length, byte[] buf) throws IOException {
        CRC32 crc = new CRC32();
        long left = length;
        while (left > 0) {
            int n = in.read(buf, 0, (int) Math.min(buf.length, left));
            if (n < 0) throw new EOFException("file shorter than " + length + " bytes");
            crc.update(buf, 0, n);
            left -= n;
        }
        return crc.getValue();
    }

    /** 1980-01-01 00:00 local time, the oldest time a DOS date holds. */
    static final long DOS_EPOCH_MS = new java.util.GregorianCalendar(1980, 0, 1).getTimeInMillis();

    /** Zip extra field header id of the extended timestamp ("UT", Info-ZIP). */
    public static final int EXTENDED_TIMESTAMP_ID = 0x5455;

    /**
     * The zip extended-timestamp extra field (0x5455) holding only the modification time:
     * id 0x5455, data size 5, flags 1, then the time as 4-byte little-endian signed UTC
     * seconds. Built by hand rather than through setLastModifiedTime; a ZipOutputStream may
     * still re-encode the field from setExtra (OpenJDK does), so a zip should carry one
     * 0x5455 per entry with this time, not necessarily these exact bytes. Readers (the
     * importer's zip_entry_mtime) then order copies by UTC instead of by the DOS time, which
     * is the phone's local time.
     */
    public static byte[] extendedTimestamp(long epochSeconds) {
        int t = (int) epochSeconds;
        return new byte[] {
            (byte) (EXTENDED_TIMESTAMP_ID & 0xFF), (byte) (EXTENDED_TIMESTAMP_ID >>> 8),
            5, 0,
            1,
            (byte) t, (byte) (t >>> 8), (byte) (t >>> 16), (byte) (t >>> 24),
        };
    }

    /**
     * Writes the first {@code length} bytes of {@code f} as entry {@code entryName}, STORED
     * or DEFLATED by {@link #method}. STORED reads the file twice (CRC, then data). Every
     * entry carries the file's UTC mtime as an extended timestamp ({@link #extendedTimestamp})
     * besides the DOS time. A file whose mtime is unknown ({@code lastModified()} 0) gets no
     * extended timestamp, which would claim an exact 1970 time, and the oldest DOS time
     * ({@link #DOS_EPOCH_MS}), so a reader takes its copy as old, never as the newest (the
     * desktop importer reads that exact DOS time as unknown).
     */
    public static void addFile(ZipOutputStream z, String entryName, File f, long length, byte[] buf,
                               Progress progress) throws IOException {
        ZipEntry e = new ZipEntry(entryName);
        long mtime = f.lastModified();
        if (mtime > 0) {
            e.setTime(mtime);
            e.setExtra(extendedTimestamp(Math.floorDiv(mtime, 1000L)));
        } else {
            e.setTime(DOS_EPOCH_MS);
        }
        if (method(entryName) == ZipEntry.STORED) {
            e.setMethod(ZipEntry.STORED);
            e.setSize(length);
            e.setCompressedSize(length);
            try (InputStream in = new FileInputStream(f)) { e.setCrc(crc(in, length, buf)); }
        } else {
            e.setMethod(ZipEntry.DEFLATED);
        }
        z.putNextEntry(e);
        try (InputStream in = new FileInputStream(f)) {
            long left = length;
            while (left > 0) {
                int n = in.read(buf, 0, (int) Math.min(buf.length, left));
                if (n < 0) throw new EOFException(f.getName() + " shrank below " + length + " bytes");
                z.write(buf, 0, n);
                left -= n;
                if (progress != null) progress.add(n);
            }
        }
        z.closeEntry();
    }
}
