"""Headless tests for the stdlib D-Bus endpoint.

Covers the marshalling layer, message framing, and a full two-endpoint
loopback through a fake session-bus daemon (socketpairs): SASL,
``Hello``, name ownership, method calls, error replies and signals.

Run with:  python3 tests/test_dbus_min.py
"""

import os
import socket
import struct
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core import dbus_min as dm  # noqa: E402


# ---------------------------------------------------------------------------
# Marshalling
# ---------------------------------------------------------------------------

class TestMarshalling(unittest.TestCase):
    def test_basic_round_trips(self):
        cases = [
            ("y", 255),
            ("b", True),
            ("b", False),
            ("n", -2),
            ("q", 65535),
            ("i", -1),
            ("u", 4294967295),
            ("x", -123456789),
            ("t", 7),
            ("d", 1.5),
            ("s", "hello"),
            ("o", "/path/obj"),
            ("g", "(sv)"),
            ("ay", b"\x00\x01\x02"),
        ]
        for sig, value in cases:
            self.assertEqual(dm.decode_body(sig, dm.encode_body(sig, value)),
                             value, sig)

    def test_container_round_trips(self):
        cases = [
            ("(iiay)", (1, 2, b"\x01\x02")),
            ("as", ["a", "bb", "ccc"]),
            ("a(iiay)", [(10, 20, b"\xaa"), (30, 40, b"\xbb")]),
            ("a{sv}", {"k1": ("s", "v1"), "k2": ("b", True)}),
            ("v", ("s", "x")),
            ("a{ss}", {"a": "b", "c": "d"}),
            ("(bs)", (False, "txt")),
            # a(v) is an array of one-field structs, so every element is
            # a 1-tuple wrapping its variant.
            ("a(v)", [(("s", "x"),), (("i", 5),)]),
        ]
        for sig, value in cases:
            self.assertEqual(dm.decode_body(sig, dm.encode_body(sig, value)),
                             value, sig)

    def test_exact_wire_bytes(self):
        self.assertEqual(dm.encode_body("y", 1), b"\x01")
        self.assertEqual(dm.encode_body("b", True), b"\x00\x00\x00\x01")
        self.assertEqual(dm.encode_body("s", "hi"), b"\x00\x00\x00\x02hi\x00")
        self.assertEqual(dm.encode_body("as", []), b"\x00\x00\x00\x00")
        self.assertEqual(
            dm.encode_body("v", ("s", "x")), b"\x01s\x00\x00\x00\x00\x00\x01x\x00"
        )
        # The header-field struct (yv): code byte + variant.
        self.assertEqual(
            dm.encode_body("(yv)", (1, ("s", "x"))),
            b"\x01\x01s\x00\x00\x00\x00\x01x\x00",
        )

    def test_string_with_nul_rejected(self):
        with self.assertRaises(ValueError):
            dm.encode_body("s", "a\x00b")

    def test_unknown_type_rejected(self):
        with self.assertRaises(ValueError):
            dm.encode_body("z", 1)


# ---------------------------------------------------------------------------
# Message framing
# ---------------------------------------------------------------------------

def _sample_call() -> bytes:
    return dm.build_message(
        dm.METHOD_CALL,
        42,
        path="/StatusNotifierItem",
        interface="org.kde.StatusNotifierItem",
        member="Activate",
        dest=":1.7",
        body_sig="(ii)",
        body=dm.encode_body("(ii)", (1, 2)),
    )


