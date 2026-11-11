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

    /**
     * Writes the first {@code length} bytes of {@code f} as entry {@code entryName}, STORED
     * or DEFLATED by {@link #method}. STORED reads the file twice (CRC, then data).
     */
    public static void addFile(ZipOutputStream z, String entryName, File f, long length, byte[] buf,
                               Progress progress) throws IOException {
        ZipEntry e = new ZipEntry(entryName);
        e.setTime(f.lastModified());
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
