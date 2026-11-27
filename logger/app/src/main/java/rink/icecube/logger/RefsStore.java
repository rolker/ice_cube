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
import rink.icecube.logger.core.VoidRule;

/**
 * refs.csv: every ref capture of every session, appended in the order taken, with the
 * session folder name in front of the points.csv columns. It lives in the storage root
 * (Documents/IceCubeLogger/refs.csv, or the app media folder when Documents cannot be
 * written; see {@link Storage}) and is what the pole check compares a new ref with.
 * It holds positions: it stays on the phone and in Drive, never in the public repo.
 *
 * From 0.9.0 a ref flagged as bad keeps its row with void=1 ({@link #setVoid} rewrites the
 * row in place, the one write that is not an append); readers here skip it. When copies
 * disagree, the newest copy (by file time) decides, voided on a tie
 * ({@link VoidRule#liveRefs}).
 */
final class RefsStore {
    private RefsStore() { }

    /**
     * All readable refs that are not voided ({@link VoidRule#liveRefs}), oldest first within
     * each file (primary root first). Bad lines are skipped.
     */
    static List<PointRow> readAll(Context ctx) {
        List<VoidRule.RefsCopy> copies = new ArrayList<>();
        for (File f : Storage.refsFiles(ctx)) {
            List<PointRow> rows = new ArrayList<>();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) {
                    if (line.isEmpty() || line.startsWith("session,")) continue;
                    try {
                        rows.add(PointRow.parse(PointRow.split(line), 1));
                    } catch (IllegalArgumentException ignored) { }
                }
                copies.add(new VoidRule.RefsCopy(f.lastModified(), rows));
            } catch (IOException | SecurityException ignored) {
                // e.g. a file left by an earlier install, which Android no longer lets this app read
            }
        }
        return VoidRule.liveRefs(copies);
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

    /** What {@link #setVoid} did: copies holding the row, copies rewritten, rows changed, copies unreadable. */
    static final class VoidWrite {
        int holding, rewritten, rows, unreadable;

        /** The events.csv line for ref n (see {@link VoidRule#refsVoidEvent}). */
        String event(int n, boolean voided) {
            return VoidRule.refsVoidEvent(n, voided, holding, rewritten, rows, unreadable);
        }
    }

    /**
     * Sets the void columns of the (session, n) row in every refs.csv (one per storage root)
     * that holds it, each rewritten whole through a temporary file and a rename. Files that
     * cannot be read or written are left alone and counted, so a partial write is reported.
     */
    static VoidWrite setVoid(Context ctx, String session, int n, boolean voided, String reason) {
        VoidWrite w = new VoidWrite();
        for (File f : Storage.refsFiles(ctx)) {
            List<String> lines = readLines(f);
            if (lines == null) { w.unreadable++; continue; }
            int k = VoidRule.setRefsVoid(lines, session, n, voided, reason);
            if (k == 0) continue;
            w.holding++;
            if (writeLines(f, lines)) { w.rewritten++; w.rows += k; }
        }
        return w;
    }

    private static List<String> readLines(File f) {
        List<String> lines = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) lines.add(line);
        } catch (IOException | SecurityException e) {
            return null;
        }
        return lines;
    }

    /** Replaces f with lines, through f.tmp and a rename. False if it could not. */
    private static boolean writeLines(File f, List<String> lines) {
        File tmp = new File(f.getPath() + ".tmp");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(tmp), StandardCharsets.UTF_8)) {
            for (String l : lines) w.write(l + "\n");
        } catch (IOException | SecurityException e) {
            //noinspection ResultOfMethodCallIgnored
            tmp.delete();
            return false;
        }
        if (tmp.renameTo(f)) return true;
        //noinspection ResultOfMethodCallIgnored
        tmp.delete();
        return false;
    }

    /**
     * A refs.csv started before 0.6 (or 0.9.0) has a shorter header; newer rows carry more
     * columns at the end. Rewrites the first line to the current header (rows unchanged), so
     * readers see one header that covers every row. Leaves the file alone if it cannot.
     */
    static void upgradeHeader(File f) {
        List<String> lines = readLines(f);
        if (lines == null || lines.isEmpty() || !lines.get(0).startsWith("session,") || lines.get(0).equals(PointRow.REFS_HEADER)) return;
        lines.set(0, PointRow.REFS_HEADER);
        writeLines(f, lines);
    }
}
