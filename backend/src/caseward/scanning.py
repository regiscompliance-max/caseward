"""Malware scanning with ClamAV's clamd, over its INSTREAM protocol."""

import socket
import struct
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Verdict:
    result: Literal["clean", "infected"]
    signature: str | None = None


class Scanner(Protocol):
    name: str

    def scan(self, data: bytes) -> Verdict: ...


class ScannerError(Exception):
    """The scanner could not give a verdict. The job retries; the file stays unscanned."""


class ClamdScanner:
    name = "clamd"
    CHUNK = 64 * 1024

    def __init__(self, host: str, port: int = 3310, timeout: float = 60.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def scan(self, data: bytes) -> Verdict:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                for start in range(0, len(data), self.CHUNK):
                    chunk = data[start:start + self.CHUNK]
                    sock.sendall(struct.pack("!I", len(chunk)) + chunk)
                sock.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError as exc:
            raise ScannerError(f"clamd unavailable: {exc}") from exc
        text = reply.rstrip(b"\0").decode(errors="replace")
        if text.endswith(" OK"):
            return Verdict("clean")
        if text.endswith(" FOUND"):
            return Verdict("infected", text.removeprefix("stream: ").removesuffix(" FOUND"))
        raise ScannerError(f"clamd error: {text}")
