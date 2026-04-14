import curses
import queue
import threading

# curses key code → KEY_* name used in mappings.yaml
CURSES_KEY_MAP = {
    curses.KEY_UP:        "KEY_UP",
    curses.KEY_DOWN:      "KEY_DOWN",
    curses.KEY_LEFT:      "KEY_LEFT",
    curses.KEY_RIGHT:     "KEY_RIGHT",
    curses.KEY_ENTER:     "KEY_ENTER",
    curses.KEY_BACKSPACE: "KEY_BACKSPACE",
    curses.KEY_DC:        "KEY_DELETE",
    curses.KEY_F1:        "KEY_F1",
    curses.KEY_F2:        "KEY_F2",
    curses.KEY_F3:        "KEY_F3",
    curses.KEY_F4:        "KEY_F4",
    # single-byte special chars
    ord("\n"):   "KEY_ENTER",
    ord("\r"):   "KEY_ENTER",
    ord("\t"):   "KEY_TAB",
    ord("\x1b"): "KEY_ESCAPE",
    0x7f:        "KEY_BACKSPACE",
}

# Ctrl+C (3) signals shutdown — not forwarded to the queue
_CTRL_C = 3


def capture_loop(
    stdscr,
    q: queue.Queue,
    stop: threading.Event,
) -> None:
    """Read keystrokes from a curses window and put them on a queue.

    Runs in a background thread. Each item placed on the queue is a string:
    - a single printable character (e.g. 'A', 'a', ' ')
    - a KEY_* name for special keys (e.g. 'KEY_UP', 'KEY_ENTER')

    Stops when stop is set or Ctrl+C is pressed.
    """
    stdscr.keypad(True)
    stdscr.nodelay(False)  # blocking read — no busy loop

    while not stop.is_set():
        try:
            code = stdscr.getch()
        except curses.error:
            continue

        if code == curses.ERR:
            continue

        if code == _CTRL_C:
            stop.set()
            break

        key = CURSES_KEY_MAP.get(code)
        if key is None and 32 <= code <= 126:
            key = chr(code)

        if key is not None:
            q.put(key)
