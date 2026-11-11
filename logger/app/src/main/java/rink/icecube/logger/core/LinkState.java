package rink.icecube.logger.core;

/**
 * The colour dot of a link on the map's status strip: grey off, amber connecting or
 * retrying, green connected, red when the latest attempt failed (it keeps retrying).
 */
public enum LinkState {
    OFF(0xFF9E9E9E), CONNECTING(0xFFFFA000), CONNECTED(0xFF2E7D32), ERROR(0xFFD32F2F);

    public final int color;

    LinkState(int color) { this.color = color; }

    /** GPS worker status strings: "connecting", "connected", "waiting: <why>", "lost: <why>", "off". */
    public static LinkState gps(boolean active, boolean connected, String status) {
        if (!active) return OFF;
        if (connected) return CONNECTED;
        return status != null && status.startsWith("waiting") ? ERROR : CONNECTING;
    }

    /** NtripClient status strings: "connecting", "connected", "error: <why>", "stopped", "idle". */
    public static LinkState caster(boolean active, boolean connected, String status) {
        if (!active) return OFF;
        if (connected) return CONNECTED;
        return status != null && status.startsWith("error") ? ERROR : CONNECTING;
    }
}
