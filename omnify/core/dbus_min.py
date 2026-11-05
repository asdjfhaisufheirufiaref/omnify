"""A minimal D-Bus session-bus endpoint in pure standard library.

Why this module exists
----------------------
The GTK/GLib build on this system exposes no working way to *receive*
D-Bus method calls from Python: ``Gio.bus_register_object`` is absent,
``register_object_with_closures2`` double-frees, ``register_subtree``
cannot fill its v-table, message filters fatal on network messages, and
the ``in-comm-message`` signal is not exported. The system tray
(StatusNotifierItem) still needs a D-Bus *server*, so this module speaks
the D-Bus wire protocol directly over the session bus's UNIX socket:
SASL authentication, ``Hello``, name ownership, method calls, replies,
errors and signals. Only the marshalling subset Omnify needs is
implemented (strings, ints, booleans, bytes, structs, arrays, dicts and
variants).

Wire reference: the D-Bus protocol specification — the message header
has signature ``yyyyuua(yv)`` and this module is big-endian throughout.
"""

from __future__ import annotations

import os
import select
import socket
import struct
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Protocol version in the message header.
PROTOCOL_VERSION = 1

#: Message types (second byte of the header).
METHOD_CALL = 1
METHOD_RETURN = 2
ERROR = 3
SIGNAL = 4

#: The ``NO_REPLY_EXPECTED`` flag (third byte of the header).
FLAG_NO_REPLY_EXPECTED = 0x01

#: Header field codes.
FIELD_PATH = 1
FIELD_INTERFACE = 2
FIELD_MEMBER = 3
FIELD_ERROR_NAME = 4
FIELD_REPLY_SERIAL = 5
FIELD_DESTINATION = 6
FIELD_SENDER = 7
FIELD_SIGNATURE = 8
FIELD_UNIX_FDS = 9

#: Hard message-size limit from the specification (128 MiB).
MAX_MESSAGE = 1 << 27

#: Basic type codes this module marshals.
_BASIC = "ynqixutbdsog"

#: Internal sentinel: _recv_some waited and nothing was ready.
_TIMEOUT = object()


class DbusError(Exception):
    """A D-Bus protocol or method error (``name`` + optional text)."""

    def __init__(self, name: str, message: str = "") -> None:
        self.name = name
        self.message = message
        text = f"{name}: {message}" if message else name
        super().__init__(text)


class DbusMethodError(DbusError):
    """Raised inside a method handler to produce an error reply."""


class _NeedMore(Exception):
    """Internal: the buffer does not hold a complete value yet."""


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def _scan_container(sig: str, start: int) -> int:
    """Index just past the matching close char of the container at *start*."""
    depth = 0
    for j in range(start, len(sig)):
        ch = sig[j]
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError(f"unbalanced signature: {sig!r}")


def _element_sig(sig: str, i: int) -> Tuple[str, int]:
    """``(element_signature, index_after)`` for the array at ``sig[i]``.

    ``sig[i]`` must be ``'a'``; the element is a single complete type.
    """
    j = i + 1
    c = sig[j]
    if c == "a":
        elem, end = _element_sig(sig, j)
        return "a" + elem, end
    if c in _BASIC or c == "v":
        return c, j + 1
    if c in "({":
        end = _scan_container(sig, j)
        return sig[j:end], end
    raise ValueError(f"bad array element in signature: {sig!r}")


