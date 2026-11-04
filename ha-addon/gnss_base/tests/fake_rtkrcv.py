#!/usr/bin/env python3
"""A stand-in for RTKLIB's rtkrcv, for the runner tests. Same command line:
`fake_rtkrcv.py -s -o CONF -d TTY`. Like rtkrcv 2.4.3 b34 it refuses to run unless TTY is
a terminal, prints a console banner there, reads its streams from CONF (inpstr1-path,
outstr1-path, both tcpcli), and writes `rtkrcv.nav` in its working folder on SIGTERM.
It sends a `%` header and then made-up llh solution lines near 0/0.

Environment:
  FAKE_RTKRCV_MODE    normal (default), exit (exits at once with code 3), hang (connects,
                      sends no solution), ignore-term (ignores SIGTERM), garbage (junk lines
                      between the solutions)
  FAKE_RTKRCV_PERIOD  seconds between solutions (default 0.1)
  FAKE_RTKRCV_EXIT_AFTER  exit with code 4 after this many seconds (default: never)
  FAKE_RTKRCV_RECORD  a file to append one JSON line to at start (argv, cwd, tty, config)
                      and one at exit (feed bytes received)
  FAKE_RTKRCV_RECORD_SENT  1: also one line per solution sent (its time)
"""
import json
import os
import signal
import socket
import sys
import threading
import time

MODE = os.environ.get("FAKE_RTKRCV_MODE", "normal")
PERIOD = float(os.environ.get("FAKE_RTKRCV_PERIOD", "0.1"))
RECORD = os.environ.get("FAKE_RTKRCV_RECORD")
RECORD_SENT = os.environ.get("FAKE_RTKRCV_RECORD_SENT") == "1"
EXIT_AFTER = float(os.environ.get("FAKE_RTKRCV_EXIT_AFTER", "0")) or None
received = 0


def record(**fields):
    if RECORD:
        with open(RECORD, "a") as stream:
            stream.write(json.dumps(fields) + "\n")


def option(conf, key):
    for line in conf.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.split("#")[0].strip()
    return None


def connect(path):
    host, port = path.rsplit(":", 1)
    for _ in range(100):
        try:
            return socket.create_connection((host, int(port)), timeout=5)
        except OSError:
            time.sleep(0.05)
    sys.exit("could not connect to " + path)


def read_feed(sock):
    global received
    while True:
        try:
            data = sock.recv(65536)
        except OSError:
            return
        if not data:
            return
        received += len(data)


def stop(*_):
    with open("rtkrcv.nav", "w") as stream:
        stream.write("fake navigation data\n")
    record(event="exit", received=received)
    os._exit(0)


def main():
    args = sys.argv[1:]
    if "-s" not in args or "-o" not in args or "-d" not in args:
        sys.exit("usage: fake_rtkrcv.py -s -o CONF -d TTY")
    conf_path, tty = args[args.index("-o") + 1], args[args.index("-d") + 1]
    try:
        fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
        if not os.isatty(fd):
            raise OSError("not a terminal")
    except OSError:
        print(f"console open error dev={tty}", flush=True)
        sys.exit(255)
    with open(conf_path) as stream:
        conf = stream.read()
    record(event="start", argv=sys.argv[1:], cwd=os.getcwd(), tty=os.isatty(fd),
           cache=os.path.exists("rtkrcv.nav"), pid=os.getpid(),
           stdin_tty=os.isatty(0), config=conf)
    os.write(fd, b"\r\n\x1b[1m** rtkrcv ver.2.4.3 b34 console (h:help) **\x1b[0m\r\nrtkrcv> ")
    if not os.path.exists("rtkrcv.nav"):
        print("no navigation data: rtkrcv.nav", flush=True)
    signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == "ignore-term" else stop)
    if MODE == "exit":
        print("fake rtkrcv: exiting early", flush=True)
        sys.exit(3)
    feed = connect(option(conf, "inpstr1-path"))
    threading.Thread(target=read_feed, args=(feed,), daemon=True).start()
    out = connect(option(conf, "outstr1-path"))
    out.sendall(b"% (lat/lon/height=WGS84/ellipsoidal,Q=1:fix,5:single,ns=# of satellites)\n")
    second = 0
    started = time.monotonic()
    while True:
        time.sleep(PERIOD)
        if EXIT_AFTER is not None and time.monotonic() - started >= EXIT_AFTER:
            record(event="exit", received=received)
            os._exit(4)
        if MODE == "hang":
            continue
        second += 1
        hms = time.strftime("%H:%M:%S", time.gmtime(second))
        line = (f"2026/01/01 {hms}.000   0.000001000    0.000002000     3.0000   5  20"
                f"   1.2000   0.9000   2.5000   0.1000   0.2000   0.3000   0.00    0.0\n")
        if MODE == "garbage":
            line = "\x00junk \xff line\n" + "z" * 900 + "\n" + line
        try:
            out.sendall(line.encode("latin-1"))
            if RECORD_SENT:
                record(event="sent", time=hms)
        except OSError:
            out = connect(option(conf, "outstr1-path"))


if __name__ == "__main__":
    main()
