# Changelog

All notable changes to neterm are documented in this file.

## [0.2.2] - 2026-09-07

### Fixed

- UI stutter on (USB-)serial connections: modem signals were queried
  from the draw loop ~100×/second, each query a batch of driver ioctls
  that can block for milliseconds. The reader thread now polls them 5×/
  second into a cache the UI reads. The signal bar looks and behaves
  the same.
- Choppy output pacing: incoming data was picked up on a 20 ms polling
  grid and drawn on another, adding 0–40 ms of random latency per line.
  The reader now blocks on the device itself (new `read_wait` on
  connections) and the UI draws at 100 Hz, cutting line-arrival jitter
  to ~7 ms.

## [0.2.1] - 2026-09-06

### Fixed

- Crash on `^G` (mouse scroll toggle) with Python builds whose curses
  lacks `BUTTON5_PRESSED` (ncurses mouse protocol v1, e.g. python.org
  macOS builds). Mouse scroll-up still works there; scroll-down isn't
  reported by such builds — use PgDn.

## [0.2.0] - 2026-09-06

### Added

- VT100/VT102 terminal emulation via [pyte](https://github.com/selectel/pyte):
  full-screen applications (e.g. the Solaris installer, `vi`, curses menus)
  now render correctly, with cursor addressing, screen/line erasing,
  reverse video, bold, underline, and color support.
- VT100 line-drawing charset (`ESC(0`, SI/SO): box borders render as
  proper line characters instead of `q`/`l`/`k` letters.
- Erase operations (EL/ED/ECH) clear to plain default background like a
  real VT100, instead of inheriting the active attributes (which left
  reverse-video blocks in blank areas after clears).
- Arrow keys, F1–F12, Insert, and Delete are sent as VT100 escape sequences.
  Arrows default to the application (SS3) form `ESC O A`…`D` that the
  vt100 terminfo entry advertises — hosts like the Solaris installer
  match it literally without ever sending DECCKM — and follow explicit
  DECCKM changes (`ESC[?1h` / `ESC[?1l`) from the host.
- The emulator answers host device queries (`ESC[c` device attributes,
  `ESC[6n` cursor position report).
- Snappier ESC key handling (25 ms delay) for hosts that use Esc-digit
  key chords.
- Raw keyboard mode: `^C`, `^Z`, `^S`/`^Q`, and other control characters
  are forwarded to the host instead of acting on neterm itself. `^X`
  remains the local exit key.

### Changed

- Scrollback now uses the emulator's history: PgUp/PgDn page through it,
  End snaps back to the live screen; incoming data is held while browsing
  and replayed on return.
- `^L` resets the emulated screen and scrollback instead of just clearing
  the line buffer.

## [0.1.0]

Initial release.

- Curses UI with title, signal, and help bars.
- Serial (RS-232) connections via pyserial, with baud/parity/stop-bit
  options, flow control, break, and live modem signal indicators.
- Telnet connections.
- Session logging, scrollback buffer, mouse scroll toggle.
