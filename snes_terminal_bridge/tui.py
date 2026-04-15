import curses


class TUI:
    """Curses-based display for the bridge.

    Layout:
      - Header bar (top)
      - Scrolling log of key → [BUTTON, …] entries (middle)
      - Status bar (bottom)
    """

    HEADER = "snes-terminal-bridge  |  Ctrl+C to quit"
    STATUS = "Waiting for input..."

    def __init__(self, stdscr):
        self._scr = stdscr
        self._log: list[str] = []

        curses.curs_set(0)  # hide cursor
        curses.use_default_colors()

        self._scr.clear()
        self._draw()

    def update(self, key: str, buttons: list[str] | None, keys: list[str] | None = None) -> None:
        """Add a keypress entry to the log and redraw."""
        if buttons and keys:
            line = f"  {key!r:<12} →  [{', '.join(buttons)}]  →  {'+'.join(keys)}"
        elif buttons:
            line = f"  {key!r:<12} →  [{', '.join(buttons)}]"
        else:
            line = f"  {key!r:<12} →  (no mapping)"
        self._log.append(line)
        self._draw()

    def _draw(self) -> None:
        h, w = self._scr.getmaxyx()
        self._scr.erase()

        # Header
        self._scr.addnstr(0, 0, self.HEADER.ljust(w), w, curses.A_REVERSE)

        # Log area: rows 1 .. h-2
        log_rows = h - 2
        visible = self._log[-log_rows:]
        for i, line in enumerate(visible):
            self._scr.addnstr(1 + i, 0, line, w - 1)

        # Status bar — capped at w-1 to avoid cursor-past-edge error
        self._scr.addnstr(h - 1, 0, self.STATUS.ljust(w), w - 1, curses.A_REVERSE)

        self._scr.refresh()
