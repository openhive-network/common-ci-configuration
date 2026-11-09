"""HTTP clients for the rewriter and the PostgREST behind it."""
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, List, Tuple
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Answer:
    status: int
    body: Any


@dataclass(frozen=True)
class Raw:
    """An answer as it came off the connection: status, headers (lowercase names) and body bytes."""
    status: int
    headers: Dict[str, str]
    body: bytes


def _send(request: urllib.request.Request) -> Answer:
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    return Answer(status, json.loads(raw) if raw.strip() else None)


def _read_chunked(reader: BinaryIO) -> bytes:
    body = b""
    while (size := int(reader.readline().split(b";")[0], 16)) > 0:
        body += reader.read(size)
        reader.readline()
    while reader.readline() not in (b"\r\n", b""):
        pass
    return body


def _read_answer(reader: BinaryIO, method: str) -> Raw:
    """Read one answer where the previous one's framing ended.

    Raises ValueError when what is there is not the start of an answer.
    """
    status_line = reader.readline()
    if not status_line.startswith(b"HTTP/1.1 "):
        raise ValueError(f"expected an answer's status line, read {status_line!r}")
    headers = {}
    while (line := reader.readline()) not in (b"\r\n", b""):
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    if method == "HEAD":
        body = b""
    elif "content-length" in headers:
        body = reader.read(int(headers["content-length"]))
    elif headers.get("transfer-encoding") == "chunked":
        body = _read_chunked(reader)
    else:
        raise ValueError(f"answer to {method} has no framing: {headers}")
    return Raw(int(status_line.split()[1]), headers, body)


@dataclass(frozen=True)
class Service:
    base: str

    def get(self, target: str) -> Answer:
        """GET `target` (path and query, sent as written)."""
        return _send(urllib.request.Request(self.base + target))

    def post_form(self, target: str, body: str) -> Answer:
        """POST `body` (sent as written) to `target` as a form."""
        return _send(urllib.request.Request(self.base + target, data=body.encode(), method="POST",
                                            headers={"Content-Type": "application/x-www-form-urlencoded"}))

    def in_turn(self, *requests: Tuple[str, str]) -> List[Raw]:
        """Send each (method, target) in turn on one keep-alive connection, reading each answer fully.

        Raises ValueError when an answer does not start where the previous one's framing ends.
        """
        address = urlsplit(self.base)
        with socket.create_connection((address.hostname, address.port), timeout=30) as sock, \
                sock.makefile("rb") as reader:
            answers = []
            for method, target in requests:
                sock.sendall(f"{method} {target} HTTP/1.1\r\nHost: {address.netloc}\r\n\r\n".encode())
                answers.append(_read_answer(reader, method))
            return answers
