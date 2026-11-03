"""Read the receiver in a thread. This module never writes to the serial port.

Opening the port leaves DTR and RTS as the kernel sets them on open, the same as
reading the port with `cat`. Changing them could toggle a line that is wired to
a reset pin on some boards.
"""
import collections
import threading

import serial

READ_SIZE = 4096
RETRY_S = 5


class ChunkQueue:
    """Bounded hand-off from the serial thread to the event loop.

    The thread never blocks: if the loop falls behind by more than `limit`
    bytes, new data is dropped and counted rather than growing without bound.
    """

    def __init__(self, loop, consume, limit=8 * 1024 * 1024):
        self._loop = loop
        self._consume = consume
        self._limit = limit
        self._lock = threading.Lock()
        self._chunks = collections.deque()
        self._size = 0
        self._scheduled = False
        self._closed = False
        self.dropped_bytes = 0

    def put(self, data):
        with self._lock:
            if self._closed:
                return
            if self._size + len(data) > self._limit:
                self.dropped_bytes += len(data)
                return
            self._chunks.append(data)
            self._size += len(data)
            if self._scheduled:
                return
            self._scheduled = True
        self._loop.call_soon_threadsafe(self.drain)

    def drain(self):
        with self._lock:
            chunks, self._chunks = self._chunks, collections.deque()
            self._size = 0
            self._scheduled = False
            if self._closed:
                return
        for chunk in chunks:
            self._consume(chunk)

    def close(self):
        """Stop accepting and delivering data, so nothing reaches a closed log."""
        with self._lock:
            self._closed = True
            self._chunks.clear()
            self._size = 0


class SerialReader:
    """`check` is called before every open. It returns "" if the port may be
    opened, otherwise why not; a refused port is reported and never opened."""

    def __init__(self, port, baud, on_data, on_state, check):
        self._port = port
        self._check = check
        self._baud = baud
        self._on_data = on_data
        self._on_state = on_state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            try:
                problem = self._check()
                if problem:
                    self._on_state(False, problem)
                    self._stop.wait(RETRY_S)
                    continue
                with serial.Serial(self._port, self._baud, timeout=0.2, exclusive=True) as port:
                    self._on_state(True, "")
                    while not self._stop.is_set():
                        data = port.read(min(port.in_waiting, READ_SIZE) or 1)
                        if data:
                            self._on_data(data)
            except Exception as error:  # any failure: report it, then reopen
                self._on_state(False, f"{type(error).__name__}: {error}")
            self._stop.wait(RETRY_S)
