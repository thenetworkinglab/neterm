# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Command-line interface for neterm."""

import argparse
import sys

from neterm import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neterm",
        description="A lightweight terminal emulator for network elements.",
    )
    parser.add_argument(
        "--version", action="version", version=f"neterm {__version__}"
    )

    sub = parser.add_subparsers(dest="mode", help="Connection mode")

    # -- serial ---------------------------------------------------------
    ser = sub.add_parser("serial", help="Connect via RS-232 serial port")
    ser.add_argument("port", help="Serial port (e.g. /dev/ttyUSB0, /dev/cu.usbserial)")
    ser.add_argument(
        "-b", "--baud", type=int, default=9600,
        help="Baud rate (default: 9600)",
    )
    ser.add_argument(
        "--bits", type=int, choices=[5, 6, 7, 8], default=8,
        help="Data bits (default: 8)",
    )
    ser.add_argument(
        "--parity", choices=["N", "E", "O", "M", "S"], default="N",
        help="Parity: N(one), E(ven), O(dd), M(ark), S(pace) (default: N)",
    )
    ser.add_argument(
        "--stop", type=float, choices=[1, 1.5, 2], default=1,
        help="Stop bits (default: 1)",
    )
    ser.add_argument(
        "--rtscts", action="store_true",
        help="Enable RTS/CTS hardware flow control",
    )
    ser.add_argument(
        "--xonxoff", action="store_true",
        help="Enable XON/XOFF software flow control",
    )

    # -- telnet ---------------------------------------------------------
    tel = sub.add_parser("telnet", help="Connect via telnet")
    tel.add_argument("host", help="Hostname or IP address")
    tel.add_argument(
        "-p", "--port", type=int, default=23,
        help="TCP port (default: 23)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode is None:
        parser.print_help()
        sys.exit(0)

    # Lazy imports so --help is fast
    from neterm.terminal import Terminal

    if args.mode == "serial":
        from neterm.connections.serial import SerialConnection
        import serial

        bytesize_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
        parity_map = {
            "N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        stopbits_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}

        conn = SerialConnection(
            port=args.port,
            baudrate=args.baud,
            bytesize=bytesize_map[args.bits],
            parity=parity_map[args.parity],
            stopbits=stopbits_map[args.stop],
            rtscts=args.rtscts,
            xonxoff=args.xonxoff,
        )

    elif args.mode == "telnet":
        from neterm.connections.telnet import TelnetConnection

        conn = TelnetConnection(host=args.host, port=args.port)

    else:
        parser.print_help()
        sys.exit(1)

    term = Terminal(conn)
    try:
        term.run()
    except KeyboardInterrupt:
        pass
