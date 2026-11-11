package rink.icecube.logger.core;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * Fetches an NTRIP sourcetable (GET / with the same headers as {@link NtripClient}) and
 * reads the misc field (the last field) of a mount point's STR record. STR fields are:
 * STR;mountpoint;identifier;format;format-details;carrier;nav-system;network;country;
 * latitude;longitude;nmea;solution;generator;compr-encryp;authentication;fee;bitrate;misc.
 */
public final class Sourcetable {
    private Sourcetable() { }

    public static String fetch(String host, int port, String user, String pass, int timeoutMs) throws IOException {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress(host, port), timeoutMs);
            s.setSoTimeout(timeoutMs);
            OutputStream out = s.getOutputStream();
            out.write(NtripClient.request(host, "", user, pass).getBytes(StandardCharsets.ISO_8859_1));
            out.flush();
            InputStream in = s.getInputStream();
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            long deadline = System.currentTimeMillis() + 3L * timeoutMs;
            int n;
            try {
                while ((n = in.read(buf)) > 0) {
                    b.write(buf, 0, n);
                    if (b.size() > (4 << 20)) throw new IOException("sourcetable larger than 4 MB");
                    if (b.toString("ISO-8859-1").contains("ENDSOURCETABLE")) break;
                    if (System.currentTimeMillis() > deadline) throw new IOException("sourcetable too slow");
                }
            } catch (java.net.SocketTimeoutException e) {
                if (b.size() == 0) throw new IOException("no reply to sourcetable request");
            }
            return body(new String(b.toByteArray(), StandardCharsets.ISO_8859_1));
        }
    }

    /** Strips the status line and headers, and decodes chunked transfer (NTRIP 2 casters). */
    static String body(String reply) throws IOException {
        int eol = reply.indexOf("\r\n");
        String first = eol < 0 ? reply : reply.substring(0, eol);
        if (!first.contains("200")) throw new IOException("caster refused: " + first);
        int end = reply.indexOf("\r\n\r\n");
        if (end < 0) return eol < 0 ? "" : reply.substring(eol + 2);
        String head = reply.substring(0, end).toLowerCase(Locale.US);
        String body = reply.substring(end + 4);
        if (head.contains("transfer-encoding: chunked")) body = dechunk(body);
        return body;
    }

    private static String dechunk(String s) {
        StringBuilder out = new StringBuilder();
        int i = 0;
        while (i < s.length()) {
            int eol = s.indexOf("\r\n", i);
            if (eol < 0) break;
            int size;
            try { size = Integer.parseInt(s.substring(i, eol).split(";")[0].trim(), 16); }
            catch (NumberFormatException e) { break; }
            if (size == 0) break;
            int start = eol + 2, stop = Math.min(s.length(), start + size);
            out.append(s, start, stop);
            i = stop + 2;
        }
        return out.toString();
    }

    /** The misc field of the mount point's STR record (case-sensitive mount), or null if absent. */
    public static String misc(String table, String mount) {
        for (String line : table.split("\r?\n")) {
            if (!line.startsWith("STR;")) continue;
            String[] f = line.split(";", -1);
            if (f.length < 2 || !f[1].equals(mount)) continue;
            if (f.length < 19) return f.length > 2 ? f[f.length - 1].trim() : "";
            StringBuilder m = new StringBuilder(f[18]);
            for (int k = 19; k < f.length; k++) m.append(';').append(f[k]);   // misc may itself contain ';'
            return m.toString().trim();
        }
        return null;
    }
}
