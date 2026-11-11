package rink.icecube.logger.core;

import java.io.BufferedOutputStream;
import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.Writer;

/**
 * Writes a byte stream unchanged to a data file, and one CSV line per write:
 * elapsed_ns,byte_offset,length. Offsets keep counting across link drops.
 */
public final class ChunkLog implements Closeable {
    public static final String HEADER = "elapsed_ns,byte_offset,length\n";
    private final OutputStream data;
    private final Writer csv;
    private long offset;

    public ChunkLog(File dataFile, File csvFile) throws IOException {
        this(new BufferedOutputStream(new FileOutputStream(dataFile), 1 << 16),
             new BufferedWriter(new FileWriter(csvFile), 1 << 14));
    }

    public ChunkLog(OutputStream data, Writer csv) throws IOException {
        this.data = data;
        this.csv = csv;
        csv.write(HEADER);
    }

    public synchronized void write(long elapsedNs, byte[] b, int n) throws IOException {
        data.write(b, 0, n);
        csv.write(elapsedNs + "," + offset + "," + n + "\n");
        offset += n;
    }

    public synchronized long offset() { return offset; }

    public synchronized void flush() throws IOException {
        data.flush();
        csv.flush();
    }

    @Override public synchronized void close() throws IOException {
        try { data.close(); } finally { csv.close(); }
    }
}
