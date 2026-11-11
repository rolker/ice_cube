package rink.icecube.logger;

import java.io.IOException;

/** A byte link to the receiver: USB or Bluetooth SPP. */
interface GpsLink {
    /** Returns the number of bytes read, or 0 after a short timeout. Throws when the link is lost. */
    int read(byte[] b) throws IOException;

    /** Writes n bytes. Calls from different threads are serialized. */
    void write(byte[] b, int n) throws IOException;

    /** Closes the link. Safe to call more than once and from any thread; a blocked read then throws. */
    void close();

    /** Transport and device, for events.csv. */
    String describe();
}
