package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

import org.junit.Test;

public class SourcetableTest {
    static final String TABLE =
            "CAS;caster.example.invalid;2101;Example;None;0;XXX;0.00;0.00;http://example.invalid\r\n"
            + "STR;OTHER;Other;RTCM 3.2;1005(10);2;GPS;NET;XXX;0.00;0.00;0;0;Gen;none;B;N;0;NAD83(2011) epoch 2010.0\r\n"
            + "STR;TEST01;Test;RTCM 3.3;1005(10),1077(1);2;GPS+GLO;NET;XXX;0.10;0.20;1;0;Gen;none;B;N;9600;ITRF2020 epoch 2026.85\r\n"
            + "STR;SEMI;Semi;RTCM 3.3;1005;2;GPS;NET;XXX;0.10;0.20;1;0;Gen;none;B;N;9600;ITRF2014; note\r\n"
            + "ENDSOURCETABLE\r\n";

    @Test public void miscOfMount() {
        assertEquals("ITRF2020 epoch 2026.85", Sourcetable.misc(TABLE, "TEST01"));
        assertEquals("NAD83(2011) epoch 2010.0", Sourcetable.misc(TABLE, "OTHER"));
        assertEquals("ITRF2014; note", Sourcetable.misc(TABLE, "SEMI"));
        assertNull(Sourcetable.misc(TABLE, "MISSING"));
        assertNull(Sourcetable.misc(TABLE, "test01"));
    }

    @Test public void bodyHandlesV1AndChunkedV2() throws IOException {
        assertTrue(Sourcetable.body("SOURCETABLE 200 OK\r\nServer: x\r\n\r\n" + TABLE).startsWith("CAS;"));
        String chunked = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + Integer.toHexString(10) + "\r\n" + TABLE.substring(0, 10) + "\r\n"
                + Integer.toHexString(TABLE.length() - 10) + "\r\n" + TABLE.substring(10) + "\r\n0\r\n\r\n";
        assertEquals(TABLE, Sourcetable.body(chunked));
        try { Sourcetable.body("HTTP/1.0 401 Unauthorized\r\n\r\n"); fail(); } catch (IOException expected) { }
    }

    @Test public void fetchesFromCaster() throws Exception {
        try (ServerSocket server = new ServerSocket(0)) {
            StringBuilder request = new StringBuilder();
            Thread caster = new Thread(() -> {
                try (Socket s = server.accept()) {
                    InputStream in = s.getInputStream();
                    byte[] b = new byte[4096];
                    int n = in.read(b);
                    request.append(new String(b, 0, n, StandardCharsets.ISO_8859_1));
                    OutputStream out = s.getOutputStream();
                    out.write(("SOURCETABLE 200 OK\r\nContent-Type: text/plain\r\n\r\n" + TABLE).getBytes(StandardCharsets.ISO_8859_1));
                    out.flush();
                } catch (IOException ignored) { }
            });
            caster.start();
            String t = Sourcetable.fetch("127.0.0.1", server.getLocalPort(), "u", "p", 3000);
            caster.join(3000);
            assertTrue(request.toString().startsWith("GET / HTTP/1.0\r\n"));
            assertTrue(request.toString().contains("Ntrip-Version: Ntrip/1.0"));
            assertEquals("ITRF2020", ReferenceFrame.parse(Sourcetable.misc(t, "TEST01")).name);
        }
    }
}