def _sample_call_le() -> bytes:
    """The same call, marshalled genuinely little-endian by hand.

    Byte-swapping a big-endian message in place would also scramble the
    UTF-8 strings, so a real sender's layout is built explicitly here.
    """
    def elem(code: int, ftype: str, value: str) -> bytes:
        out = bytearray()
        out.append(code)
        out.append(len(ftype))
        out.extend(ftype.encode())
        out.append(0)
        if ftype in "so":
            data = value.encode()
            out.extend(struct.pack("<I", len(data)))
            out.extend(data)
            out.append(0)
        elif ftype == "g":
            out.append(len(value))
            out.extend(value.encode())
            out.append(0)
        while len(out) % 8:
            out.append(0)
        return bytes(out)

    fields = (
        elem(1, "o", "/StatusNotifierItem")
        + elem(2, "s", "org.kde.StatusNotifierItem")
        + elem(3, "s", "Activate")
        + elem(6, "s", ":1.7")
        + elem(8, "g", "ii")
    )
    body = struct.pack("<ii", 1, 2)
    hdr = bytearray()
    hdr.append(0x6C)
    hdr.append(dm.METHOD_CALL)
    hdr.append(0)
    hdr.append(dm.PROTOCOL_VERSION)
    hdr.extend(struct.pack("<I", len(body)))
    hdr.extend(struct.pack("<I", 42))
    hdr.extend(struct.pack("<I", len(fields)))
    hdr.extend(b"\x00" * ((-len(hdr)) % 8))
    hdr.extend(fields)
    hdr.extend(b"\x00" * ((-len(hdr)) % 8))
    return bytes(hdr) + body


