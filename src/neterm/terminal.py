# This file is part of neterm.
# Copyright (C) 2026 thenetworkinglab
# License: GPLv3+ — see LICENSE for details.

"""Curses-based terminal UI for neterm.

Layout (nano-style):
  ┌─────────────────────────────────────────┐
  │  neterm 0.2.1  serial:/dev/ttyUSB0 9600 │  <- title bar (reverse video)
  │                                         │
  │  ... terminal output ...                │  <- VT100 screen area
  │                                         │
  │  CTS:● DSR:● DCD:○ RI:○  RTS:● DTR:●  │  <- signal bar (serial only)
  │  ^X Exit  ^L Clear  ^B Break  ^T Toggle │  <- help bar (reverse video)
  └─────────────────────────────────────────┘

Terminal emulation is provided by pyte (VT100/VT102 + parts of VT220):
incoming bytes are fed to a pyte ByteStream which maintains a screen
grid with cursor position and per-character attributes; we paint that
grid into the curses window. Outgoing special keys (arrows, function
keys, etc.) are encoded as VT100 escape sequences.
"""

import curses
import locale
import os
import threading
import time
from datetime import datetime
from typing import Optional

import pyte

from neterm import __version__
from neterm.connections.base import Connection


# How often (seconds) to poll for incoming data and refresh signals
POLL_INTERVAL = 0.02  # 50 Hz

# Scrollback history (lines)
HISTORY_LINES = 10000

# While browsing scrollback we hold incoming data so pyte's history
# pagination isn't corrupted mid-view; cap how much we hold.
MAX_PENDING = 1024 * 1024

# Missing when Python's curses is linked against an ncurses with mouse
# protocol v1 (e.g. python.org macOS builds): no scroll-wheel-down
# constant exists there, so mouse scroll-down is unavailable.
BUTTON5_PRESSED = getattr(curses, "BUTTON5_PRESSED", 0)

# pyte color name -> curses color constant
COLOR_MAP = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "brown": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
}

# DECCKM: private mode 1, application cursor keys (ESC[?1h / ESC[?1l)
DECCKM = 1

# Arrow keys: final byte is shared, the prefix depends on DECCKM
# (normal: ESC[A, application mode: ESC OA — set by full-screen hosts).
ARROW_KEYS = {
    curses.KEY_UP: b"A",
    curses.KEY_DOWN: b"B",
    curses.KEY_RIGHT: b"C",
    curses.KEY_LEFT: b"D",
}
ARROW_FINALS = (b"A", b"B", b"C", b"D")

# curses key -> VT100 escape sequence
KEY_SEQUENCES = {
    curses.KEY_DC: b"\x1b[3~",
    curses.KEY_IC: b"\x1b[2~",
    curses.KEY_F1: b"\x1bOP",
    curses.KEY_F2: b"\x1bOQ",
    curses.KEY_F3: b"\x1bOR",
    curses.KEY_F4: b"\x1bOS",
    curses.KEY_F5: b"\x1b[15~",
    curses.KEY_F6: b"\x1b[17~",
    curses.KEY_F7: b"\x1b[18~",
    curses.KEY_F8: b"\x1b[19~",
    curses.KEY_F9: b"\x1b[20~",
    curses.KEY_F10: b"\x1b[21~",
    curses.KEY_F11: b"\x1b[23~",
    curses.KEY_F12: b"\x1b[24~",
}