def _fields(inner: str) -> List[str]:
    """Split a struct's inner signature into single complete types."""
    out: List[str] = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "a":
            _, j = _element_sig(inner, i)
            out.append(inner[i:j])
            i = j
        elif c in "({":
            j = _scan_container(inner, i)
            out.append(inner[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return out


def _align_of(sig: str) -> int:
    """The natural alignment (bytes) of a single complete type."""
    c = sig[0]
    if c in "yg":
        return 1
    if c in "nq":
        return 2
    if c in "iubsog" and c != "o":
        return 4
    if c == "o":
        return 4
    if c in "xtd":
        return 8
    if c in "a({v":
        return 8 if c in "({" else 4
    raise ValueError(f"unknown type code: {sig!r}")


def _pad_to(buf: bytearray, align: int) -> None:
    """Append NUL padding so ``len(buf)`` reaches an *align* boundary."""
    pad = (-len(buf)) % align
    if pad:
        buf.extend(b"\x00" * pad)


def _align(offset: int, align: int) -> int:
    """The smallest offset >= *offset* that is a multiple of *align*."""
    return offset + ((-offset) % align)


def _args_sig(sig: str) -> str:
    """*sig* as the single struct a method body actually is.

    ``"s"`` becomes ``"(s)"``; an already-struct signature is kept.
    """
    return sig if sig.startswith("(") else "(" + sig + ")"


def _sig_field(sig: str) -> str:
    """The SIGNATURE header field form of *sig*.

    Internally, bodies are handled as one tuple (see :func:`_args_sig`),
    but on the wire the header field carries the bare concatenation of
    the body's field types: GLib, Qt and the brokers all expect that,
    and a stray pair of tuple parentheses around it makes peers fail
    method lookups ("No such method").
    """
    if len(sig) >= 2 and sig.startswith("(") and sig.endswith(")"):
        return sig[1:-1]
    return sig


# ---------------------------------------------------------------------------
# Marshalling
# ---------------------------------------------------------------------------

def encode_body(sig: str, value: Any) -> bytes:
    """Marshal *value* (shaped to match *sig*) into D-Bus wire bytes.

    Conventions: strings are ``str``, booleans ``bool``, bytes-like data
    for ``ay`` any ``bytes``/``bytearray``, structs are tuples, arrays
    are lists (or dicts for ``a{...}``), variants are ``(sig, value)``.
    """
    buf = bytearray()
    _encode(sig, value, buf)
    return bytes(buf)


def decode_body(sig: str, data: bytes, fmt: str = ">") -> Any:
    """Unmarshal wire *data* of signature *sig* into Python values.

    *fmt* is the struct endianness prefix of the wire data (``">"`` for
    big-endian, ``"<"`` for little-endian messages).
    """
    value, _off = _decode(sig, data, 0, fmt)
    return value


def _encode(sig: str, value: Any, buf: bytearray) -> None:
    if sig.startswith("("):
        _encode_struct(sig, value, buf)
        return
    fields = _fields(sig)
    if len(fields) == 1:
        _encode_one(fields[0], value, buf)
    else:
        # A flat multi-field signature is one anonymous struct.
        _encode_struct("(" + sig + ")", value, buf)


def _encode_one(sig: str, value: Any, buf: bytearray) -> None:
    c = sig[0]
    if c in _BASIC:
        _encode_basic(c, value, buf)
    elif c == "a":
        elem, _end = _element_sig(sig, 0)
        _encode_array(elem, value, buf)
    elif c in "({":
        _encode_struct(sig, value, buf)
    elif c == "v":
        inner_sig, inner_value = value
        _encode_basic("g", inner_sig, buf)
        _encode(inner_sig, inner_value, buf)
    else:
        raise ValueError(f"cannot encode signature: {sig!r}")


def _encode_basic(c: str, value: Any, buf: bytearray) -> None:
    if c == "y":
        buf.append(int(value) & 0xFF)
    elif c == "b":
        _pad_to(buf, 4)
        buf.extend(struct.pack(">I", 1 if value else 0))
    elif c in "nqixut":
        fmt = {"y": ">B", "n": ">h", "q": ">H", "i": ">i",
               "u": ">I", "x": ">q", "t": ">Q"}[c]
        _pad_to(buf, _align_of(c))
        buf.extend(struct.pack(fmt, value))
    elif c == "d":
        _pad_to(buf, 8)
        buf.extend(struct.pack(">d", float(value)))
    elif c in "so":
        _pad_to(buf, 4)
        data = value.encode("utf-8")
        if b"\x00" in data:
            raise ValueError("D-Bus strings may not contain NUL")
        buf.extend(struct.pack(">I", len(data)))
        buf.extend(data)
        buf.append(0)
    elif c == "g":
        data = value.encode("ascii")
        if len(data) > 255:
            raise ValueError("signature longer than 255 bytes")
        buf.append(len(data))
        buf.extend(data)
        buf.append(0)


def _encode_array(elem_sig: str, value: Any, buf: bytearray) -> None:
    _pad_to(buf, 4)
    if elem_sig.startswith("{"):
        if isinstance(value, dict):
            items = sorted(value.items(), key=lambda kv: str(kv[0]))
        else:
            items = list(value)
        key_sig, val_sig = _fields(elem_sig[1:-1])
        parts = bytearray()
        for key, val in items:
            _pad_to(parts, 8)  # DICT_ENTRY is always 8-aligned
            _encode(key_sig, key, parts)
            _encode(val_sig, val, parts)
    else:
        parts = bytearray()
        for item in value:
            _encode(elem_sig, item, parts)
    # Per the spec the length covers the array elements only: neither
    # the alignment padding after the length field (still required,
    # even for an empty array) nor any padding after the last element
    # is counted in it.
    pad = (- (len(buf) + 4)) % _align_of(elem_sig)
    buf.extend(struct.pack(">I", len(parts)))
    buf.extend(b"\x00" * pad)
    buf.extend(parts)


def _encode_struct(sig: str, value: Any, buf: bytearray) -> None:
    _pad_to(buf, 8)
    fields = _fields(sig[1:-1])
    # Shape of *value*: a sequence matching the fields (a method body,
    # or a 1-tuple for a single-field struct), or — for a single-field
    # struct — the bare field value (e.g. a variant pair for "(v)").
    if isinstance(value, (list, tuple)) and len(value) == len(fields):
        parts = value
    else:
        parts = (value,)
    if len(parts) != len(fields):
        raise ValueError(
            f"struct {sig!r} expects {len(fields)} field(s), got {len(parts)}"
        )
    for field_sig, field_value in zip(fields, parts):
        _encode(field_sig, field_value, buf)


def _decode(
    sig: str, data: bytes, off: int, fmt: str = ">"
) -> Tuple[Any, int]:
    values: List[Any] = []
    for field in _fields(sig):
        value, off = _decode_one(field, data, off, fmt)
        values.append(value)
    if len(values) == 1:
        return values[0], off
    return tuple(values), off


def _decode_one(
    sig: str, data: bytes, off: int, fmt: str = ">"
) -> Tuple[Any, int]:
    c = sig[0]
    if c in _BASIC:
        return _decode_basic(c, data, off, fmt)
    if c == "a":
        elem, _end = _element_sig(sig, 0)
        return _decode_array(elem, data, off, fmt)
    if c in "({":
        return _decode_struct(sig, data, off, fmt)
    if c == "v":
        inner_sig, off = _decode_basic("g", data, off, fmt)
        value, off = _decode(inner_sig, data, off, fmt)
        return (inner_sig, value), off
    raise ValueError(f"cannot decode signature: {sig!r}")


def _take(data: bytes, off: int, n: int) -> bytes:
    if off + n > len(data):
        raise _NeedMore
    return data[off:off + n]


def _decode_basic(
    c: str, data: bytes, off: int, fmt: str = ">"
) -> Tuple[Any, int]:
    if c == "y":
        _take(data, off, 1)  # range check
        return data[off], off + 1
    if c == "b":
        off = _align(off, 4)
        (flag,) = struct.unpack(fmt + "I", _take(data, off, 4))
        return bool(flag), off + 4
    if c in "nqixut":
        code = {"y": "B", "n": "h", "q": "H", "i": "i",
                "u": "I", "x": "q", "t": "Q"}[c]
        size = struct.calcsize(code)
        off = _align(off, size)
        return struct.unpack(fmt + code, _take(data, off, size))[0], off + size
    if c == "d":
        off = _align(off, 8)
        return struct.unpack(fmt + "d", _take(data, off, 8))[0], off + 8
    if c in "so":
        off = _align(off, 4)
        (n,) = struct.unpack(fmt + "I", _take(data, off, 4))
        raw = _take(data, off + 4, n + 1)
        if raw[-1] != 0:
            raise ValueError("string-like value is not NUL-terminated")
        return raw[:n].decode("utf-8"), off + 4 + n + 1
    if c == "g":
        _take(data, off, 1)  # range check
        n = data[off]
        raw = _take(data, off + 1, n + 1)
        if raw[-1] != 0:
            raise ValueError("signature is not NUL-terminated")
        return raw[:n].decode("ascii"), off + 1 + n + 1
    raise ValueError(f"cannot decode basic type: {c!r}")


def _decode_array(
    elem_sig: str, data: bytes, off: int, fmt: str = ">"
) -> Tuple[Any, int]:
    off = _align(off, 4)
    (n,) = struct.unpack(fmt + "I", _take(data, off, 4))
    off += 4
    start = _align(off, _align_of(elem_sig))
    end = start + n
    if elem_sig.startswith("{"):
        key_sig, val_sig = _fields(elem_sig[1:-1])
        out: Dict[Any, Any] = {}
        while start < end:
            start = _align(start, 8)  # DICT_ENTRY is always 8-aligned
            key, start = _decode(key_sig, data, start, fmt)
            val, start = _decode(val_sig, data, start, fmt)
            out[key] = val
        return out, end
    out_list: List[Any] = []
    while start < end:
        item, start = _decode(elem_sig, data, start, fmt)
        out_list.append(item)
    if elem_sig == "y":
        return bytes(out_list), end
    return out_list, end


def _decode_struct(
    sig: str, data: bytes, off: int, fmt: str = ">"
) -> Tuple[Tuple[Any, ...], int]:
    off = _align(off, 8)
    values: List[Any] = []
    for field_sig in _fields(sig[1:-1]):
        value, off = _decode(field_sig, data, off, fmt)
        values.append(value)
    return tuple(values), off


# ---------------------------------------------------------------------------
# Message framing
# ---------------------------------------------------------------------------

def build_message(
    mtype: int,
    serial: int,
    *,
    path: str = "",
    interface: str = "",
    member: str = "",
    dest: str = "",
    error_name: str = "",
    reply_serial: int = 0,
    body_sig: str = "",
    body: bytes = b"",
) -> bytes:
    """Assemble one complete D-Bus message (header + body), big-endian."""
    pairs: List[Tuple[int, str, Any]] = []
    if mtype in (METHOD_CALL, SIGNAL) and path:
        pairs.append((FIELD_PATH, "o", path))
    if interface:
        pairs.append((FIELD_INTERFACE, "s", interface))
    if member:
        pairs.append((FIELD_MEMBER, "s", member))
    if mtype == ERROR and error_name:
        pairs.append((FIELD_ERROR_NAME, "s", error_name))
    if reply_serial:
        pairs.append((FIELD_REPLY_SERIAL, "u", reply_serial))
    if dest:
        pairs.append((FIELD_DESTINATION, "s", dest))
    if body_sig:
        pairs.append((FIELD_SIGNATURE, "g", _sig_field(body_sig)))

    # The header fields array: a(yv)
    parts = bytearray()
    for code, ftype, value in pairs:
        _encode("(yv)", (code, (ftype, value)), parts)

    hdr = bytearray()
    hdr.append(0x42)  # big-endian
    hdr.append(mtype)
    hdr.append(0)  # flags
    hdr.append(PROTOCOL_VERSION)
    hdr.extend(struct.pack(">I", len(body)))
    hdr.extend(struct.pack(">I", serial))
    # field array
    _pad_to(hdr, 4)
    hdr.extend(struct.pack(">I", len(parts)))
    _pad_to(hdr, 8)  # element alignment of (yv)
    hdr.extend(parts)
    # The field-array length written above is the sole authority on the
    # header size: both dbus-broker and GLib locate the body at
    # align8(16 + n_fields), so nothing may sit between the last field
    # and that boundary.  A legacy NUL terminator, where a sender keeps
    # one, is simply the first zero byte of the padding appended below.
    while len(hdr) % 8:
        hdr.append(0)
    return bytes(hdr) + body


def parse_message(data: bytes) -> Dict[str, Any]:
    """Parse one complete message buffer into a plain dict.

    Keys: ``type``, ``flags``, ``serial``, ``path``, ``interface``,
    ``member``, ``error_name``, ``reply_serial``, ``dest``, ``sender``,
    ``body_sig``, ``body``.
    """
    if len(data) < 16 or data[0] not in (0x42, 0x6C):
        raise DbusError("org.omnify.DBus.Error.Protocol", "bad message header")
    # Multi-byte fields are read with the message's own endianness; the
    # body is kept raw and decoded with the same prefix (byte-swap tricks
    # corrupt string data).
    fmt = "<" if data[0] == 0x6C else ">"
    mtype, _flags, proto = data[1], data[2], data[3]
    if proto != PROTOCOL_VERSION:
        raise DbusError(
            "org.omnify.DBus.Error.Protocol",
            f"unsupported protocol version {proto}",
        )
    body_len, serial = struct.unpack_from(fmt + "II", data, 4)
    if len(data) < 16 + body_len:
        raise DbusError("org.omnify.DBus.Error.Protocol", "truncated message")

    # The field-array length is the third uint32 of the fixed header
    # (offset 12); offset 8 holds the serial.
    fields: Dict[int, Any] = {}
    off = 12
    (arr_len,) = struct.unpack_from(fmt + "I", data, off)
    arr_start = _align(off + 4, 8)
    arr_end = arr_start + arr_len
    off = arr_start
    while off < arr_end:
        off = _align(off, 8)
        _take(data, off, 1)  # range check
        code = data[off]
        if code == 0:
            break
        # (yv) field: the code byte, then the variant's signature.
        sig, off = _decode_basic("g", data, off + 1, fmt)
        value, off = _decode(sig, data, off, fmt)
        fields[code] = value
    if arr_end > len(data):
        raise DbusError("org.omnify.DBus.Error.Protocol", "truncated header")

    body = data[len(data) - body_len:] if body_len else b""
    return {
        "type": mtype,
        "flags": _flags,
        "serial": serial,
        "path": fields.get(FIELD_PATH, ""),
        "interface": fields.get(FIELD_INTERFACE, ""),
        "member": fields.get(FIELD_MEMBER, ""),
        "error_name": fields.get(FIELD_ERROR_NAME, ""),
        "reply_serial": fields.get(FIELD_REPLY_SERIAL, 0),
        "dest": fields.get(FIELD_DESTINATION, ""),
        "sender": fields.get(FIELD_SENDER, ""),
        "body_sig": fields.get(FIELD_SIGNATURE, ""),
        "body": body,
        "fmt": fmt,
    }


def _find_header_end(data: bytes) -> int:
    """Offset of the first body byte, or raise :class:`_NeedMore`.

    The field array sits at offset 16; its byte length is the uint32 at
    offset 12, read with the message's own endianness (brokers answer
    little-endian).  Every wire convention in the wild agrees that the
    body begins at ``align8(16 + n_fields)``: dbus-broker counts the
    raw ``a(yv)`` element bytes and may put a legacy NUL terminator in
    the gap that follows; GLib writes no terminator at all; libdbus
    style senders count the terminator inside the length.  The same
    formula covers all three, because the gap is always zero-filled.
    """
    if len(data) < 16:
        raise _NeedMore
    fmt = "<I" if data[0] == 0x6C else ">I"
    (arr_len,) = struct.unpack_from(fmt, data, 12)
    if arr_len > 65536:
        raise ValueError("implausible header field length")
    header_end = _align(16 + arr_len, 8)
    if header_end > len(data):
        raise _NeedMore
    if any(data[16 + arr_len:header_end]):
        raise ValueError("non-zero header padding")
    return header_end


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def read_message(sock: socket.socket) -> Optional[Dict[str, Any]]:
    """Read one complete message from *sock*; ``None`` on EOF/error."""
    buf = _recv_exact(sock, 16)
    if buf is None:
        return None
    if buf[0] not in (0x42, 0x6C):
        return None
    fmt = ">I" if buf[0] == 0x42 else "<I"
    (body_len,) = struct.unpack_from(fmt, buf, 4)
    if body_len > MAX_MESSAGE:
        return None
    while True:
        need = 4096
        try:
            header_end = _find_header_end(buf)
        except _NeedMore:
            pass
        else:
            need = header_end + body_len - len(buf)
            if need <= 0:
                try:
                    return parse_message(buf)
                except DbusError:
                    return None
        chunk_size = min(65536, max(need, 1024))
        try:
            chunk = sock.recv(chunk_size)
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        if len(buf) > MAX_MESSAGE:
            return None


# ---------------------------------------------------------------------------
# Address handling
# ---------------------------------------------------------------------------

def default_address() -> str:
    """The session-bus address from the environment (may be empty)."""
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not addr:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime and os.path.isabs(runtime):
            addr = f"unix:path={runtime}/bus"
    return addr


def parse_address(addr: str) -> Optional[Tuple[int, str]]:
    """``(family, path)`` for the first usable ``unix:`` target, else None."""
    for target in addr.split(","):
        target = target.strip()
        if not target:
            continue
        transport, _, params = target.partition(":")
        if transport != "unix":
            continue
        if "path=" in params:
            return socket.AF_UNIX, params.partition("path=")[2]
        if "abstract=" in params:
            return socket.AF_UNIX, "\0" + params.partition("abstract=")[2]
    return None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

Dispatch = Callable[[str, Tuple[Any, ...]], Optional[Tuple[str, Any]]]


class DbusEndpoint:
    """A session-bus connection that can both call methods and serve them.

    The endpoint owns a background reader thread that answers incoming
    method calls via the handlers installed with :meth:`handle`. All
    public methods are safe to call from any thread; method handlers
    themselves run on the reader thread, so GUI callbacks must hop to
    the main thread (see :mod:`omnify.core.sni`).
    """

    def __init__(self, address: Optional[str] = None) -> None:
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._serial_lock = threading.Lock()
        self._serial = 0
        self._pending: Dict[int, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._closed = False
        self._unique_name = ""
        self._handlers: Dict[Tuple[str, str], Dispatch] = {}
        self._method_sigs: Dict[Tuple[str, str, str], str] = {}
        self._rx_buf = bytearray()
        self._signal_handler: Optional[
            Callable[[str, str, str, str, bytes], None]
        ] = None
        self._address = address or default_address()

    # -- connection lifecycle ---------------------------------------------
    @property
    def unique_name(self) -> str:
        """Our unique bus name (valid after :meth:`connect`)."""
        return self._unique_name

    def connect(
        self, timeout: float = 5.0, sock: Optional[socket.socket] = None
    ) -> None:
        """Open the socket, authenticate and ``Hello`` (raises on failure).

        *sock* may supply an already-connected socket (the loopback
        tests use it); otherwise one is created from the bus address.
        """
        if self._sock is not None:
            return
        if sock is None:
            parsed = parse_address(self._address)
            if parsed is None:
                raise DbusError(
                    "org.omnify.DBus.Error.NoBus",
                    f"no usable UNIX address in {self._address!r}",
                )
            family, path = parsed
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(path)
        self._sock = sock
        try:
            self._authenticate()
        finally:
            # The handshake above runs with a timeout so a misbehaving
            # broker cannot wedge the caller; message mode is blocking.
            try:
                sock.settimeout(None)
            except OSError:
                pass
        self._reader = threading.Thread(
            target=self._run, name="omnify-dbus", daemon=True
        )
        self._reader.start()
        reply = self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "Hello",
            "",
            (),
            timeout,
        )
        self._unique_name = reply[0] if reply else ""

    def close(self) -> None:
        """Close the connection; pending calls fail with an error."""
        if self._closed:
            return
        self._closed = True
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        reader = self._reader
        if reader is not None:
            # close() is also reached from the reader thread itself (the
            # EOF path in _run); joining the current thread would raise
            # RuntimeError and kill the reader before it can settle the
            # pending calls below.
            if reader is not threading.current_thread():
                reader.join(timeout=2.0)
            self._reader = None
        error = DbusError("org.omnify.DBus.Error.Failed", "connection closed")
        with self._pending_lock:
            for _event, result in self._pending.values():
                result["error"] = error
                _event.set()
            self._pending.clear()

    def _authenticate(self) -> None:
        """Perform the SASL handshake (raises on any protocol failure).

        Follows the current D-Bus protocol (spec revision 0.43 and later,
        as implemented by dbus-broker >= 36): the *client* speaks first,
        the very first byte of the stream is a NUL that prefixes the first
        command, and lines are terminated with CRLF. Only the EXTERNAL
        mechanism is supported; no uid is transmitted — the broker derives
        it from the unix socket's peer credentials, so there is nothing to
        get wrong on the client side.

        The exchange is:

            C: \\0AUTH EXTERNAL\\r\\n
            S: DATA\\r\\n                  (challenge)
            C: DATA\\r\\n                  (empty reply = peer credentials)
            S: OK <guid>\\r\\n
            C: BEGIN\\r\\n
            -- the D-Bus message stream starts here --
        """
        sock = self._sock
        assert sock is not None
        # The NUL byte is part of the first line: the broker strips it and
        # treats the remainder as the first SASL command.
        sock.sendall(b"\x00AUTH EXTERNAL\r\n")
        while True:
            line = self._read_auth_line(sock)
            if line == b"DATA":
                # Challenge: answer with an empty DATA line; the broker
                # authenticates from the socket's peer credentials.
                sock.sendall(b"DATA\r\n")
            elif line.startswith(b"OK"):
                # Authenticated. The broker replies nothing to BEGIN;
                # after it the wire carries binary D-Bus messages only.
                sock.sendall(b"BEGIN\r\n")
                return
            else:
                raise DbusError(
                    "org.omnify.DBus.Error.Auth",
                    line[:64].decode("ascii", "replace"),
                )

    @staticmethod
    def _read_auth_line(sock: socket.socket) -> bytes:
        """Read one SASL line.

        Lines are CRLF-terminated on the wire; a bare LF is tolerated for
        older brokers. Raises :class:`DbusError` on EOF, timeout or an
        oversized line.
        """
        buf = b""
        prev = b""
        while True:
            try:
                ch = sock.recv(1)
            except OSError:
                raise DbusError(
                    "org.omnify.DBus.Error.Auth", "connection lost"
                ) from None
            if not ch:
                raise DbusError(
                    "org.omnify.DBus.Error.Auth", "connection lost"
                )
            if ch == b"\n":
                if prev == b"\r":
                    buf = buf[:-1]  # strip the \r of a CRLF terminator
                return buf
            buf += ch
            prev = ch
            if len(buf) > 4096:
                raise DbusError(
                    "org.omnify.DBus.Error.Auth", "oversized auth line"
                )

    # -- names -------------------------------------------------------------
    def request_name(self, name: str, timeout: float = 5.0) -> bool:
        """``True`` when we became the primary owner of *name*."""
        try:
            (status,) = self.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "RequestName",
                "(su)",
                (name, 0),
                timeout,
            )
            return status == 1
        except DbusError:
            return False

    def release_name(self, name: str, timeout: float = 3.0) -> None:
        try:
            self.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ReleaseName",
                "(s)",
                (name,),
                timeout,
            )
        except DbusError:
            pass

    # -- calls & signals ----------------------------------------------------
    def call(
        self,
        dest: str,
        path: str,
        interface: str,
        member: str,
        sig: str = "",
        args: Tuple[Any, ...] = (),
        timeout: float = 5.0,
    ) -> Tuple[Any, ...]:
        """Call a method synchronously; returns the decoded reply body.

        *args* is the tuple of argument values shaped to *sig* (a
        single-field signature takes the bare value or a 1-tuple).
        """
        if self._closed or self._sock is None:
            raise DbusError(
                "org.omnify.DBus.Error.Failed", "connection is closed"
            )
        body = encode_body(_args_sig(sig), args) if sig else b""
        serial = self._next_serial()
        msg = build_message(
            METHOD_CALL,
            serial,
            dest=dest,
            path=path,
            interface=interface,
            member=member,
            body_sig=sig,
            body=body,
        )
        event, result = threading.Event(), {}
        with self._pending_lock:
            self._pending[serial] = (event, result)
        with self._send_lock:
            try:
                self._sock.sendall(msg)
            except OSError as exc:
                with self._pending_lock:
                    self._pending.pop(serial, None)
                raise DbusError(
                    "org.omnify.DBus.Error.Failed", str(exc)
                ) from None
        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(serial, None)
            raise DbusError(
                "org.omnify.DBus.Error.Timeout",
                f"no reply to {interface}.{member}",
            )
        if "error" in result:
            raise result["error"]
        return result["value"]

    def emit_signal(
        self,
        path: str,
        interface: str,
        member: str,
        sig: str = "",
        args: Tuple[Any, ...] = (),
    ) -> None:
        """Emit a signal (never raises; a dead socket is simply ignored)."""
        if self._closed or self._sock is None:
            return
        body = encode_body(_args_sig(sig), args) if sig else b""
        msg = build_message(
            SIGNAL,
            self._next_serial(),
            path=path,
            interface=interface,
            member=member,
            body_sig=sig,
            body=body,
        )
        with self._send_lock:
            try:
                self._sock.sendall(msg)
            except OSError:
                pass

    def set_signal_handler(
        self,
        cb: Optional[Callable[[str, str, str, str, bytes], None]],
    ) -> None:
        """Receive ``(path, interface, member, body_sig, body)`` per signal.

        Called on the reader thread; the callback must not block.
        Pass ``None`` to disable.
        """
        self._signal_handler = cb

    # -- serving -------------------------------------------------------------
    def handle(
        self,
        path: str,
        interface: str,
        dispatch: Dispatch,
        method_sigs: Optional[Dict[str, str]] = None,
    ) -> None:
        """Serve method calls on ``(path, interface)``.

        *dispatch* receives ``(member, args)`` and returns either
        ``None`` (empty reply), ``None``-like for no args, or a
        ``(reply_sig, value)`` pair; raising :class:`DbusMethodError`
        produces an error reply.
        """
        self._handlers[(path, interface)] = dispatch
        for member, sig in (method_sigs or {}).items():
            self._method_sigs[(path, interface, member)] = sig

    def _next_serial(self) -> int:
        with self._serial_lock:
            self._serial += 1
            if self._serial >= 0xFFFFFFFF:
                self._serial = 1
            return self._serial

    # -- reader thread ---------------------------------------------------------
    def _run(self) -> None:
        while not self._closed and self._sock is not None:
            msg = self._rx_message()
            if msg is None:
                break
            try:
                self._dispatch(msg)
            except Exception:
                # A malformed message must never kill the reader thread.
                pass
        if not self._closed:
            self.close()

    def _recv_some(self, n: int):
        """Blocking recv with a short wake-up timeout.

        Returns the received bytes, ``None`` on EOF/error, or the
        :data:`_TIMEOUT` sentinel when nothing was ready in time — so
        :meth:`close` can interrupt the reader thread promptly
        (closing a socket does not unblock a concurrent recv).
        """
        sock = self._sock
        if sock is None:
            return None
        try:
            ready, _, _ = select.select([sock], [], [], 0.2)
        except OSError:
            return None
        if not ready:
            return _TIMEOUT
        try:
            chunk = sock.recv(n)
        except OSError:
            return None
        # recv() reports EOF as b""; normalise it to the None sentinel.
        return chunk if chunk else None

    def _rx_message(self) -> Optional[Dict[str, Any]]:
        """Receive and parse the next complete message.

        Bytes belonging to later (pipelined) messages stay buffered in
        ``_rx_buf`` for the next call.  Returns ``None`` on EOF, an
        error, or a corrupt stream — the stream is unusable either way.
        """
        while not self._closed:
            if len(self._rx_buf) < 16:
                chunk = self._recv_some(16 - len(self._rx_buf))
                if chunk is None:
                    return None
                if chunk is _TIMEOUT:
                    continue  # wake-up timeout
                self._rx_buf.extend(chunk)
            head = bytes(self._rx_buf[:16])
            if head[0] not in (0x42, 0x6C):
                return None
            fmt = ">I" if head[0] == 0x42 else "<I"
            (body_len,) = struct.unpack_from(fmt, head, 4)
            if body_len > MAX_MESSAGE:
                return None
            try:
                header_end = _find_header_end(bytes(self._rx_buf))
            except _NeedMore:
                header_end = -1
            except ValueError:
                return None
            if header_end >= 0 and header_end + body_len <= len(self._rx_buf):
                total = header_end + body_len
                raw = bytes(self._rx_buf[:total])
                del self._rx_buf[:total]
                try:
                    return parse_message(raw)
                except DbusError:
                    return None
            chunk = self._recv_some(65536)
            if chunk is None:
                return None
            if chunk is _TIMEOUT:
                continue  # wake-up timeout
            self._rx_buf.extend(chunk)
            if len(self._rx_buf) > MAX_MESSAGE:
                return None
        return None

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        mtype = msg["type"]
        if mtype == METHOD_RETURN:
            try:
                value = self._decode_body(msg)
            except Exception:
                # A reply we cannot decode must settle the waiter with
                # an error instead of letting the call time out.
                self._settle_error(msg)
                return
            self._settle(msg["reply_serial"], value=value)
        elif mtype == ERROR:
            self._settle_error(msg)
        elif mtype == METHOD_CALL:
            self._serve_call(msg)
        elif mtype == SIGNAL:
            cb = self._signal_handler
            if cb is not None:
                try:
                    cb(
                        msg["path"],
                        msg["interface"],
                        msg["member"],
                        msg["body_sig"],
                        msg["body"],
                    )
                except Exception:
                    pass
        # Unknown message types are ignored.

    def _decode_body(self, msg: Dict[str, Any]) -> Tuple[Any, ...]:
        sig = msg["body_sig"]
        if not sig:
            return ()
        return decode_body(_args_sig(sig), msg["body"], msg.get("fmt", ">"))

    def _settle(self, reply_serial: int, value: Tuple[Any, ...]) -> None:
        with self._pending_lock:
            slot = self._pending.pop(reply_serial, None)
        if slot is not None:
            slot[1]["value"] = value
            slot[0].set()

    def _settle_error(self, msg: Dict[str, Any]) -> None:
        name = msg["error_name"] or "org.omnify.DBus.Error.Failed"
        detail = ""
        try:
            body_args = self._decode_body(msg)
            if body_args and isinstance(body_args[0], str):
                detail = body_args[0]
        except Exception:
            pass
        error = DbusError(name, detail)
        with self._pending_lock:
            slot = self._pending.pop(msg["reply_serial"], None)
        if slot is not None:
            slot[1]["error"] = error
            slot[0].set()

    def _serve_call(self, msg: Dict[str, Any]) -> None:
        handler = self._handlers.get((msg["path"], msg["interface"]))
        if handler is None:
            self._reply_error(
                msg, "org.freedesktop.DBus.Error.UnknownMethod",
                f"no object at {msg['path']!r}",
            )
            return
        sig = self._method_sigs.get(
            (msg["path"], msg["interface"], msg["member"]), ""
        )
        try:
            # The body is one struct of the argument values.
            args = (
                decode_body(_args_sig(sig), msg["body"], msg.get("fmt", ">"))
                if sig else ()
            )
            result = handler(msg["member"], args)
        except DbusMethodError as exc:
            self._reply_error(msg, exc.name, exc.message)
            return
        except Exception as exc:  # never leak a handler crash
            self._reply_error(
                msg,
                "org.freedesktop.DBus.Error.Internal",
                repr(exc),
            )
            return
        if result is None:
            reply_sig, value = "", ()
        else:
            reply_sig, value = result
        if msg["flags"] & FLAG_NO_REPLY_EXPECTED:
            return
        try:
            body = (
                encode_body(_args_sig(reply_sig), value)
                if reply_sig
                else b""
            )
            self._send(
                build_message(
                    METHOD_RETURN,
                    self._next_serial(),
                    dest=msg["sender"],
                    reply_serial=msg["serial"],
                    body_sig=reply_sig,
                    body=body,
                )
            )
        except Exception:
            # A mis-shaped handler result must become an error reply,
            # never a dropped one.
            self._reply_error(
                msg,
                "org.freedesktop.DBus.Error.Internal",
                "handler returned a value that does not match "
                f"{reply_sig!r}",
            )

    def _reply_error(self, msg: Dict[str, Any], name: str, text: str) -> None:
        if msg["flags"] & FLAG_NO_REPLY_EXPECTED:
            return
        body = encode_body("(s)", (text,))
        self._send(
            build_message(
                ERROR,
                self._next_serial(),
                dest=msg["sender"],
                error_name=name,
                reply_serial=msg["serial"],
                body_sig="(s)",
                body=body,
            )
        )

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            return
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError:
                pass
