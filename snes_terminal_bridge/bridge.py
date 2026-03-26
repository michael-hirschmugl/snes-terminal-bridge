import curses
import queue
import threading

from . import config, mapper
from .gamepad import Gamepad
from .input_capture import capture_loop
from .tui import TUI


def run(stdscr, cfg: config.Config) -> None:
    """Main bridge loop — wires input capture, mapper, TUI, and gamepad."""
    tui = TUI(stdscr)
    stop = threading.Event()
    q: queue.Queue[str] = queue.Queue()

    capture_thread = threading.Thread(
        target=capture_loop,
        args=(stdscr, q, stop),
        daemon=True,
    )
    capture_thread.start()

    with Gamepad() as gp:
        while not stop.is_set():
            try:
                key = q.get(timeout=0.1)
            except queue.Empty:
                continue

            buttons = mapper.lookup(key, cfg)
            tui.update(key, buttons)

            if buttons:
                gp.press_combo(buttons, cfg.settings.hold_ms, cfg.settings.release_gap_ms)


def main() -> None:
    cfg = config.load()
    curses.wrapper(run, cfg)
