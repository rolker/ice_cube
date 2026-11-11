package rink.icecube.logger;

import android.content.Context;
import android.os.Build;
import android.os.Environment;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Where the app keeps its files. The primary root is Documents/IceCubeLogger/ on
 * Android 11 and later (it survives uninstall). The fallback root is the app's media
 * folder, Android/media/rink.icecube.logger/, used when Documents cannot be written.
 * Both hold sessions/ and layers/; refs.csv (every ref capture, for the pole check) sits
 * in the root itself.
 */
final class Storage {
    private Storage() { }

    /** Documents/IceCubeLogger, or null before Android 11. */
    static File primaryRoot() {
        if (Build.VERSION.SDK_INT < 30) return null;
        return new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS), "IceCubeLogger");
    }

    static File fallbackRoot(Context ctx) {
        File[] media = ctx.getExternalMediaDirs();
        return media.length > 0 && media[0] != null ? media[0] : ctx.getFilesDir();
    }

    /** Primary (if any) then fallback. */
    static List<File> roots(Context ctx) {
        List<File> r = new ArrayList<>();
        File p = primaryRoot();
        if (p != null) r.add(p);
        r.add(fallbackRoot(ctx));
        return r;
    }

    /** The sub folder name under the first root where it exists or can be created. */
    static File writableDir(Context ctx, String sub) {
        for (File root : roots(ctx)) {
            File d = new File(root, sub);
            if (d.isDirectory() || d.mkdirs()) return d;
        }
        return new File(fallbackRoot(ctx), sub);
    }

    /** Every session folder in either root, newest first (folder names are start times). */
    static List<File> sessions(Context ctx) {
        List<File> out = new ArrayList<>();
        for (File root : roots(ctx)) {
            File[] fs = new File(root, "sessions").listFiles(File::isDirectory);
            if (fs != null) for (File f : fs) out.add(f);
        }
        out.sort((a, b) -> b.getName().compareTo(a.getName()));
        return out;
    }

    /** Bytes in the folder and its sub folders (a session's photos/). */
    static long size(File dir) {
        long n = 0;
        File[] fs = dir.listFiles();
        if (fs != null) for (File f : fs) n += f.isFile() ? f.length() : f.isDirectory() ? size(f) : 0;
        return n;
    }

    static final String REFS = "refs.csv";

    /** refs.csv in each root where it exists, primary first (for reading). */
    static List<File> refsFiles(Context ctx) {
        List<File> out = new ArrayList<>();
        for (File root : roots(ctx)) {
            File f = new File(root, REFS);
            if (f.isFile()) out.add(f);
        }
        return out;
    }

    /** Where to append refs: the first root's refs.csv that can be written (created if missing). */
    static List<File> refsCandidates(Context ctx) {
        List<File> out = new ArrayList<>();
        for (File root : roots(ctx)) out.add(new File(root, REFS));
        return out;
    }
}
