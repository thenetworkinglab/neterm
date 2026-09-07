# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Serial port connection using pyserial."""

from typing import Optional

import serial

from neterm.connections.base import Connection


class SerialConnection(Connection):
    """RS-232 serial connection via pyserial."""

    DEFAULT_BAUD = 9600
    DEFAULT_BYTESIZE = serial.EIGHTBITS
    DEFAULT_PARITY = serial.PARITY_NONE
    DEFAULT_STOPBITS = serial.STOPBITS_ONE

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUD,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: float = DEFAULT_STOPBITS,
        rtscts: bool = False,
        xonxoff: bool = False,
    ):
        self._port = port
        self._baudrate = baudrate
        self._serial = serial.Serial()
        self._serial.port = port
        self._serial.baudrate = baudrate
        self._serial.bytesize = bytesize
        self._serial.parity = parity
        self._serial.stopbits = stopbits
        self._serial.rtscts = rtscts
        self._serial.xonxoff = xonxoff
        self._serial.timeout = 0  # non-blocking reads

    def open(self) -> None:
        if not self._serial.is_open:
            self._serial.open()

    def close(self) -> None:
        if self._serial.is_open:
            self._serial.close()

    def read(self, size: int = 1024) -> bytes:
        if self._serial.is_open and self._serial.in_waiting:
            return self._serial.read(min(size, self._serial.in_waiting))
        return b""

    def read_wait(self, size: int = 1024, timeout: float = 0.02) -> bytes:
        """Block on the port (up to `timeout`) and return what arrived.

        Uses the driver's own blocking read, so bytes are delivered the
        moment they exist instead of on a polling grid.
        """
        if not self._serial.is_open:
            return b""
        if self._serial.timeout != timeout:
            self._serial.timeout = timeout
        data = self._serial.read(1)
        if not data:
            return b""
        waiting = self._serial.in_waiting
        if waiting:
            data += self._serial.read(min(size - 1, waiting))
        return data

    def write(self, data: bytes) -> None:
        if self._serial.is_open:
            self._serial.write(data)

    @property
    def is_open(self) -> bool:
        return self._serial.is_open

    @property
    def name(self) -> str:
        return f"{self._port} @ {self._baudrate}bps"

    def get_signals(self) -> Optional[dict]:
        if not self._serial.is_open:
            return None
        try:
            return {
                "CTS": self._serial.cts,
                "DSR": self._serial.dsr,
                "DCD": self._serial.cd,
                "RI": self._serial.ri,
                "RTS": self._serial.rts,
                "DTR": self._serial.dtr,
            }
        except (OSError, serial.SerialException):
            return None

    def set_rts(self, state: bool) -> None:
        self._serial.rts = state

    def set_dtr(self, state: bool) -> None:
        self._serial.dtr = state

    def send_break(self, duration: float = 0.25) -> None:
        self._serial.send_break(duration)
