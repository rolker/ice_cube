package rink.icecube.logger.core;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** The coverage test vectors written by {@code python3 -m icecube.logger_vectors} (src/test/resources). */
final class Vectors {
    private static JSONObject cached;

    private Vectors() { }

    static synchronized JSONObject load() throws Exception {
        if (cached != null) return cached;
        try (InputStream in = Vectors.class.getResourceAsStream("/coverage_vectors.json")) {
            if (in == null) throw new IOException("coverage_vectors.json not on the test classpath");
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            for (int k; (k = in.read(buf)) > 0; ) b.write(buf, 0, k);
            cached = new JSONObject(new String(b.toByteArray(), StandardCharsets.UTF_8));
        }
        return cached;
    }

    /** A number, or NaN for JSON null (the Python side's None). */
    static double num(JSONObject o, String key) throws Exception { return o.isNull(key) ? Double.NaN : o.getDouble(key); }
}
