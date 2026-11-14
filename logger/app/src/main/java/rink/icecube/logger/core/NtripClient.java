package rink.icecube.logger.core;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * NTRIP v1 client. Sends the latest GGA upstream when the source has one, and hands
 * every received byte to a sink unchanged. On failure it reconnects after a delay that
 * starts at 5 s and doubles up to 60 s; a connection that delivered data resets it.
 * Events never include the password.
 */
public final class NtripClient {
    public interface Sink { void onRtcm(byte[] data, int n); }
    public interface GgaSource { String latest(); }
    public interface Events { void onEvent(String text); }

    public static final long FIRST_RETRY_MS = 5000, MAX_RETRY_MS = 60000;

    private final String host, mount, user, pass;
    private final int port;
    private final Sink sink;
    private final GgaSource gga;
    private final Events events;
    private volatile boolean running;
    private volatile Socket socket;
    private Thread thread;

    public volatile String status = "idle";
    public volatile boolean connected;
    public volatile long rtcmBytes, connects, ggaSent;
    /** System.nanoTime() of the last received byte, or 0. */
    public volatile long lastDataNanos;
    public long ggaPeriodMs = 10000;

    public NtripClient(String host, int port, String mount, String user, String pass,
                       Sink sink, GgaSource gga, Events events) {
        this.host = host; this.port = port; this.mount = mount; this.user = user; this.pass = pass;
        this.sink = sink; this.gga = gga;
        this.events = events != null ? events : t -> { };
    }

    public void start() {
        running = true;
        thread = new Thread(this::loop, "ntrip");
        thread.start();
    }

    public void stop() {
        running = false;
        Socket s = socket;
        if (s != null) { try { s.close(); } catch (IOException ignored) { } }
        if (thread != null) thread.interrupt();
    }

    public void join(long ms) throws InterruptedException { if (thread != null) thread.join(ms); }

    public boolean isRunning() { return running; }

    /** Delay before the next attempt after a failure. */
    public static long nextRetry(long current) { return Math.min(MAX_RETRY_MS, current * 2); }

    /** Seconds since the last received byte, or -1 if none yet. */
    public double secondsSinceData() {
        long t = lastDataNanos;
        return t == 0 ? -1 : (System.nanoTime() - t) / 1e9;
    }

    private void loop() {
        long retry = FIRST_RETRY_MS;
        while (running) {
            long before = rtcmBytes;
            events.onEvent("caster connecting mount=" + mount);
            try {
                session();
            } catch (IOException e) {
                status = "error: " + e.getMessage();
            }
            boolean wasConnected = connected;
            connected = false;
            if (!running) break;
            if (rtcmBytes > before) retry = FIRST_RETRY_MS;
            events.onEvent((wasConnected ? "caster lost: " : "caster failed: ") + status
                    + "; retry in " + retry / 1000 + " s");
            try { Thread.sleep(retry); } catch (InterruptedException e) { break; }
            retry = nextRetry(retry);
        }
        connected = false;
        status = "stopped";
    }

    private void session() throws IOException {
        status = "connecting";
        try (Socket s = new Socket()) {
            socket = s;
            s.connect(new InetSocketAddress(host, port), 10000);
            s.setTcpNoDelay(true);
            s.setSoTimeout(1000);
            OutputStream out = s.getOutputStream();
            InputStream in = s.getInputStream();
            out.write(request().getBytes(StandardCharsets.ISO_8859_1));
            out.flush();
            byte[] first = readResponseHead(in);
            connects++;
            connected = true;
            status = "connected";
            events.onEvent("caster connected");
            long connectedAt = System.nanoTime();
            if (first.length > 0) deliver(first, first.length);
            byte[] buf = new byte[4096];
            long nextGga = 0;
            while (running) {
                long now = System.currentTimeMillis();
                if (now >= nextGga) {
                    String g = gga == null ? null : gga.latest();
                    if (g != null) {
                        out.write((g + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                        out.flush();
                        ggaSent++;
                        nextGga = now + ggaPeriodMs;
                    } else nextGga = now + 1000;
                }
                int n;
                try {
                    n = in.read(buf);
                } catch (java.net.SocketTimeoutException e) {
                    long last = Math.max(lastDataNanos, connectedAt);
                    if (System.nanoTime() - last > 30_000_000_000L) throw new IOException("no data for 30 s");
                    continue;
                }
                if (n < 0) throw new IOException("caster closed the connection");
                deliver(buf, n);
            }
        } finally {
            socket = null;
        }
    }

    private void deliver(byte[] b, int n) {
        rtcmBytes += n;
        lastDataNanos = System.nanoTime();
        sink.onRtcm(b, n);
    }

    String request() { return request(host, mount, user, pass); }

    /** The NTRIP 1.0 GET request for a mount point; an empty mount asks for the sourcetable. */
    static String request(String host, String mount, String user, String pass) {
        StringBuilder r = new StringBuilder();
        r.append("GET /").append(mount).append(" HTTP/1.0\r\n");
        r.append("Host: ").append(host).append("\r\n");
        r.append("Ntrip-Version: Ntrip/1.0\r\n");
        r.append("User-Agent: NTRIP IceCubeLogger/0.3\r\n");
        if (user != null && !user.isEmpty()) {
            String c = user + ":" + (pass == null ? "" : pass);
            r.append("Authorization: Basic ")
             .append(Base64.getEncoder().encodeToString(c.getBytes(StandardCharsets.UTF_8))).append("\r\n");
        }
        r.append("\r\n");
        return r.toString();
    }

    /** Reads the status line and any headers. Returns stream bytes that arrived after them. */
    private byte[] readResponseHead(InputStream in) throws IOException {
        ByteArrayOutputStream h = new ByteArrayOutputStream();
        byte[] b = new byte[1024];
        long deadline = System.currentTimeMillis() + 10000;
        while (System.currentTimeMillis() < deadline) {
            int n;
            try { n = in.read(b); } catch (java.net.SocketTimeoutException e) { n = 0; }
            if (n < 0) throw new IOException("caster closed during handshake: " + firstLine(h));
            h.write(b, 0, n);
            byte[] all = h.toByteArray();
            int eol = indexOf(all, new byte[] {'\r', '\n'}, 0);
            if (eol < 0) continue;
            String line = new String(all, 0, eol, StandardCharsets.ISO_8859_1);
            if (line.startsWith("SOURCETABLE")) throw new IOException("mount point not found");
            if (!line.contains(" 200 ") && !line.endsWith(" 200")) throw new IOException("caster refused: " + line);
            if (line.startsWith("HTTP")) {
                int end = indexOf(all, new byte[] {'\r', '\n', '\r', '\n'}, 0);
                if (end < 0) continue;
                return tail(all, end + 4);
            }
            int start = eol + 2;
            if (all.length >= start + 2 && all[start] == '\r' && all[start + 1] == '\n') start += 2;
            return tail(all, start);
        }
        throw new IOException("no reply from caster");
    }

    private static byte[] tail(byte[] a, int from) {
        byte[] t = new byte[a.length - from];
        System.arraycopy(a, from, t, 0, t.length);
        return t;
    }

    private static String firstLine(ByteArrayOutputStream h) {
        String s = new String(h.toByteArray(), StandardCharsets.ISO_8859_1);
        int i = s.indexOf('\r');
        return i > 0 ? s.substring(0, i) : s;
    }

    private static int indexOf(byte[] a, byte[] p, int from) {
        outer:
        for (int i = from; i + p.length <= a.length; i++) {
            for (int k = 0; k < p.length; k++) if (a[i + k] != p[k]) continue outer;
            return i;
        }
        return -1;
    }
}
