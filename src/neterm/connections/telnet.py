# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Telnet connection using the standard library telnetlib replacement.

Python 3.13 removed telnetlib entirely. We use a minimal socket-based
implementation so neterm works on all supported Python versions.
"""

import socket
from typing import Optional

from neterm.connections.base import Connection

# Telnet protocol bytes
IAC = bytes([255])
WILL = bytes([251])
WONT = bytes([252])
DO = bytes([253])
DONT = bytes([254])
SB = bytes([250])
SE = bytes([240])

# Common telnet options
ECHO = bytes([1])
SUPPRESS_GO_AHEAD = bytes([3])
NAWS = bytes([31])  # Negotiate About Window Size
TERMINAL_TYPE = bytes([24])


class TelnetConnection(Connection):
    """Raw TCP/telnet connection with basic telnet negotiation."""

    DEFAULT_PORT = 23

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = 10.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._negotiation_buf = b""

    def open(self) -> None:
        if self._sock is not None:
            return
        self._sock = socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )
        self._sock.setblocking(False)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def read(self, size: int = 4096) -> bytes:
        if self._sock is None:
            return b""
        try:
            data = self._sock.recv(size)
            if data == b"":
                # Connection closed by remote
                self.close()
                return b""
            return self._process_telnet(data)
        except BlockingIOError:
            return b""
        except (ConnectionResetError, BrokenPipeError, OSError):
            self.close()
            return b""

    def write(self, data: bytes) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendall(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    @property
    def name(self) -> str:
        port_str = f":{self._port}" if self._port != self.DEFAULT_PORT else ""
        return f"telnet://{self._host}{port_str}"

    def _process_telnet(self, data: bytes) -> bytes:
        """Strip telnet IAC sequences and respond to negotiations."""
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i:i + 1] == IAC:
                if i + 1 >= len(data):
                    break
                cmd = data[i + 1:i + 2]
                if cmd == IAC:
                    out.append(255)
                    i += 2
                elif cmd in (DO, DONT, WILL, WONT):
                    if i + 2 >= len(data):
                        break
                    opt = data[i + 2:i + 3]
                    self._handle_negotiation(cmd, opt)
                    i += 3
                elif cmd == SB:
                    # Skip subnegotiation
                    end = data.find(IAC + SE, i)
                    if end == -1:
                        break
                    i = end + 2
                else:
                    i += 2
            else:
                out.append(data[i])
                i += 1
        return bytes(out)

    def _handle_negotiation(self, cmd: bytes, opt: bytes) -> None:
        """Respond to telnet option negotiations.

        We agree to SUPPRESS_GO_AHEAD and ECHO (let server echo).
        Everything else is refused.
        """
        if self._sock is None:
            return

        if cmd == DO:
            if opt == SUPPRESS_GO_AHEAD:
                self._send_negotiation(WILL, opt)
            else:
                self._send_negotiation(WONT, opt)
        elif cmd == WILL:
            if opt in (SUPPRESS_GO_AHEAD, ECHO):
                self._send_negotiation(DO, opt)
            else:
                self._send_negotiation(DONT, opt)
        # DONT/WONT — just acknowledge
        elif cmd == DONT:
            self._send_negotiation(WONT, opt)
        elif cmd == WONT:
            self._send_negotiation(DONT, opt)

    def _send_negotiation(self, cmd: bytes, opt: bytes) -> None:
        try:
            self._sock.sendall(IAC + cmd + opt)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close()
