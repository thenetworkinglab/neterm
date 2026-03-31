# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Connection backends for neterm."""

from neterm.connections.base import Connection
from neterm.connections.serial import SerialConnection
from neterm.connections.telnet import TelnetConnection

__all__ = ["Connection", "SerialConnection", "TelnetConnection"]
