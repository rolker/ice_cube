"""A fake NTRIP caster on localhost for the reference check's client tests: a
sourcetable at `/`, and per-mountpoint behaviour (`ICY`, HTTP 200 with or without
chunked encoding, 401, 404, a sourcetable in reply, silence). It counts connections and
the most open at once, keeps every request it saw, and can run on a thread for a test
that runs the app in a subprocess. Synthetic data only."""
import asyncio
import base64
import threading

ENDTABLE = b"ENDSOURCETABLE\r\n"


def str_line(mountpoint, nmea="0", solution="0", misc="", identifier=None,
             fmt="RTCM 3.2", details="1005(10),1074(1),1084(1),1094(1),1124(1)",
             nav="GPS+GLO+GAL+BDS"):
    return (f"STR;{mountpoint};{identifier or mountpoint};{fmt};{details};2;{nav};NET;USA;"
            f"43.00;-71.00;{nmea};{solution};sNTRIP;none;B;N;9600;{misc}\r\n")


class FakeCaster:
    """`mountpoints` maps a name to a behaviour: "icy" (ICY 200 OK, v1), "http" (HTTP/1.1
    200 with headers), "chunked" (HTTP 200 with chunked encoding), "silent" (accepts
    and sends nothing), "slow" (never answers the request). Unknown mountpoints get a
    404 with `reply_404`, or the sourcetable when `v1_table_for_unknown`. `login`
    (user, password) is required for the stream requests when set; otherwise any.
    `data` is the bytes streamed, repeated `repeat` times with `period` seconds between
    sends (0: all at once, then the connection stays open until `hold_s` or the test
    closes it)."""

    def __init__(self, mountpoints=None, login=None, data=b"", period=0.05, repeat=1000,
                 v1_table_for_unknown=False, hold_s=60.0, table_extra="", table_style="v1"):
        self.mountpoints = mountpoints or {"MP_1": "icy"}
        self.login = login
        self.data = data
        self.period = period
        self.repeat = repeat
        self.v1_table_for_unknown = v1_table_for_unknown
        self.hold_s = hold_s
        self.table_extra = table_extra
        self.table_style = table_style    # "v1": SOURCETABLE 200 OK; "http"; "chunked"
        self.root_reply = None            # bytes to answer "/" with instead of the table
        self.chunk_size = 700
        self.requests = []          # (path, headers dict) in order
        self.open = 0
        self.most_open = 0
        self.connections = 0
        self.stream_connections = 0
        self.sourcetable_requests = 0
        self._server = None
        self._writers = set()
        self._thread = None
        self._loop = None
        self.port = None

    def sourcetable(self):
        lines = "".join(str_line(name, **(self._str_fields(name)))
                        for name in self.mountpoints)
        body = (self.table_extra + lines).encode() + ENDTABLE
        if self.table_style == "v1":
            return (b"SOURCETABLE 200 OK\r\nServer: NTRIP fake/1\r\nContent-Type: text/plain\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        head = (b"HTTP/1.1 200 OK\r\nNtrip-Version: Ntrip/2.0\r\nServer: NTRIP fake/2\r\n"
                b"Content-Type: gnss/sourcetable\r\n")
        if self.table_style == "http":
            return head + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        # Chunked, in pieces that split lines (and the ENDSOURCETABLE marker when the
        # table's length makes it so).
        size = self.chunk_size
        pieces = [body[i:i + size] for i in range(0, len(body), size)]
        chunks = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in pieces)
        return head + b"Transfer-Encoding: chunked\r\n\r\n" + chunks + b"0\r\n\r\n"

    def _str_fields(self, name):
        behaviour = self.mountpoints[name]
        return behaviour if isinstance(behaviour, dict) else {}

    def _behaviour(self, name):
        behaviour = self.mountpoints[name]
        return behaviour.get("behaviour", "icy") if isinstance(behaviour, dict) else behaviour

    async def start(self, host="127.0.0.1", port=0):
        self._server = await asyncio.start_server(self._handle, host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self._server is not None:
            self._server.close()
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()
            self._server = None

    async def drop_clients(self):
        """Close every stream connection, as a caster restart would."""
        for writer in list(self._writers):
            writer.close()
        await asyncio.sleep(0.05)

    async def _handle(self, reader, writer):
        self.connections += 1
        self.open += 1
        self.most_open = max(self.most_open, self.open)
        self._writers.add(writer)
        try:
            request = await asyncio.wait_for(self._read_request(reader), 5)
            if request is None:
                return
            path, headers = request
            self.requests.append((path, headers))
            # Every reply holds the connection until the client closes it, so `open`
            # and `most_open` count connections as the client sees them.
            if path in ("/", ""):
                self.sourcetable_requests += 1
                writer.write(self.sourcetable() if self.root_reply is None else self.root_reply)
                await writer.drain()
                await self._hold(reader)
                return
            name = path.lstrip("/")
            if name not in self.mountpoints:
                if self.v1_table_for_unknown:
                    writer.write(self.sourcetable())
                else:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                await self._hold(reader)
                return
            if self.login is not None:
                expected = base64.b64encode(f"{self.login[0]}:{self.login[1]}".encode()).decode()
                if headers.get("authorization") != f"Basic {expected}":
                    writer.write(b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic "
                                 b"realm=\"/\"\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
                    await self._hold(reader)
                    return
            behaviour = self._behaviour(name)
            if behaviour == "slow":
                await self._hold(reader)
                return
            if behaviour == "table-200":     # an NTRIP 2 caster: a table, as HTTP 200
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: gnss/sourcetable\r\n\r\n"
                             + b"STR;OTHER;OTHER;RTCM 3.2;;2;GPS;;;0;0;0;0;x;none;B;N;0;\r\n"
                             + ENDTABLE)
                await writer.drain()
                await self._hold(reader)
                return
            if behaviour == "headers-only":  # a 200 whose headers never end
                writer.write(b"HTTP/1.1 200 OK\r\nServer: slow\r\n")
                await writer.drain()
                await self._hold(reader)
                return
            if behaviour == "bare-error":    # a refusal with no line ending, then silence
                writer.write(b"ERROR - Bad Password")
                await writer.drain()
                await self._hold(reader)
                return
            self.stream_connections += 1
            chunked = behaviour == "chunked"
            if behaviour == "icy":
                writer.write(b"ICY 200 OK\r\n")
            elif behaviour == "icy-blank":   # a caster that adds a blank line, late
                writer.write(b"ICY 200 OK\r\n")
                await writer.drain()
                await asyncio.sleep(0.05)
                writer.write(b"\r\n")
            else:
                writer.write(b"HTTP/1.1 200 OK\r\nServer: NTRIP fake/1\r\n"
                             b"Content-Type: gnss/data\r\n"
                             + (b"Transfer-Encoding: chunked\r\n" if chunked else b"")
                             + b"\r\n")
            await writer.drain()
            if behaviour == "silent":
                await self._hold(reader)
                return
            for _ in range(self.repeat):
                if self.data:
                    piece = self.data
                    if chunked:
                        piece = f"{len(piece):x}\r\n".encode() + piece + b"\r\n"
                    writer.write(piece)
                    await writer.drain()
                if self.period:
                    await asyncio.sleep(self.period)
                else:
                    break
            if chunked:
                writer.write(b"0\r\n\r\n")
                await writer.drain()
            await self._hold(reader)
        except (asyncio.TimeoutError, ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            self.open -= 1
            self._writers.discard(writer)
            writer.close()

    async def _hold(self, reader):
        """Keep the connection until the client closes it (or hold_s), so `open`
        counts real connections."""
        try:
            await asyncio.wait_for(reader.read(1), self.hold_s)
        except asyncio.TimeoutError:
            pass

    async def _read_request(self, reader):
        lines = []
        while True:
            line = await reader.readline()
            if not line:
                return None
            line = line.decode("latin-1").rstrip("\r\n")
            if not line:
                break
            lines.append(line)
        parts = lines[0].split()
        headers = {}
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if colon:
                headers[name.strip().lower()] = value.strip()
        return parts[1] if len(parts) > 1 else "", headers

    # ---- on a thread, for a test that runs the app as a subprocess ----

    def run_in_thread(self):
        """Start on a private event loop in a daemon thread; returns the port."""
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.start())
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        ready.wait(5)
        return self.port

    def stop_thread(self):
        loop = self._loop
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.stop(), loop)
        future.result(5)
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(5)
