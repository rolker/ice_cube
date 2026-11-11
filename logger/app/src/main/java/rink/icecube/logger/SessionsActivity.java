package rink.icecube.logger;

import android.app.Activity;
import android.os.Bundle;
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
        setTitle("Sessions: tap to share");
        list = new ListView(this);
        empty = new TextView(this);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        empty.setPadding(pad, pad, pad, pad);
        empty.setText("No sessions yet.");
        list.setEmptyView(empty);
        android.widget.FrameLayout f = new android.widget.FrameLayout(this);
        f.addView(list);
        f.addView(empty);
        setContentView(f);
        list.setOnItemClickListener((p, v, pos, id) -> Sharer.share(this, sessions.get(pos)));
    }

    @Override protected void onResume() {
        super.onResume();
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