class _Vt100Screen(pyte.HistoryScreen):
    """pyte screen that can answer host queries (DA/DSR) over the connection."""

    def __init__(self, columns: int, lines: int, respond) -> None:
        super().__init__(columns, lines, history=HISTORY_LINES)
        self._respond = respond
        # Cursor key encoding: the vt100 terminfo entry advertises the
        # application (SS3, ESC O x) form, and hosts like the Solaris
        # installer match it literally without ever sending DECCKM — so
        # SS3 is the default until the host explicitly resets the mode.
        self.cursor_keys_app = True

    def write_process_input(self, data: str) -> None:
        # Cursor-position / device-attribute reports requested by the host
        self._respond(data.encode("ascii", errors="ignore"))

    # pyte fills erased cells with the *current* SGR attributes (BCE
    # semantics), so e.g. clearing while reverse video is active leaves
    # reverse-video blanks. A real VT100 has no back-color-erase: erased
    # cells always revert to plain default background.

    def _erase_with_default_attrs(self, op, *args, **kwargs) -> None:
        saved = self.cursor.attrs
        self.cursor.attrs = self.default_char
        try:
            op(*args, **kwargs)
        finally:
            self.cursor.attrs = saved

    def erase_in_line(self, *args, **kwargs) -> None:
        self._erase_with_default_attrs(super().erase_in_line, *args, **kwargs)

    def erase_in_display(self, *args, **kwargs) -> None:
        self._erase_with_default_attrs(super().erase_in_display, *args, **kwargs)

    def erase_characters(self, *args, **kwargs) -> None:
        self._erase_with_default_attrs(super().erase_characters, *args, **kwargs)

    def set_mode(self, *modes, **kwargs) -> None:
        super().set_mode(*modes, **kwargs)
        if kwargs.get("private") and DECCKM in modes:
            self.cursor_keys_app = True

    def reset_mode(self, *modes, **kwargs) -> None:
        super().reset_mode(*modes, **kwargs)
        if kwargs.get("private") and DECCKM in modes:
            self.cursor_keys_app = False


