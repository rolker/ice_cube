package rink.icecube.logger;

import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;

import java.io.IOException;

/** Direct USB CDC-ACM link to the ZED-F9P's own USB port. */
final class UsbLink implements GpsLink {
    private final UsbDeviceConnection conn;
    private final UsbInterface data;
    private final UsbInterface ctrl;
    private final UsbEndpoint in, out;
    private final UsbManager mgr;
    private final String name, description;
    private boolean closed;

    private UsbLink(UsbManager mgr, UsbDevice d, UsbDeviceConnection c, UsbInterface data, UsbInterface ctrl,
                    UsbEndpoint in, UsbEndpoint out) {
        this.mgr = mgr; this.conn = c; this.data = data; this.ctrl = ctrl; this.in = in; this.out = out;
        name = d.getDeviceName();
        description = String.format("usb %04x:%04x %s", d.getVendorId(), d.getProductId(), name);
    }

    /** The u-blox device if attached, else any CDC serial device, else null. */
    static UsbDevice findGps(UsbManager m) {
        UsbDevice any = null;
        for (UsbDevice d : m.getDeviceList().values()) {
            if (d.getVendorId() == 0x1546) return d;
            if (isCdc(d)) any = d;
        }
        return any;
    }

    /** True if the device exposes a CDC data interface with bulk endpoints. */
    static boolean isCdc(UsbDevice d) {
        for (int i = 0; i < d.getInterfaceCount(); i++) {
            if (d.getInterface(i).getInterfaceClass() == UsbConstants.USB_CLASS_CDC_DATA) return true;
        }
        return false;
    }

    static UsbLink open(UsbManager mgr, UsbDevice d) throws IOException {
        UsbInterface dataIf = null, ctrlIf = null;
        UsbEndpoint in = null, out = null;
        for (int i = 0; i < d.getInterfaceCount(); i++) {
            UsbInterface f = d.getInterface(i);
            if (f.getInterfaceClass() == UsbConstants.USB_CLASS_COMM && ctrlIf == null) ctrlIf = f;
            if (f.getInterfaceClass() == UsbConstants.USB_CLASS_CDC_DATA && dataIf == null) {
                UsbEndpoint a = null, b = null;
                for (int e = 0; e < f.getEndpointCount(); e++) {
                    UsbEndpoint ep = f.getEndpoint(e);
                    if (ep.getType() != UsbConstants.USB_ENDPOINT_XFER_BULK) continue;
                    if (ep.getDirection() == UsbConstants.USB_DIR_IN) a = ep; else b = ep;
                }
                if (a != null && b != null) { dataIf = f; in = a; out = b; }
            }
        }
        if (dataIf == null) {
            throw new IOException(String.format("no CDC serial interface on USB device %04x:%04x. Is this the GPS port?",
                    d.getVendorId(), d.getProductId()));
        }
        UsbDeviceConnection c = mgr.openDevice(d);
        if (c == null) throw new IOException("could not open the USB device");
        if (ctrlIf != null) c.claimInterface(ctrlIf, true);
        if (!c.claimInterface(dataIf, true)) { c.close(); throw new IOException("could not claim the USB interface"); }
        int idx = ctrlIf != null ? ctrlIf.getId() : 0;
        byte[] line = {0x00, (byte) 0xC2, 0x01, 0x00, 0, 0, 8};     // 115200 8N1, ignored by native USB
        c.controlTransfer(0x21, 0x20, 0, idx, line, line.length, 1000);   // SET_LINE_CODING
        c.controlTransfer(0x21, 0x22, 0x03, idx, null, 0, 1000);          // SET_CONTROL_LINE_STATE, DTR and RTS
        return new UsbLink(mgr, d, c, dataIf, ctrlIf, in, out);
    }

    /** Returns the byte count read, or 0 after a 200 ms timeout. Throws once the device is detached. */
    @Override public int read(byte[] b) throws IOException {
        int n = conn.bulkTransfer(in, b, b.length, 200);
        if (n > 0) return n;
        if (!mgr.getDeviceList().containsKey(name)) throw new IOException("USB device detached");
        return 0;
    }

    @Override public synchronized void write(byte[] b, int n) throws IOException {
        int off = 0;
        byte[] chunk = b;
        while (off < n) {
            if (off > 0) {
                chunk = new byte[n - off];
                System.arraycopy(b, off, chunk, 0, n - off);
            }
            int w = conn.bulkTransfer(out, chunk, n - off, 1000);
            if (w <= 0) throw new IOException("USB write failed");
            off += w;
        }
    }

    @Override public String describe() { return description; }

    @Override public synchronized void close() {
        if (closed) return;
        closed = true;
        try {
            conn.releaseInterface(data);
            if (ctrl != null) conn.releaseInterface(ctrl);
        } finally {
            conn.close();
        }
    }
}
