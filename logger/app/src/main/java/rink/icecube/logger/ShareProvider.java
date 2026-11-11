package rink.icecube.logger;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.List;

/**
 * Minimal read-only provider for the session zips in cache/share/, so other apps (Google
 * Drive from the share sheet) can read them through a content:// URI with a temporary read
 * grant. Not exported; access only through the grant on the share intent. It serves plain
 * file names in that one folder and nothing else.
 *
 * Receivers name the upload from the share intent's EXTRA_SUBJECT/EXTRA_TITLE or from
 * query()'s DISPLAY_NAME (falling back to the URI's last path segment); Sharer sets all of
 * them to the same IceCubeLogger_<session>.zip, and getType()/getStreamTypes() and the
 * mime_type column say application/zip.
 */
public final class ShareProvider extends ContentProvider {
    static final String AUTHORITY = BuildConfig.APPLICATION_ID + ".share";

    static File dir(android.content.Context c) { return new File(c.getCacheDir(), "share"); }

    static Uri uriFor(File f) {
        return new Uri.Builder().scheme("content").authority(AUTHORITY).appendPath(f.getName()).build();
    }

    @Override public boolean onCreate() { return true; }

    private File fileFor(Uri uri) {
        List<String> seg = uri.getPathSegments();
        if (seg.size() != 1 || getContext() == null) return null;
        String name = seg.get(0);
        if (name.isEmpty() || name.startsWith(".") || name.contains("/") || name.contains("\\")) return null;
        File f = new File(dir(getContext()), name);
        return f.isFile() ? f : null;
    }

    static final String ZIP = "application/zip";
    private static final String MIME_COLUMN = "mime_type"; // DocumentsContract.Document.COLUMN_MIME_TYPE

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f = fileFor(uri);
        if (f == null) return null;
        String[] cols = projection != null ? projection
                : new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE, MIME_COLUMN};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
            else if (MIME_COLUMN.equals(cols[i])) row[i] = typeOf(f.getName());
        }
        c.addRow(row);
        return c;
    }

    private static String typeOf(String name) {
        return name != null && name.endsWith(".zip") ? ZIP : "application/octet-stream";
    }

    @Override public String getType(Uri uri) { return typeOf(uri.getLastPathSegment()); }

    @Override public String[] getStreamTypes(Uri uri, String filter) {
        String t = getType(uri);
        return android.content.ClipDescription.compareMimeTypes(t, filter) ? new String[] {t} : null;
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("read only");
        File f = fileFor(uri);
        if (f == null) throw new FileNotFoundException(uri.toString());
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public Uri insert(Uri uri, ContentValues v) { throw new UnsupportedOperationException("read only"); }
    @Override public int delete(Uri uri, String s, String[] a) { return 0; }
    @Override public int update(Uri uri, ContentValues v, String s, String[] a) { return 0; }
}
