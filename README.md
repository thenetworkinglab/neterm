# neterm

A lightweight terminal emulator for network elements — a simple minicom replacement for connecting to routers, switches, and other gear via serial or telnet.

## Features

- **Serial (RS-232)** with full signal monitoring (CTS, DSR, DCD, RI, RTS, DTR, Rx/Tx activity)
- **Telnet** with basic option negotiation (no telnetlib dependency — works on Python 3.13+)
- Nano-style curses UI with title bar, scrollback, and help bar
- Scrollback buffer (10,000 lines) with keyboard and mouse scrolling
- Session logging to timestamped files
- Runs on macOS and Linux

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Linux, you may need to add your user to the `dialout` group for serial port access:

```bash
sudo usermod -aG dialout $USER
# Log out and back in for this to take effect
```

## Usage

### Serial

```bash
# Basic — 9600 8N1
neterm serial /dev/ttyUSB0

# Custom baud, with hardware flow control
neterm serial /dev/cu.usbserial-1420 -b 115200 --rtscts

# 7E1 (7 data bits, even parity, 1 stop bit)
neterm serial /dev/ttyS0 -b 9600 --bits 7 --parity E
```

### Telnet

```bash
neterm telnet 192.168.1.1
neterm telnet router.local -p 2323
```

## Keyboard Shortcuts

All keys — including control characters like `Ctrl+X` and `Ctrl+C` — are
sent to the host, so full-screen applications (nano, vi, installers)
work normally. Local commands hide behind the `Ctrl+]` prefix, telnet's
classic escape key, which host applications essentially never use.

| Key             | Action                                    |
|-----------------|-------------------------------------------|
| `Ctrl+]` `x`    | Exit                                      |
| `Ctrl+]` `l`    | Clear screen and scrollback               |
| `Ctrl+]` `b`    | Send break (serial only)                  |
| `Ctrl+]` `m`    | Toggle mouse scrolling on/off             |
| `Ctrl+]` `o`    | Toggle session logging on/off             |
| `Ctrl+]` `Ctrl+]` | Send a literal `Ctrl+]` to the host     |
| `PgUp`          | Scroll up                                 |
| `PgDn`          | Scroll down                               |
| `End`           | Snap to bottom (follow mode)              |

The command letter also works with Ctrl held (`Ctrl+]` `Ctrl+X` exits,
like plain `Ctrl+]` `x`). Any other key after the prefix cancels it.

Mouse scrolling is off by default so that native text selection works in your terminal. Toggle it on with `Ctrl+]` `m` when you need to scroll through output with the mouse wheel.

## Session Logging

Press `Ctrl+]` `o` to start logging. All session output is written to a timestamped file in `~/.neterm/logs/`. Press `Ctrl+]` `o` again to stop logging and close the file. Starting logging again creates a new file. The log file name is shown in the title bar while logging is active.

## Screen Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  neterm 0.3.0  /dev/ttyUSB0 @ 9600bps  [LOG: 20260331_...]      │  title bar
│                                                                 │
│  Router> show version                                           │  terminal
│  Cisco IOS Software ...                                         │  area
│                                                                 │
│  Rx:● Tx:○ RTS:● DTR:● CTS:● DSR:● DCD:○ RI:○                   │  signal bar
│  ^]x Exit  ^]l Clear  ^]b Break  ^]m Mouse  ^]o Log  PgUp/Dn    │  help bar
└─────────────────────────────────────────────────────────────────┘
```

The signal bar only appears for serial connections. Green filled circles indicate asserted signals; red empty circles indicate deasserted.

## License

This project is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
