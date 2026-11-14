package rink.icecube.logger;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.FrameLayout;
import android.widget.ArrayAdapter;
import android.widget.ListView;
import android.widget.TextView;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Session folders, newest first. Tap one to share it as a zip. */
public final class SessionsActivity extends Activity {
    private ListView list;
    private TextView empty;
    private List<File> sessions = new ArrayList<>();

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Screen.applyOrientation(this);
        setTitle("Sessions: tap to share");
        list = new ListView(this);
        empty = new TextView(this);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        empty.setPadding(pad, pad, pad, pad);
        empty.setText("No sessions yet.");
        list.setEmptyView(empty);
        // Rotation keeps this screen (configChanges, see Screen), so a share dialog stays open.
        // In landscape the list is capped to a readable width and centred.
        Screen.MaxWidth m = new Screen.MaxWidth(this);
        m.addView(list);
        m.addView(empty);
        FrameLayout f = new FrameLayout(this);
        f.addView(m, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT, Gravity.CENTER_HORIZONTAL));
        setContentView(f);
        list.setOnItemClickListener((p, v, pos, id) -> Sharer.share(this, sessions.get(pos)));
    }

    @Override protected void onResume() {
        super.onResume();
        Screen.applyOrientation(this);
        sessions = Storage.sessions(this);
        File fallback = Storage.fallbackRoot(this);
        List<String> rows = new ArrayList<>();
        for (File s : sessions) {
            String row = String.format(Locale.US, "%s   %.1f MB", s.getName(), Storage.size(s) / 1e6);
            if (Sharer.isRecording(s)) row += "   (recording)";
            if (s.getAbsolutePath().startsWith(fallback.getAbsolutePath())) row += "\n  app media folder";
            rows.add(row);
        }
        list.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_list_item_1, rows));
    }
}