class Terminal:
    """Main curses terminal UI."""

    def __init__(self, connection: Connection, log_dir: Optional[str] = None):
        self.conn = connection
        self._running = False
        self._screen: Optional[curses.window] = None
        self._lock = threading.Lock()
        # VT100 emulation (created once curses is up and we know our size)
        self._vt: Optional[_Vt100Screen] = None
        self._stream: Optional[pyte.ByteStream] = None
        self._repaint = True  # full repaint of the terminal area needed
        self._scroll_mode = False  # True when user is browsing scrollback
        self._pending = bytearray()  # data held while browsing scrollback
        self._mouse_scroll = False  # mouse scroll off by default (text selection works)
        # (fg, bg) -> curses color pair number, allocated lazily
        self._pairs: dict = {}
        self._next_pair = 10  # 1-6 are reserved for the chrome
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
        # Needed so curses can output the Unicode box-drawing characters
        # produced by the VT100 line-drawing charset.
        locale.setlocale(locale.LC_ALL, "")
        # Don't make the user wait after pressing bare ESC (default 1000ms);
        # hosts like the Solaris installer use ESC-digit key chords.
        os.environ.setdefault("ESCDELAY", "25")
        curses.wrapper(self._main)

    def _main(self, stdscr: curses.window) -> None:
        self._screen = stdscr
        curses.curs_set(1)
        # Raw mode: deliver ^C, ^Z, ^S/^Q etc. as input bytes to forward to
        # the host instead of signalling neterm. ^X stays the local exit key.
        curses.raw()
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

        # Create the VT100 screen sized to the visible terminal area.
        # Must happen after open(): whether the signal bar exists (and thus
        # how tall the terminal area is) depends on the live connection.
        _, cols = stdscr.getmaxyx()
        self._vt = _Vt100Screen(max(2, cols), self._get_term_height(), self._respond)
        self._stream = pyte.ByteStream(self._vt)
        # pyte's UTF-8 mode ignores VT100 charset switching (ESC(0, SI/SO),
        # which full-screen hosts use for line-drawing. A real VT100 isn't
        # UTF-8 anyway; hosts can re-enable it with ESC%G.
        self._stream.use_utf8 = False

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

    def _respond(self, data: bytes) -> None:
        """Send an emulator-generated reply (DA/DSR reports) to the host."""
        try:
            self.conn.write(data)
        except Exception:
            pass

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
        """Background thread that reads from the connection into the emulator."""
        while self._running:
            try:
                data = self.conn.read(4096)
            except Exception:
                break

            if data:
                now = time.monotonic()
                self._log(data.decode("utf-8", errors="replace"))
                with self._lock:
                    self._rx_active = True
                    self._rx_time = now
                    if self._scroll_mode:
                        # Hold data while the user browses scrollback
                        if len(self._pending) < MAX_PENDING:
                            self._pending.extend(data)
                    else:
                        self._stream.feed(data)
                        self._repaint = True

            time.sleep(POLL_INTERVAL)

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

    def _at_bottom(self) -> bool:
        return self._vt.history.position >= self._vt.history.size

    def _scroll_up(self) -> None:
        with self._lock:
            self._scroll_mode = True
            self._vt.prev_page()
            self._repaint = True

    def _scroll_down(self) -> None:
        with self._lock:
            if not self._scroll_mode:
                return
            self._vt.next_page()
            if self._at_bottom():
                self._exit_scroll_locked()
            self._repaint = True

    def _exit_scroll_locked(self) -> None:
        """Snap back to the live screen. Caller must hold the lock."""
        while not self._at_bottom():
            self._vt.next_page()
        self._scroll_mode = False
        if self._pending:
            self._stream.feed(bytes(self._pending))
            self._pending.clear()
        self._repaint = True

    def _send_arrow(self, final: bytes) -> None:
        """Send an arrow key, honoring the host's DECCKM cursor key mode."""
        with self._lock:
            app_mode = self._vt.cursor_keys_app
        self._send((b"\x1bO" if app_mode else b"\x1b[") + final)

    def _read_escape_tail(self) -> bytes:
        """Collect the bytes following a bare ESC from the keyboard.

        Returns b"" for a lone ESC press, a single byte for an Alt/Esc
        chord (e.g. Esc-2), or a full CSI/SS3 sequence tail like b"[B".
        """
        deadline = time.monotonic() + 0.05
        tail = bytearray()
        while time.monotonic() < deadline:
            ch = self._screen.getch()
            if ch == -1:
                curses.napms(2)
                continue
            if ch > 255:  # curses special key; not part of a byte sequence
                curses.ungetch(ch)
                break
            tail.append(ch)
            if tail[0] == ord("["):
                if len(tail) > 1 and 0x40 <= ch <= 0x7E:  # CSI final byte
                    break
            elif tail[0] == ord("O"):
                if len(tail) > 1:  # SS3 is one byte after 'O'
                    break
            else:  # Esc-<char> chord — done
                break
        return bytes(tail)

    def _send(self, data: bytes) -> None:
        try:
            self.conn.write(data)
            self._tx_active = True
            self._tx_time = time.monotonic()
        except Exception:
            pass

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

        # Ctrl+L — reset emulator screen and scrollback
        if key == 12:  # ^L
            with self._lock:
                self._vt.reset()
                self._vt.history.top.clear()
                self._vt.history.bottom.clear()
                self._pending.clear()
                self._scroll_mode = False
                self._repaint = True
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
                curses.mousemask(curses.BUTTON4_PRESSED | BUTTON5_PRESSED)
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

        # Terminal resize
        if key == curses.KEY_RESIZE:
            with self._lock:
                _, cols = self._screen.getmaxyx()
                self._vt.resize(self._get_term_height(), max(2, cols))
                self._repaint = True
            return

        # Page Up / Page Down for scrollback
        if key == curses.KEY_PPAGE:
            self._scroll_up()
            return

        if key == curses.KEY_NPAGE:
            self._scroll_down()
            return

        # End — snap back to the live screen
        if key == curses.KEY_END:
            with self._lock:
                if self._scroll_mode:
                    self._exit_scroll_locked()
            return

        # Mouse scroll
        if key == curses.KEY_MOUSE:
            try:
                _, _, _, _, bstate = curses.getmouse()
                if bstate & curses.BUTTON4_PRESSED:  # scroll up
                    self._scroll_up()
                elif BUTTON5_PRESSED and bstate & BUTTON5_PRESSED:  # scroll down
                    self._scroll_down()
            except curses.error:
                pass
            return

        # Arrow keys — prefix depends on the host-set cursor key mode
        final = ARROW_KEYS.get(key)
        if final is not None:
            self._send_arrow(final)
            return

        # Bare ESC — could be a lone ESC press, an Esc-digit chord, or an
        # arrow/function sequence our outer terminal's terminfo didn't match
        if key == 27:
            rest = self._read_escape_tail()
            if len(rest) == 2 and rest[0] in b"[O" and rest[1:] in ARROW_FINALS:
                self._send_arrow(rest[1:])
            else:
                self._send(b"\x1b" + rest)
            return

        # Other special keys — encode as VT100 sequences
        seq = KEY_SEQUENCES.get(key)
        if seq is not None:
            self._send(seq)
            return

        # Backspace — curses may report as KEY_BACKSPACE (263), 127, or 8
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self._send(b"\x7f")
            return

        # Regular key — send to connection
        if 0 <= key <= 255:
            ch = bytes([key])
            # Translate Enter to CR (standard for serial terminals)
            if key in (curses.KEY_ENTER, 10, 13):
                ch = b"\r"
            self._send(ch)

    def _get_term_height(self) -> int:
        """Height available for the terminal text area."""
        rows, _ = self._screen.getmaxyx()
        # title bar (1) + signal bar (1 if serial) + help bar (1)
        chrome = 2  # title + help
        if self.conn.get_signals() is not None:
            chrome += 1
        return max(1, rows - chrome)

    def _char_attr(self, char) -> int:
        """Map a pyte Char's attributes to a curses attribute."""
        attr = 0
        if char.bold:
            attr |= curses.A_BOLD
        if char.underscore:
            attr |= curses.A_UNDERLINE
        if char.reverse:
            attr |= curses.A_REVERSE
        if char.blink:
            attr |= curses.A_BLINK

        if curses.has_colors():
            fg = COLOR_MAP.get(char.fg.replace("bright", ""), -1)
            bg = COLOR_MAP.get(char.bg.replace("bright", ""), -1)
            if char.fg.startswith("bright"):
                attr |= curses.A_BOLD
            if (fg, bg) != (-1, -1):
                pair = self._pairs.get((fg, bg))
                if pair is None and self._next_pair < curses.COLOR_PAIRS:
                    pair = self._next_pair
                    try:
                        curses.init_pair(pair, fg, bg)
                        self._pairs[(fg, bg)] = pair
                        self._next_pair += 1
                    except curses.error:
                        pair = None
                if pair is not None:
                    attr |= curses.color_pair(pair)
        return attr

    def _draw(self) -> None:
        """Redraw the screen chrome every frame; terminal area when dirty."""
        try:
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

            # Position the cursor where the emulator says it is
            with self._lock:
                hidden = self._vt.cursor.hidden or self._scroll_mode
                cursor_row = term_start + self._vt.cursor.y
                cursor_col = self._vt.cursor.x
            try:
                curses.curs_set(0 if hidden else 1)
            except curses.error:
                pass
            if not hidden:
                try:
                    self._screen.move(
                        min(cursor_row, rows - 1), min(cursor_col, cols - 1)
                    )
                except curses.error:
                    pass

            self._screen.refresh()
        except curses.error:
            pass

    def _draw_title_bar(self, row: int, cols: int) -> None:
        title = f" neterm {__version__}  {self.conn.name}  VT100"
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
            if not (self._repaint or self._vt.dirty):
                return
            dirty_all = self._repaint
            dirty = set(self._vt.dirty)
            self._vt.dirty.clear()
            self._repaint = False

            buffer = self._vt.buffer
            vt_lines = min(self._vt.lines, height)
            for y in range(vt_lines):
                if not dirty_all and y not in dirty:
                    continue
                row = start_row + y
                line = buffer[y]
                # Draw runs of identical attributes with one addstr each
                x = 0
                while x < min(self._vt.columns, cols):
                    char = line[x]
                    attr = self._char_attr(char)
                    run = [char.data]
                    x2 = x + 1
                    while x2 < min(self._vt.columns, cols):
                        nxt = line[x2]
                        if self._char_attr(nxt) != attr:
                            break
                        run.append(nxt.data)
                        x2 += 1
                    try:
                        self._screen.addstr(row, x, "".join(run), attr)
                    except curses.error:
                        pass
                    x = x2
                # Clear anything to the right of the emulated screen
                if self._vt.columns < cols:
                    try:
                        self._screen.addstr(
                            row, self._vt.columns, " " * (cols - self._vt.columns - 1)
                        )
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
                indicator = "●"  # filled circle ●
                attr = curses.color_pair(5) | curses.A_BOLD
            else:
                indicator = "○"  # empty circle ○
                attr = curses.color_pair(6)

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
