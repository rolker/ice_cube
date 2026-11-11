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
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Zips a session folder into cache/share/<session>.zip and opens the share sheet with it
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
                send(a, z, dir.getName());
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

    /** Writes cache/share/<name>.zip with the folder's files (and photos/) under <name>/. Older zips are deleted. */
    static File zip(Activity a, File dir, boolean snapshot, Progress progress) throws IOException {
        File out = ShareProvider.dir(a);
        if (!out.isDirectory() && !out.mkdirs()) throw new IOException("cannot create " + out);
        File[] old = out.listFiles();
        if (old != null) for (File f : old) //noinspection ResultOfMethodCallIgnored
            f.delete();
        java.util.List<String> names = filesIn(dir);
        File[] files = new File[names.size()];
        for (int i = 0; i < files.length; i++) files[i] = new File(dir, names.get(i));
        long[] lengths = new long[files.length];
        long total = 0;
        for (int i = 0; i < files.length; i++) { lengths[i] = files[i].length(); total += lengths[i]; }
        File zip = new File(out, dir.getName() + (snapshot ? "_snapshot" : "") + ".zip");
        long done = 0, lastReport = 0;
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
            for (int i = 0; i < files.length; i++) {
                z.putNextEntry(new ZipEntry(dir.getName() + "/" + names.get(i)));
                try (InputStream in = new FileInputStream(files[i])) {
                    long left = lengths[i];
                    int n;
                    while (left > 0 && (n = in.read(buf, 0, (int) Math.min(buf.length, left))) > 0) {
                        z.write(buf, 0, n);
                        left -= n;
                        done += n;
                        if (done - lastReport > (4 << 20)) { lastReport = done; progress.update(done, total); }
                    }
                }
                z.closeEntry();
            }
        } catch (IOException e) {
            //noinspection ResultOfMethodCallIgnored
            zip.delete();
            throw e;
        }
        return zip;
    }

    private static void send(Activity a, File zip, String session) {
        Uri uri = ShareProvider.uriFor(zip);
        Intent i = new Intent(Intent.ACTION_SEND)
                .setType("application/zip")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .putExtra(Intent.EXTRA_SUBJECT, "Ice Cube Logger session " + session)
                .putExtra(Intent.EXTRA_TITLE, zip.getName())
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        i.setClipData(ClipData.newRawUri(zip.getName(), uri));
        a.startActivity(Intent.createChooser(i, "Share " + zip.getName()));
    }
}