class TestFraming(unittest.TestCase):
    def test_round_trip_all_fields(self):
        msg = _sample_call()
        parsed = dm.parse_message(msg)
        self.assertEqual(parsed["type"], dm.METHOD_CALL)
        self.assertEqual(parsed["serial"], 42)
        self.assertEqual(parsed["path"], "/StatusNotifierItem")
        self.assertEqual(parsed["interface"], "org.kde.StatusNotifierItem")
        self.assertEqual(parsed["member"], "Activate")
        self.assertEqual(parsed["dest"], ":1.7")
        # The SIGNATURE header field carries the bare argument list;
        # the tuple form exists only in our internal body encoding.
        self.assertEqual(parsed["body_sig"], "ii")
        self.assertEqual(dm.decode_body("(ii)", parsed["body"]), (1, 2))

    def test_little_endian_normalisation(self):
        msg = _sample_call_le()
        parsed = dm.parse_message(msg)
        self.assertEqual(parsed["member"], "Activate")
        self.assertEqual(parsed["dest"], ":1.7")
        self.assertEqual(parsed["body_sig"], "ii")
        self.assertEqual(
            dm.decode_body("(ii)", parsed["body"], parsed["fmt"]), (1, 2)
        )

    def test_error_message(self):
        raw = dm.build_message(
            dm.ERROR,
            7,
            error_name="org.example.Error.Nope",
            reply_serial=99,
            body_sig="(s)",
            body=dm.encode_body("(s)", ("detail",)),
        )
        parsed = dm.parse_message(raw)
        self.assertEqual(parsed["type"], dm.ERROR)
        self.assertEqual(parsed["error_name"], "org.example.Error.Nope")
        self.assertEqual(parsed["reply_serial"], 99)
        self.assertEqual(dm.decode_body("(s)", parsed["body"]), ("detail",))

    def test_signal_and_empty_reply(self):
        sig = dm.build_message(
            dm.SIGNAL, 8, path="/obj", interface="org.x.I", member="Changed",
            body_sig="s", body=dm.encode_body("(s)", ("hi",)),
        )
        parsed = dm.parse_message(sig)
        self.assertEqual(parsed["type"], dm.SIGNAL)
        self.assertEqual(parsed["member"], "Changed")

        reply = dm.build_message(dm.METHOD_RETURN, 3, reply_serial=42)
        parsed = dm.parse_message(reply)
        self.assertEqual(parsed["reply_serial"], 42)
        self.assertEqual(parsed["body_sig"], "")
        self.assertEqual(parsed["body"], b"")

    def test_find_header_end_partial(self):
        msg = _sample_call()
        full = dm._find_header_end(msg)
        arr_len = struct.unpack_from(">I", msg, 12)[0]
        header_end = dm._align(16 + arr_len, 8)
        for n in range(1, header_end):
            with self.assertRaises(dm._NeedMore, msg=f"n={n}"):
                dm._find_header_end(msg[:n])
        for n in range(header_end, len(msg) + 1):
            self.assertEqual(dm._find_header_end(msg[:n]), full)

    def test_read_message_chunked(self):
        msg = _sample_call()
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            for i in range(0, len(msg), 7):
                a.sendall(msg[i:i + 7])
            got = dm.read_message(b)
            self.assertIsNotNone(got)
            self.assertEqual(got["member"], "Activate")
        finally:
            a.close()
            b.close()

    def test_read_message_eof(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.close()
        self.assertIsNone(dm.read_message(b))
        b.close()


# ---------------------------------------------------------------------------
# Fake bus daemon + endpoint loopback
# ---------------------------------------------------------------------------

class FakeBusDaemon:
    """A just-enough D-Bus session-bus daemon over socketpairs.

    Speaks the modern SASL handshake exactly like dbus-broker >= 36
    (client-first: the daemon stays silent until the client's NUL-prefixed
    ``AUTH EXTERNAL`` arrives; CRLF-terminated lines; a ``DATA`` challenge
    when no uid is passed), answers ``Hello`` / ``RequestName`` /
    ``ReleaseName`` / ``GetNameOwner`` on the ``org.freedesktop.DBus``
    interface, and forwards everything else (method calls, replies,
    signals) between the connected endpoints by unique or well-known name.
    """

    #: The 32-hex-char GUID a real broker appends to the ``OK`` line.
    _FAKE_GUID = b"0123456789abcdef0123456789abcdef"

    def __init__(self, n: int = 2) -> None:
        self._states: dict = {}
        self._daemon_socks = []
        self._client_socks = []
        self._sock_by_unique = {}
        self._unique_by_sock = {}
        self._name_owner = {}
        self._caller = {}
        self._unique_counter = 0
        self._serial = 0
        self._lock = threading.Lock()
        for _ in range(n):
            daemon_side, client_side = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_STREAM
            )
            self._states[daemon_side] = "wait-auth"
            self._daemon_socks.append(daemon_side)
            self._client_socks.append(client_side)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def take_client_socket(self) -> socket.socket:
        with self._lock:
            return self._client_socks.pop(0)

    def stop(self) -> None:
        for sock in list(self._daemon_socks):
            try:
                sock.close()
            except OSError:
                pass

    # -- daemon side -----------------------------------------------------
    def _run(self) -> None:
        import select

        # Like the real broker, the daemon sends nothing until the client
        # speaks first (the NUL-prefixed AUTH line), so there is no up-front
        # offer to race with the endpoint's connect().
        socks = list(self._daemon_socks)
        while socks:
            try:
                ready, _, _ = select.select(socks, [], [], 0.2)
            except OSError:
                break
            for sock in ready:
                state = self._states.get(sock)
                if state == "wait-auth":
                    line = self._read_line(sock)
                    # First byte of the stream must be the NUL prefix.
                    if not line or line[0:1] != b"\x00":
                        self._drop(sock, socks)
                        continue
                    parts = line[1:].split(b" ", 1)
                    if len(parts) != 2 or parts[0] != b"AUTH":
                        self._drop(sock, socks)
                        continue
                    mech = parts[1].split()
                    if not mech or mech[0] != b"EXTERNAL":
                        self._drop(sock, socks)
                        continue
                    if len(mech) == 2:
                        # uid passed: must be the hex of the peer's DECIMAL
                        # uid string (spec: uid 1000 -> "31303030").
                        expected = (
                            str(os.getuid()).encode("ascii").hex().encode()
                        )
                        if mech[1] != expected:
                            sock.sendall(b"REJECTED EXTERNAL\r\n")
                            self._drop(sock, socks)
                            continue
                        sock.sendall(b"OK " + self._FAKE_GUID + b"\r\n")
                        self._states[sock] = "wait-begin"
                    else:
                        # No uid: challenge, exactly like the real broker.
                        sock.sendall(b"DATA\r\n")
                        self._states[sock] = "wait-data"
                elif state == "wait-data":
                    line = self._read_line(sock)
                    if line is None:
                        self._drop(sock, socks)
                        continue
                    if line == b"DATA":
                        sock.sendall(b"OK " + self._FAKE_GUID + b"\r\n")
                        self._states[sock] = "wait-begin"
                    elif line in (b"CANCEL", b"ERROR"):
                        sock.sendall(b"REJECTED EXTERNAL\r\n")
                        self._states[sock] = "wait-auth"
                    else:
                        self._drop(sock, socks)
                elif state == "wait-begin":
                    line = self._read_line(sock)
                    if line is None:
                        self._drop(sock, socks)
                        continue
                    if line == b"BEGIN":
                        self._states[sock] = "ready"
                    elif line == b"NEGOTIATE_UNIX_FD":
                        sock.sendall(b"AGREE_UNIX_FD\r\n")
                    elif line in (b"CANCEL", b"ERROR"):
                        sock.sendall(b"REJECTED EXTERNAL\r\n")
                        self._states[sock] = "wait-auth"
                    else:
                        self._drop(sock, socks)
                elif state == "ready":
                    raw = self._read_raw(sock)
                    if raw is None:
                        self._drop(sock, socks)
                        continue
                    try:
                        self._route(sock, raw)
                    except Exception:
                        pass

    @staticmethod
    def _read_line(sock: socket.socket):
        """One CRLF-terminated line (a bare LF is tolerated); None on EOF."""
        buf = b""
        prev = b""
        while True:
            try:
                ch = sock.recv(1)
            except OSError:
                return None
            if not ch:
                return None
            if ch == b"\n":
                if prev == b"\r":
                    buf = buf[:-1]
                return buf
            buf += ch
            prev = ch
            if len(buf) > 4096:
                return None

    @staticmethod
    def _read_raw(sock: socket.socket):
        buf = b""
        while True:
            if len(buf) < 16:
                try:
                    chunk = sock.recv(16 - len(buf))
                except OSError:
                    return None
                if not chunk:
                    return None
                buf += chunk
                continue
            if buf[0] not in (0x42, 0x6C):
                return None
            fmt = ">I" if buf[0] == 0x42 else "<I"
            (body_len,) = struct.unpack_from(fmt, buf, 4)
            try:
                header_end = dm._find_header_end(buf)
            except dm._NeedMore:
                pass
            except ValueError:
                return None
            else:
                if header_end + body_len <= len(buf):
                    return bytes(buf[: header_end + body_len])
            try:
                chunk = sock.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
            if len(buf) > (1 << 20):
                return None

    def _drop(self, sock: socket.socket, socks: list) -> None:
        try:
            sock.close()
        except OSError:
            pass
        with self._lock:
            self._sock_by_unique = {
                u: s for u, s in self._sock_by_unique.items() if s is not sock
            }
            self._unique_by_sock.pop(sock, None)
        self._states.pop(sock, None)
        if sock in socks:
            socks.remove(sock)

    def _next_serial(self) -> int:
        with self._lock:
            self._serial += 1
            return self._serial

    def _reply(self, from_sock: socket.socket, req: dict,
               sig: str, value) -> None:
        body = dm.encode_body(sig, value) if sig else b""
        raw = dm.build_message(
            dm.METHOD_RETURN,
            self._next_serial(),
            reply_serial=req["serial"],
            dest=self._unique_by_sock.get(from_sock, ""),
            body_sig=sig,
            body=body,
        )
        try:
            from_sock.sendall(raw)
        except OSError:
            pass

    def _reply_error(self, from_sock: socket.socket, req: dict,
                     name: str, text: str) -> None:
        raw = dm.build_message(
            dm.ERROR,
            self._next_serial(),
            reply_serial=req["serial"],
            error_name=name,
            body_sig="(s)",
            body=dm.encode_body("(s)", (text,)),
        )
        try:
            from_sock.sendall(raw)
        except OSError:
            pass

    def _handle_dbus_api(self, from_sock: socket.socket, msg: dict) -> None:
        member = msg["member"]
        if member == "Hello":
            with self._lock:
                self._unique_counter += 1
                unique = f":1.{self._unique_counter}"
                self._sock_by_unique[unique] = from_sock
                self._unique_by_sock[from_sock] = unique
            self._reply(from_sock, msg, "(s)", (unique,))
        elif member == "RequestName":
            (name, _flags) = dm.decode_body("(su)", msg["body"])
            with self._lock:
                if name not in self._name_owner:
                    self._name_owner[name] = self._unique_by_sock[from_sock]
                    status = 1  # PRIMARY_OWNER
                else:
                    status = 5  # EXISTING_OWNER
            self._reply(from_sock, msg, "(u)", (status,))
        elif member == "ReleaseName":
            (name,) = dm.decode_body("(s)", msg["body"])
            with self._lock:
                if self._name_owner.get(name) == self._unique_by_sock[from_sock]:
                    del self._name_owner[name]
                    status = 1  # RELEASED
                else:
                    status = 3  # NOT_OWNER
            self._reply(from_sock, msg, "(u)", (status,))
        elif member == "GetNameOwner":
            (name,) = dm.decode_body("(s)", msg["body"])
            with self._lock:
                owner = self._name_owner.get(name, "")
            if owner:
                self._reply(from_sock, msg, "(s)", (owner,))
            else:
                self._reply_error(
                    from_sock, msg,
                    "org.freedesktop.DBus.Error.NameHasNoOwner", name,
                )
        else:
            self._reply_error(
                from_sock, msg,
                "org.freedesktop.DBus.Error.UnknownMethod", member,
            )

    def _route(self, from_sock: socket.socket, raw: bytes) -> None:
        msg = dm.parse_message(raw)
        mtype = msg["type"]
        if mtype == dm.METHOD_CALL:
            if msg["dest"] == "org.freedesktop.DBus":
                self._handle_dbus_api(from_sock, msg)
                return
            self._caller[msg["serial"]] = from_sock
            target = self._target_for(msg["dest"])
            if target is not None and target is not from_sock:
                target.sendall(raw)
            # Unknown dest: dropped, like a real bus would error — the
            # tests never rely on that path.
            return
        if mtype in (dm.METHOD_RETURN, dm.ERROR):
            target = self._caller.pop(msg["reply_serial"], None)
            if target is not None:
                target.sendall(raw)
            return
        if mtype == dm.SIGNAL:
            with self._lock:
                targets = [
                    s for s in self._sock_by_unique.values()
                    if s is not from_sock
                ]
            for sock in targets:
                try:
                    sock.sendall(raw)
                except OSError:
                    pass

    def _target_for(self, dest: str):
        with self._lock:
            if dest.startswith(":"):
                return self._sock_by_unique.get(dest)
            owner = self._name_owner.get(dest)
            if owner is None:
                return None
            return self._sock_by_unique.get(owner)


