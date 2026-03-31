# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Curses-based terminal UI for neterm.

Layout (nano-style):
  ┌─────────────────────────────────────────┐
  │  neterm 0.1.0  serial:/dev/ttyUSB0 9600 │  <- title bar (reverse video)
  │                                         │
  │  ... terminal output ...                │  <- scrollable terminal area
  │                                         │
  │  CTS:● DSR:● DCD:○ RI:○  RTS:● DTR:●  │  <- signal bar (serial only)
  │  ^X Exit  ^L Clear  ^B Break  ^T Toggle │  <- help bar (reverse video)
  └─────────────────────────────────────────┘
"""

import curses
import os
import threading
import time
from datetime import datetime
from typing import Optional

from neterm import __version__
from neterm.connections.base import Connection


# How often (seconds) to poll for incoming data and refresh signals
POLL_INTERVAL = 0.02  # 50 Hz


class Terminal:
    """Main curses terminal UI."""

    def __init__(self, connection: Connection, log_dir: Optional[str] = None):
        self.conn = connection
        self._running = False
        self._screen: Optional[curses.window] = None
        self._term_win: Optional[curses.window] = None
        self._lock = threading.Lock()
        # Scrollback buffer: list of lines (bytes decoded to str)
        self._lines: list[str] = [""]
        self._scroll_offset = 0  # line index of the top of the viewport
        self._scroll_mode = False  # True when user is browsing scrollback
        self._mouse_scroll = False  # mouse scroll off by default (text selection works)
        # Session log
        self._log_dir = log_dir or os.path.join(os.path.expanduser("~"), ".neterm", "logs")
        self._log_file: Optional[object] = None
        # Track Tx/Rx activity for blinking indicators
        self._rx_active = False
        self._tx_active = False
        self._rx_time = 0.0
        self._tx_time = 0.0
        self._activity_duration = 0.15  # seconds to keep indicator lit

    def run(self) -> None:
        """Start the terminal UI (blocks until exit)."""
        curses.wrapper(self._main)

    def _main(self, stdscr: curses.window) -> None:
        self._screen = stdscr
        curses.curs_set(1)
        stdscr.nodelay(True)
        stdscr.keypad(True)

        # Set up color pairs
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)   # title/help bars
            curses.init_pair(2, curses.COLOR_GREEN, -1)                   # signal ON
            curses.init_pair(3, curses.COLOR_RED, -1)                     # signal OFF
            curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)   # signal bar bg
            curses.init_pair(5, curses.COLOR_GREEN, curses.COLOR_BLACK)   # signal ON on bar
            curses.init_pair(6, curses.COLOR_RED, curses.COLOR_BLACK)     # signal OFF on bar

        # Enable mouse events (scroll wheel)
        # Mouse scroll starts disabled so native text selection works
        curses.mousemask(0)

        try:
            self.conn.open()
        except Exception as e:
            stdscr.addstr(0, 0, f"Connection failed: {e}")
            stdscr.addstr(1, 0, "Press any key to exit.")
            stdscr.nodelay(False)
            stdscr.getch()
            return

        self._running = True

        # Start reader thread
        reader = threading.Thread(target=self._reader_thread, daemon=True)
        reader.start()

        try:
            self._event_loop()
        finally:
            self._running = False
            self.conn.close()
            if self._log_file:
                self._close_log()

    def _open_log(self) -> None:
        os.makedirs(self._log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize connection name for the filename
        safe_name = self.conn.name.replace("/", "_").replace(":", "_").replace(" ", "_")
        log_path = os.path.join(self._log_dir, f"{timestamp}_{safe_name}.log")
        self._log_file = open(log_path, "a", encoding="utf-8")
        self._log_file.write(f"--- neterm session: {self.conn.name} ---\n")
        self._log_file.write(f"--- started: {datetime.now().isoformat()} ---\n")
        self._log_file.flush()
        self._log_path = log_path

    def _close_log(self) -> None:
        if self._log_file:
            self._log_file.write(f"\n--- ended: {datetime.now().isoformat()} ---\n")
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    def _log(self, text: str) -> None:
        if self._log_file:
            self._log_file.write(text)
            self._log_file.flush()

    def _reader_thread(self) -> None:
        """Background thread that reads from the connection into the buffer."""
        while self._running:
            try:
                data = self.conn.read(4096)
            except Exception:
                break

            if data:
                now = time.monotonic()
                with self._lock:
                    self._rx_active = True
                    self._rx_time = now
                    self._process_incoming(data)

            time.sleep(POLL_INTERVAL)

    def _process_incoming(self, data: bytes) -> None:
        """Decode incoming bytes and append to the line buffer.

        Handles CR, LF, CR+LF, and basic backspace.
        """
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("latin-1", errors="replace")

        self._log(text)

        for ch in text:
            if ch == "\r":
                continue  # handled by \n or standalone CR
            elif ch == "\n":
                self._lines.append("")
            elif ch == "\b" or ch == "\x7f":
                if self._lines[-1]:
                    self._lines[-1] = self._lines[-1][:-1]
            elif ch == "\t":
                self._lines[-1] += "    "
            elif ord(ch) >= 32 or ch in ("\x1b",):
                # Printable or escape (we pass through raw for now)
                self._lines[-1] += ch

        # Limit scrollback to ~10000 lines
        max_lines = 10000
        if len(self._lines) > max_lines:
            excess = len(self._lines) - max_lines
            self._lines = self._lines[excess:]

    def _event_loop(self) -> None:
        """Main input/render loop."""
        while self._running:
            self._handle_input()
            self._draw()
            # Decay activity indicators
            now = time.monotonic()
            if self._rx_active and now - self._rx_time > self._activity_duration:
                self._rx_active = False
            if self._tx_active and now - self._tx_time > self._activity_duration:
                self._tx_active = False

            curses.napms(int(POLL_INTERVAL * 1000))

    def _scroll_up(self, lines: int) -> None:
        """Scroll up by `lines`. Clamps at the top of the buffer."""
        h = self._get_term_height()
        with self._lock:
            if not self._scroll_mode:
                # Enter scroll mode: start from the bottom
                self._scroll_mode = True
                bottom = max(0, len(self._lines) - h)
                self._scroll_offset = max(0, bottom - lines)
            else:
                self._scroll_offset = max(0, self._scroll_offset - lines)

    def _scroll_down(self, lines: int) -> None:
        """Scroll down by `lines`. Snaps back to follow mode at the bottom."""
        h = self._get_term_height()
        with self._lock:
            if not self._scroll_mode:
                return  # already at bottom, nothing to do
            bottom = max(0, len(self._lines) - h)
            self._scroll_offset = min(bottom, self._scroll_offset + lines)
            if self._scroll_offset >= bottom:
                # Reached the bottom — exit scroll mode
                self._scroll_mode = False
                self._scroll_offset = 0

    def _handle_input(self) -> None:
        """Process keyboard input."""
        try:
            key = self._screen.getch()
        except curses.error:
            return

        if key == -1:
            return

        # Ctrl+X — exit
        if key == 24:  # ^X
            self._running = False
            return

        # Ctrl+L — clear screen buffer
        if key == 12:  # ^L
            with self._lock:
                self._lines = [""]
                self._scroll_offset = 0
                self._scroll_mode = False
            return

        # Ctrl+B — send break (serial only)
        if key == 2:  # ^B
            if hasattr(self.conn, "send_break"):
                self.conn.send_break()
            return

        # Ctrl+G — toggle mouse scroll (off = native text selection works)
        if key == 7:  # ^G
            self._mouse_scroll = not self._mouse_scroll
            if self._mouse_scroll:
                curses.mousemask(curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED)
            else:
                curses.mousemask(0)
            return

        # Ctrl+O — toggle session logging on/off
        if key == 15:  # ^O
            if self._log_file:
                self._close_log()
            else:
                self._open_log()
            return

        # Page Up / Page Down for scrollback
        if key == curses.KEY_PPAGE:
            self._scroll_up(self._get_term_height())
            return

        if key == curses.KEY_NPAGE:
            self._scroll_down(self._get_term_height())
            return

        # Home — scroll to top
        if key == curses.KEY_HOME:
            with self._lock:
                self._scroll_offset = 0
                self._scroll_mode = True
            return

        # End — snap back to follow mode
        if key == curses.KEY_END:
            with self._lock:
                self._scroll_offset = 0
                self._scroll_mode = False
            return

        # Mouse scroll
        if key == curses.KEY_MOUSE:
            try:
                _, _, _, _, bstate = curses.getmouse()
                scroll_lines = 3  # lines per scroll tick
                if bstate & curses.BUTTON4_PRESSED:  # scroll up
                    self._scroll_up(scroll_lines)
                elif bstate & curses.BUTTON5_PRESSED:  # scroll down
                    self._scroll_down(scroll_lines)
            except curses.error:
                pass
            return

        # Backspace — curses may report as KEY_BACKSPACE (263), 127, or 8
        if key in (curses.KEY_BACKSPACE, 127, 8):
            try:
                self.conn.write(b"\x7f")
                self._tx_active = True
                self._tx_time = time.monotonic()
            except Exception:
                pass
            return

        # Regular key — send to connection
        if 0 <= key <= 255:
            ch = bytes([key])
            # Translate Enter to CR (standard for serial terminals)
            if key in (curses.KEY_ENTER, 10, 13):
                ch = b"\r"
            try:
                self.conn.write(ch)
                self._tx_active = True
                self._tx_time = time.monotonic()
            except Exception:
                pass

    def _get_term_height(self) -> int:
        """Height available for the terminal text area."""
        rows, _ = self._screen.getmaxyx()
        # title bar (1) + signal bar (1 if serial) + help bar (1)
        chrome = 2  # title + help
        if self.conn.get_signals() is not None:
            chrome += 1
        return max(1, rows - chrome)

    def _draw(self) -> None:
        """Redraw the entire screen."""
        try:
            self._screen.erase()
            rows, cols = self._screen.getmaxyx()
            if rows < 3 or cols < 20:
                return

            has_signals = self.conn.get_signals() is not None

            # Row allocation
            title_row = 0
            term_start = 1
            if has_signals:
                signal_row = rows - 2
                help_row = rows - 1
                term_height = rows - 3  # title + signal + help
            else:
                signal_row = -1
                help_row = rows - 1
                term_height = rows - 2  # title + help

            self._draw_title_bar(title_row, cols)
            self._draw_terminal(term_start, term_height, cols)
            if has_signals:
                self._draw_signal_bar(signal_row, cols)
            self._draw_help_bar(help_row, cols)

            # Position cursor at the end of the last visible line
            with self._lock:
                total = len(self._lines)
                if not self._scroll_mode:
                    visible_lines = min(total, term_height)
                    cursor_row = term_start + visible_lines - 1
                    cursor_col = len(self._lines[-1]) if self._lines else 0
                else:
                    cursor_row = term_start
                    cursor_col = 0
            cursor_col = min(cursor_col, cols - 1)
            cursor_row = min(cursor_row, rows - 1)
            try:
                self._screen.move(cursor_row, cursor_col)
            except curses.error:
                pass

            self._screen.refresh()
        except curses.error:
            pass

    def _draw_title_bar(self, row: int, cols: int) -> None:
        title = f" neterm {__version__}  {self.conn.name} "
        if not self.conn.is_open:
            title += " [DISCONNECTED]"
        if self._log_file:
            title += f" [LOG: {os.path.basename(self._log_path)}]"
        if self._scroll_mode:
            title += " [SCROLL]"
        title = title.ljust(cols)[:cols]
        try:
            self._screen.addstr(row, 0, title[:cols - 1], curses.color_pair(1))
        except curses.error:
            pass

    def _draw_terminal(self, start_row: int, height: int, cols: int) -> None:
        with self._lock:
            total = len(self._lines)
            if not self._scroll_mode:
                # Follow mode: show the last `height` lines
                begin = max(0, total - height)
            else:
                begin = self._scroll_offset
            end = min(begin + height, total)
            visible = self._lines[begin:end]

        for i, line in enumerate(visible):
            row = start_row + i
            if row >= start_row + height:
                break
            # Truncate long lines
            display = line[:cols]
            try:
                self._screen.addstr(row, 0, display)
            except curses.error:
                pass

    def _draw_signal_bar(self, row: int, cols: int) -> None:
        """Draw RS-232 signal status bar."""
        signals = self.conn.get_signals()

        bar_attr = curses.color_pair(4)

        # Fill the bar background
        try:
            self._screen.addstr(row, 0, " " * (cols - 1), bar_attr)
        except curses.error:
            pass

        # Build signal display
        # Activity indicators first, then hardware signals
        parts = []

        # Rx/Tx activity (blinky indicators)
        rx_on = self._rx_active
        tx_on = self._tx_active
        parts.append(("Rx", rx_on))
        parts.append(("Tx", tx_on))

        if signals:
            for name in ("RTS", "DTR", "CTS", "DSR", "DCD", "RI"):
                if name in signals:
                    parts.append((name, signals[name]))

        col = 1
        for label, active in parts:
            if active:
                indicator = "\u25cf"  # filled circle ●
                attr = curses.color_pair(5) | curses.A_BOLD
            else:
                indicator = "\u25cb"  # empty circle ○
                attr = curses.color_pair(6)

            text = f"{label}:{indicator} "
            try:
                # Label part
                self._screen.addstr(row, col, f"{label}:", bar_attr)
                col += len(label) + 1
                # Indicator
                self._screen.addstr(row, col, indicator, attr)
                col += 1
                # Space
                self._screen.addstr(row, col, " ", bar_attr)
                col += 1
            except curses.error:
                pass

    def _draw_help_bar(self, row: int, cols: int) -> None:
        mouse_label = "Mouse:ON" if self._mouse_scroll else "Mouse:OFF"
        log_label = "Log:ON" if self._log_file else "Log:OFF"
        help_items = [
            ("^X", "Exit"),
            ("^L", "Clear"),
            ("^B", "Break"),
            ("^G", mouse_label),
            ("^O", log_label),
            ("PgUp/Dn", "Scroll"),
        ]
        help_text = "  ".join(f"{k} {v}" for k, v in help_items)
        help_text = " " + help_text
        help_text = help_text.ljust(cols)[:cols]
        try:
            self._screen.addstr(row, 0, help_text[:cols - 1], curses.color_pair(1))
        except curses.error:
            pass
