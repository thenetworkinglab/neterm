# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Abstract base class for all connection types."""

import time
from abc import ABC, abstractmethod
from typing import Optional


class Connection(ABC):
    """Base class for serial, telnet, and future SSH connections."""

    @abstractmethod
    def open(self) -> None:
        """Open the connection."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection."""

    @abstractmethod
    def read(self, size: int = 1) -> bytes:
        """Read up to `size` bytes. Must be non-blocking (return b'' if nothing available)."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write data to the connection."""

    def read_wait(self, size: int = 1024, timeout: float = 0.02) -> bytes:
        """Read up to `size` bytes, waiting up to `timeout` for data.

        Subclasses should override with a real blocking wait on the
        underlying device so data is delivered the moment it arrives;
        this default just polls read() across one sleep.
        """
        data = self.read(size)
        if data:
            return data
        time.sleep(timeout)
        return self.read(size)

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True if the connection is currently open."""

    @property
    def name(self) -> str:
        """Human-readable connection description for the title bar."""
        return self.__class__.__name__

    def get_signals(self) -> Optional[dict]:
        """Return RS-232 signal states as a dict, or None if not applicable.

        Keys: CTS, DSR, DCD, RI, RTS, DTR
        Values: True (asserted) / False (deasserted)
        """
        return None
