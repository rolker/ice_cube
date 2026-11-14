package rink.icecube.logger;

import android.content.Context;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import rink.icecube.logger.core.BaseCheck;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.ReferenceFrame;

/** Status texts shared by the map (main screen) and the Details screen. */
final class Status {
    private Status() { }

    /** The frame line: in use and its source, or what the setting says when no caster runs. */
    static String frameText(LoggerService s, Context c) {
        ReferenceFrame.Resolved r = s == null ? null : s.frame();
        if (r == null) r = ReferenceFrame.resolve(Settings.frame(Settings.prefs(c)), null);
        String t = "frame     " + r.describe();
        String note = s == null || s.caster() == null ? null : s.sourcetableNote();
        if (note != null && !"sourcetable".equals(r.source)) t += "\n  sourcetable: " + note;
        return t;
    }

    /** "layers match base" when a layer base point agrees with the received ARP, else null. */
    static String layerOkText(LoggerService s) {
        BaseCheck c = s == null ? null : s.layerCheck();
        return c != null && c.ok ? c.describe() + " [" + s.layerCheckFile() + "]" : null;
    }

    /**
     * Warnings for the red bar: frame mismatch, base moved, layers not in the base's frame,
     * a failed pole check, and a GPS or caster link that was up and is now lost (retrying)
     * or a GPS that went silent. Null when there is none.
     */
    static String warnings(LoggerService s) {
        if (s == null) return null;
        List<String> w = new ArrayList<>();
        ReferenceFrame.Resolved r = s.frame();
        if (r != null && r.warning != null) w.add(r.warning);
        String moved = s.baseWatch().warning();
        if (moved != null) w.add(moved);
        BaseCheck c = s.layerCheck();
        if (c != null && !c.ok) w.add(c.describe() + " [" + s.layerCheckFile() + "]");
        Survey sv = s.survey();
        String pole = sv == null ? null : sv.poleWarning();
        if (pole != null) w.add(pole);
        if (s.gpsLost()) w.add("GPS link lost (" + s.gpsStatus() + "): retrying");
        else if (s.gpsConnected() && s.gpsSecondsSinceData() > 5) {
            w.add(String.format(Locale.US, "GPS silent for %.0f s", s.gpsSecondsSinceData()));
        }
        NtripClient n = s.caster();
        if (n != null && !n.connected && n.connects > 0) w.add("caster link lost (" + n.status + "): retrying");
        return w.isEmpty() ? null : android.text.TextUtils.join("\n", w);
    }
}
