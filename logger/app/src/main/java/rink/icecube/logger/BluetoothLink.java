package rink.icecube.logger;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.UUID;

/**
 * Bluetooth Classic SPP link to an already-paired receiver (the RTK Express's ESP32).
 * The caller must hold BLUETOOTH_CONNECT on Android 12 and later.
 */
final class BluetoothLink implements GpsLink {
    static final UUID SPP = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");

    private final BluetoothSocket socket;
    private final InputStream in;
    private final OutputStream out;
    private final String description;

    private BluetoothLink(BluetoothSocket s, String description) throws IOException {
        socket = s;
        in = s.getInputStream();
        out = s.getOutputStream();
        this.description = description;
    }

    /** Connects with a secure RFCOMM socket, falling back to an insecure one. Blocks for several seconds on failure. */
    @SuppressLint("MissingPermission")
    static BluetoothLink open(BluetoothDevice d) throws IOException {
        String name = d.getName();
        String who = "bluetooth " + (name == null ? "?" : name.replace(',', ';')) + " " + d.getAddress();
        IOException first;
        BluetoothSocket s = null;
        try {
            s = d.createRfcommSocketToServiceRecord(SPP);
            s.connect();
            return new BluetoothLink(s, who + " secure");
        } catch (IOException e) {
            first = e;
            closeQuietly(s);
        }
        s = null;
        try {
            s = d.createInsecureRfcommSocketToServiceRecord(SPP);
            s.connect();
            return new BluetoothLink(s, who + " insecure");
        } catch (IOException e) {
            closeQuietly(s);
            throw new IOException("Bluetooth connect failed (" + first.getMessage() + "; insecure: " + e.getMessage() + ")");
        }
    }

    private static void closeQuietly(BluetoothSocket s) {
        if (s != null) { try { s.close(); } catch (IOException ignored) { } }
    }

    @Override public int read(byte[] b) throws IOException {
        int n = in.read(b);
        if (n < 0) throw new IOException("Bluetooth stream closed");
        return n;
    }

    @Override public synchronized void write(byte[] b, int n) throws IOException {
        out.write(b, 0, n);
        out.flush();
    }

    @Override public void close() { closeQuietly(socket); }

    @Override public String describe() { return description; }
}
