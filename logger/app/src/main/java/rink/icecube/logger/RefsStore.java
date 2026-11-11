package rink.icecube.logger;

import android.content.Context;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import rink.icecube.logger.core.PointRow;

/**
 * refs.csv: every ref capture of every session, appended in the order taken, with the
 * session folder name in front of the points.csv columns. It lives in the storage root
 * (Documents/IceCubeLogger/refs.csv, or the app media folder when Documents cannot be
 * written; see {@link Storage}) and is what the pole check compares a new ref with.
 * It holds positions: it stays on the phone and in Drive, never in the public repo.
 */
final class RefsStore {
    private RefsStore() { }

    /** All readable refs, oldest first within each file (primary root first). Bad lines are skipped. */
    static List<PointRow> readAll(Context ctx) {
        List<PointRow> out = new ArrayList<>();
        for (File f : Storage.refsFiles(ctx)) {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) {
                    if (line.isEmpty() || line.startsWith("session,")) continue;
                    try { out.add(PointRow.parse(PointRow.split(line), 1)); }
                    catch (IllegalArgumentException ignored) { }
                }
            } catch (IOException | SecurityException ignored) {
                // e.g. a file left by an earlier install, which Android no longer lets this app read
            }
        }
        return out;
    }

    /** Appends one ref (creating the file with its header if needed). Returns the file written. */
    static File append(Context ctx, PointRow ref) throws IOException {
        IOException last = null;
        for (File f : Storage.refsCandidates(ctx)) {
            File dir = f.getParentFile();
            if (dir != null && !dir.isDirectory() && !dir.mkdirs()) continue;
            boolean fresh = !f.exists() || f.length() == 0;
            if (!fresh) upgradeHeader(f);
            try (Writer w = new OutputStreamWriter(new FileOutputStream(f, true), StandardCharsets.UTF_8)) {
                if (fresh) w.write(PointRow.REFS_HEADER + "\n");
                w.write(ref.toRefsCsv() + "\n");
                return f;
            } catch (IOException | SecurityException e) {
                last = e instanceof IOException ? (IOException) e : new IOException(e.getMessage());
            }
        }
        throw last != null ? last : new IOException("no writable folder for refs.csv");
    }

    /**
     * A refs.csv started before 0.6 has the shorter header; rows from 0.6 on carry more
     * columns at the end. Rewrites the first line to the current header (rows unchanged), so
     * readers see one header that covers every row. Leaves the file alone if it cannot.
     */
    static void upgradeHeader(File f) {
        List<String> lines = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) lines.add(line);
        } catch (IOException | SecurityException e) {
            return;
        }
        if (lines.isEmpty() || !lines.get(0).startsWith("session,") || lines.get(0).equals(PointRow.REFS_HEADER)) return;
        lines.set(0, PointRow.REFS_HEADER);
        File tmp = new File(f.getPath() + ".tmp");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(tmp), StandardCharsets.UTF_8)) {
            for (String l : lines) w.write(l + "\n");
        } catch (IOException | SecurityException e) {
            //noinspection ResultOfMethodCallIgnored
            tmp.delete();
            return;
        }
        //noinspection ResultOfMethodCallIgnored
        if (!tmp.renameTo(f)) tmp.delete();
    }
}
