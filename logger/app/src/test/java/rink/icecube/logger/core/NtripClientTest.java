package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.Test;

public class NtripClientTest {
    @Test public void backoffDoublesToSixtySeconds() {
        long d = NtripClient.FIRST_RETRY_MS;
        StringBuilder s = new StringBuilder();
        for (int i = 0; i < 6; i++) { s.append(d / 1000).append(' '); d = NtripClient.nextRetry(d); }
        assertEquals("5 10 20 40 60 60 ", s.toString());
    }

    @Test public void receivesBytesUnchangedAndSendsNoGgaWithoutGps() throws Exception {
        byte[] rtcm = Rtcm3Parser.frame(new byte[] {(byte) 0x3E, (byte) 0xD0, 1, 2, 3});
        try (ServerSocket server = new ServerSocket(0)) {
            ByteArrayOutputStream request = new ByteArrayOutputStream();
            Thread caster = new Thread(() -> {
                try (Socket s = server.accept()) {
                    InputStream in = s.getInputStream();
                    byte[] b = new byte[4096];
                    int n = in.read(b);
                    request.write(b, 0, n);
                    OutputStream out = s.getOutputStream();
                    out.write("ICY 200 OK\r\n\r\n".getBytes(StandardCharsets.US_ASCII));
                    out.write(rtcm);
                    out.flush();
                    s.setSoTimeout(1500);
                    try { n = in.read(b); if (n > 0) request.write(b, 0, n); } catch (java.net.SocketTimeoutException ignored) { }
                } catch (Exception ignored) { }
            });
            caster.start();
            ByteArrayOutputStream got = new ByteArrayOutputStream();
            CountDownLatch done = new CountDownLatch(1);
            List<String> events = new CopyOnWriteArrayList<>();
            NtripClient c = new NtripClient("127.0.0.1", server.getLocalPort(), "MOUNT", "u", "secret",
                    (b, n) -> { synchronized (got) { got.write(b, 0, n); if (got.size() >= rtcm.length) done.countDown(); } },
                    () -> null, events::add);
            c.start();
            assertTrue(done.await(5, TimeUnit.SECONDS));
            caster.join(3000);
            c.stop();
            c.join(2000);
            assertArrayEquals(rtcm, got.toByteArray());
            String req = request.toString("ISO-8859-1");
            assertTrue(req.startsWith("GET /MOUNT HTTP/1.0\r\n"));
            assertTrue(req.contains("Authorization: Basic "));
            assertFalse(req.contains("$G"));            // no GGA without a GPS
            assertEquals(0, c.ggaSent);
            assertTrue(events.contains("caster connected"));
            for (String e : events) assertFalse(e.contains("secret"));
        }
    }
}
