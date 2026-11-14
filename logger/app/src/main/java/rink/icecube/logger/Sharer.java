package rink.icecube.logger;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import rink.icecube.logger.core.ShareZip;

/**
 * Zips a session folder (and refs.csv) into cache/share/IceCubeLogger_<session>.zip and opens the share sheet with it
 * (application/zip through {@link ShareProvider}). A session that is still recording is
 * not zipped silently: the user chooses Stop and share, or Share snapshot, which flushes
 * the files and zips each one up to its length at that moment, adding SNAPSHOT.txt.
 */
final class Sharer {
    private Sharer() { }

    static boolean isRecording(File dir) {
        LoggerService s = LoggerService.current;
        return s != null && s.sessionOpen() && dir.equals(s.folder());
    }

    static void share(Activity a, File dir) {
        if (!isRecording(dir)) { zipAndSend(a, dir, false); return; }
        new AlertDialog.Builder(a)
                .setTitle("Session still recording")
                .setMessage(dir.getName() + " is the running session. Stop it first and share the complete files, "
                        + "or share a snapshot of what is written so far and keep recording?")
                .setPositiveButton("Stop and share", (d, w) -> stopThenZip(a, dir))
                .setNeutralButton("Share snapshot", (d, w) -> zipAndSend(a, dir, true))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private static void stopThenZip(Activity a, File dir) {
        a.startForegroundService(new Intent(a, LoggerService.class).setAction(LoggerService.ACTION_STOP));
        AlertDialog wait = progress(a, "Stopping session…");
        Handler ui = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            long until = SystemClock.elapsedRealtime() + 15000;
            while (isRecording(dir) && SystemClock.elapsedRealtime() < until) SystemClock.sleep(200);
            boolean stopped = !isRecording(dir);
            ui.post(() -> {
                wait.dismiss();
                if (stopped) zipAndSend(a, dir, false);
                else message(a, "The session did not stop. Try again.");
            });
        }, "share-stop").start();
    }

    private static AlertDialog progress(Activity a, String text) {
        AlertDialog d = new AlertDialog.Builder(a).setMessage(text).setCancelable(false).create();
        d.show();
        return d;
    }

    private static void message(Activity a, String text) {
        if (!a.isFinishing()) new AlertDialog.Builder(a).setMessage(text).setPositiveButton("OK", null).show();
    }

    private static void zipAndSend(Activity a, File dir, boolean snapshot) {
        AlertDialog d = progress(a, "Zipping " + dir.getName() + "…");
        Handler ui = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            File zip;
            String err = null;
            try {
                if (snapshot) {
                    LoggerService s = LoggerService.current;
                    if (s != null) { s.event("share snapshot"); s.flushSession(); }
                }
                zip = zip(a, dir, snapshot, (done, total) -> ui.post(() -> d.setMessage(String.format(Locale.US,
                        "Zipping %s…\n%.1f of %.1f MB", dir.getName(), done / 1e6, total / 1e6))));
            } catch (IOException | RuntimeException e) {
                zip = null;
                err = e.getMessage();
            }
            File z = zip;
            String e = err;
            ui.post(() -> {
                d.dismiss();
                if (z == null) { message(a, "Could not zip the session: " + e); return; }
                send(a, z);
            });
        }, "share-zip").start();
    }

    interface Progress { void update(long done, long total); }

    /** The folder's files and those one level down (photos/), sorted by relative path. */
    static java.util.List<String> filesIn(File dir) throws IOException {
        File[] top = dir.listFiles();
        if (top == null) throw new IOException("cannot read " + dir);
        java.util.List<String> out = new java.util.ArrayList<>();
        for (File f : top) {
            if (f.isFile()) out.add(f.getName());
            else if (f.isDirectory()) {
                File[] sub = f.listFiles(File::isFile);
                if (sub != null) for (File g : sub) out.add(f.getName() + "/" + g.getName());
            }
        }
        java.util.Collections.sort(out);
        return out;
    }

    /** refs.csv to include: the one in the session's own storage root, else the first that exists, else null. */
    static File refsFor(Activity a, File dir) {
        File parent = dir.getParentFile();
        File root = parent != null ? parent.getParentFile() : null;
        if (root != null) {
            File own = new File(root, Storage.REFS);
            if (own.isFile()) return own;
        }
        java.util.List<File> all = Storage.refsFiles(a);
        return all.isEmpty() ? null : all.get(0);
    }

    /**
     * Writes cache/share/IceCubeLogger_<name>.zip with the folder's files (and photos/) under
     * <name>/, plus refs.csv from the storage root at the zip's top level when it exists.
     * Photos and other compressed files are STORED, logs DEFLATED. Older zips are deleted.
     */
    static File zip(Activity a, File dir, boolean snapshot, Progress progress) throws IOException {
        File out = ShareProvider.dir(a);
        if (!out.isDirectory() && !out.mkdirs()) throw new IOException("cannot create " + out);
        File[] old = out.listFiles();
        if (old != null) for (File f : old) //noinspection ResultOfMethodCallIgnored
            f.delete();
        java.util.List<String> names = filesIn(dir);
        java.util.List<String> entries = new java.util.ArrayList<>();
        java.util.List<File> files = new java.util.ArrayList<>();
        for (String n : names) { entries.add(dir.getName() + "/" + n); files.add(new File(dir, n)); }
        File refs = refsFor(a, dir);
        if (refs != null) { entries.add(Storage.REFS); files.add(refs); }
        // Lengths fixed now: a snapshot copies each file up to this length even if it keeps growing.
        long[] lengths = new long[files.size()];
        long total = 0;
        for (int i = 0; i < lengths.length; i++) { lengths[i] = files.get(i).length(); total += lengths[i]; }
        File zip = new File(out, ShareZip.fileName(dir.getName(), snapshot));
        long[] done = {0, 0};
        long all = total;
        byte[] buf = new byte[1 << 16];
        try (ZipOutputStream z = new ZipOutputStream(new FileOutputStream(zip))) {
            if (snapshot) {
                z.putNextEntry(new ZipEntry(dir.getName() + "/SNAPSHOT.txt"));
                z.write(("Snapshot taken while the session was recording, at elapsed_ns="
                        + SystemClock.elapsedRealtimeNanos() + ". Each file is cut at its length at that moment, "
                        + "so the last chunk of a log may be incomplete and there is no stop event.\n")
                        .getBytes(StandardCharsets.UTF_8));
                z.closeEntry();
            }
            for (int i = 0; i < lengths.length; i++) {
                ShareZip.addFile(z, entries.get(i), files.get(i), lengths[i], buf, n -> {
                    done[0] += n;
                    if (done[0] - done[1] > (4 << 20)) { done[1] = done[0]; progress.update(done[0], all); }
                });
            }
        } catch (IOException e) {
            //noinspection ResultOfMethodCallIgnored
            zip.delete();
            throw e;
        }
        return zip;
    }

    private static void send(Activity a, File zip) {
        Uri uri = ShareProvider.uriFor(zip);
        Intent i = new Intent(Intent.ACTION_SEND)
                .setType(ShareProvider.ZIP)
                .putExtra(Intent.EXTRA_STREAM, uri)
                // Drive's upload dialog names the file from EXTRA_SUBJECT/EXTRA_TITLE when set,
                // else from the provider's DISPLAY_NAME; all three are the .zip file name.
                .putExtra(Intent.EXTRA_SUBJECT, zip.getName())
                .putExtra(Intent.EXTRA_TITLE, zip.getName())
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        i.setClipData(ClipData.newRawUri(zip.getName(), uri));
        a.startActivity(Intent.createChooser(i, "Share " + zip.getName()));
    }
}
