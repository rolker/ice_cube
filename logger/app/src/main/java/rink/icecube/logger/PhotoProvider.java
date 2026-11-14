package rink.icecube.logger;

import android.annotation.SuppressLint;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.List;
import java.util.UUID;

/**
 * Lets the system camera (ACTION_IMAGE_CAPTURE) write one photo into the session's photos/
 * folder. It serves exactly one pending file, at content://<package>.photo/<token>/<name>
 * with a random token per photo, and nothing else; the camera gets a write grant on the
 * intent for that one URI, which is revoked when the result comes back. Not exported.
 * The pending file is kept in its own SharedPreferences file, so a camera that writes after
 * the app's process was restarted still finds it.
 *
 * ShareProvider (session zips, read only) is left as it is.
 */
public final class PhotoProvider extends ContentProvider {
    static final String AUTHORITY = BuildConfig.APPLICATION_ID + ".photo";
    private static final String PREFS = "pending_photo", PATH = "path", TOKEN = "token";

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE); }

    /** Makes f the pending photo and returns its URI. commit, not apply: it must be on disk before the camera opens. */
    @SuppressLint("ApplySharedPref")
    static Uri expose(Context c, File f) {
        String token = UUID.randomUUID().toString().replace("-", "");
        prefs(c).edit().putString(PATH, f.getAbsolutePath()).putString(TOKEN, token).commit();
        return new Uri.Builder().scheme("content").authority(AUTHORITY).appendPath(token).appendPath(f.getName()).build();
    }

    /** Nothing is pending any more: the URI stops working. */
    @SuppressLint("ApplySharedPref")
    static void clear(Context c) { prefs(c).edit().clear().commit(); }

    @Override public boolean onCreate() { return true; }

    private File fileFor(Uri uri) {
        Context c = getContext();
        if (c == null) return null;
        List<String> seg = uri.getPathSegments();
        SharedPreferences p = prefs(c);
        String path = p.getString(PATH, null), token = p.getString(TOKEN, null);
        if (seg.size() != 2 || path == null || token == null || !token.equals(seg.get(0))) return null;
        File f = new File(path);
        return f.getName().equals(seg.get(1)) ? f : null;
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f = fileFor(uri);
        if (f == null) return null;
        String[] cols = projection != null ? projection : new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override public String getType(Uri uri) { return "image/jpeg"; }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = fileFor(uri);
        if (f == null) throw new FileNotFoundException("no pending photo for " + uri);
        int m;
        try { m = ParcelFileDescriptor.parseMode(mode); }
        catch (IllegalArgumentException e) { throw new FileNotFoundException("bad mode " + mode); }
        return ParcelFileDescriptor.open(f, m | ParcelFileDescriptor.MODE_CREATE);
    }

    @Override public Uri insert(Uri uri, ContentValues v) { throw new UnsupportedOperationException("no inserts"); }
    @Override public int delete(Uri uri, String s, String[] a) { return 0; }
    @Override public int update(Uri uri, ContentValues v, String s, String[] a) { return 0; }
}