class _WaitMixin:
    @staticmethod
    def wait_for(cond, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return
            time.sleep(0.02)
        raise AssertionError("condition not met within %.1fs" % timeout)


class TestEndpointLoopback(_WaitMixin, unittest.TestCase):
    """Two real endpoints talking through the fake daemon."""

    def setUp(self):
        self.daemon = FakeBusDaemon()
        self.a = dm.DbusEndpoint()
        self.a.connect(sock=self.daemon.take_client_socket(), timeout=5.0)
        self.b = dm.DbusEndpoint()
        self.b.connect(sock=self.daemon.take_client_socket(), timeout=5.0)

    def tearDown(self):
        for ep in (self.a, self.b):
            try:
                ep.close()
            except Exception:
                pass
        self.daemon.stop()

    def test_hello_and_names(self):
        self.assertTrue(self.a.unique_name.startswith(":1."))
        self.assertTrue(self.b.unique_name.startswith(":1."))
        self.assertNotEqual(self.a.unique_name, self.b.unique_name)
        self.assertTrue(self.b.request_name("org.test.Watcher"))
        self.assertFalse(self.a.request_name("org.test.Watcher"))

    def test_method_call_and_reply(self):
        seen = []

        def dispatch(member, args):
            if member == "Ping":
                seen.append(args)
                return "s", "pong"
            return None

        self.a.handle("/obj", "org.test.Iface", dispatch, {"Ping": "s"})
        result = self.b.call(
            self.a.unique_name, "/obj", "org.test.Iface", "Ping", "s",
            ("hi",), timeout=5.0,
        )
        self.assertEqual(result, ("pong",))
        self.wait_for(lambda: seen == [("hi",)])

    def test_no_such_method(self):
        self.a.handle("/obj", "org.test.Iface", lambda m, a: None, {})
        with self.assertRaises(dm.DbusError) as ctx:
            self.b.call(
                self.a.unique_name, "/obj", "org.test.Other", "Missing",
                "", (), timeout=3.0,
            )
        self.assertIn("UnknownMethod", ctx.exception.name)

    def test_error_reply(self):
        def dispatch(member, args):
            raise dm.DbusMethodError("org.test.Nope", "denied")

        self.a.handle("/obj", "org.test.Iface", dispatch, {})
        with self.assertRaises(dm.DbusError) as ctx:
            self.b.call(
                self.a.unique_name, "/obj", "org.test.Iface", "Boom",
                "", (), timeout=3.0,
            )
        self.assertEqual(ctx.exception.name, "org.test.Nope")
        self.assertEqual(ctx.exception.message, "denied")

    def test_handler_exception_becomes_error_reply(self):
        def dispatch(member, args):
            raise RuntimeError("kaboom")

        self.a.handle("/obj", "org.test.Iface", dispatch, {})
        with self.assertRaises(dm.DbusError) as ctx:
            self.b.call(
                self.a.unique_name, "/obj", "org.test.Iface", "Boom",
                "", (), timeout=3.0,
            )
        self.assertIn("Internal", ctx.exception.name)

    def test_call_to_dead_endpoint_times_out(self):
        self.b.close()
        with self.assertRaises(dm.DbusError):
            self.a.call(
                self.b.unique_name, "/obj", "org.test.Iface", "Ping",
                "", (), timeout=1.0,
            )

    def test_signal_delivery(self):
        got = []
        self.b.set_signal_handler(
            lambda p, i, m, s, body: got.append((p, i, m, s, body))
        )
        self.a.emit_signal("/obj", "org.test.Iface", "Changed", "s", ("v",))
        self.wait_for(lambda: len(got) == 1)
        path, iface, member, sig, body = got[0]
        self.assertEqual((path, iface, member), ("/obj", "org.test.Iface", "Changed"))
        self.assertEqual(sig, "s")
        self.assertEqual(dm.decode_body("(s)", body), ("v",))

    def test_release_name(self):
        self.b.request_name("org.test.Watcher")
        self.b.release_name("org.test.Watcher")
        with self.assertRaises(dm.DbusError):
            self.a.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetNameOwner", "(s)",
                ("org.test.Watcher",), timeout=2.0,
            )

    def test_call_after_close_raises(self):
        self.b.close()
        with self.assertRaises(dm.DbusError):
            self.b.call(
                self.a.unique_name, "/obj", "org.test.Iface", "Ping",
                "", (), timeout=1.0,
            )


if __name__ == "__main__":
    unittest.main()
