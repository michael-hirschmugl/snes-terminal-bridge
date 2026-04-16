import curses
import queue
import signal
import threading

from . import config, mapper
from .keyboard_injector import KeyboardInjector
from .input_capture import capture_loop
from .tui import TUI


def run(stdscr, cfg: config.Config, kb_cfg: config.KeyboardConfig) -> None:
    """Main bridge loop — wires input capture, mapper, TUI, and keyboard injector."""
    tui = TUI(stdscr)
    stop = threading.Event()
    q: queue.Queue[str] = queue.Queue()

    capture_thread = threading.Thread(
        target=capture_loop,
        args=(stdscr, q, stop),
        daemon=True,
    )
    capture_thread.start()

    with KeyboardInjector(kb_cfg.window, kb_cfg.buttons) as injector:
        # Release all keys on SIGTERM / SIGHUP (e.g. terminal window closed)
        def _shutdown(sig, frame):
            injector.close()
            stop.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGHUP, _shutdown)

        while not stop.is_set():
            try:
                key = q.get(timeout=0.1)
            except queue.Empty:
                continue

            buttons = mapper.lookup(key, cfg)
            keys = [kb_cfg.buttons[b] for b in (buttons or []) if b in kb_cfg.buttons] or None
            tui.update(key, buttons, keys)

            if buttons:
                injector.press_combo(buttons, cfg.settings.hold_ms, cfg.settings.release_gap_ms)


def main(target_override: str | None = None) -> None:
    cfg = config.load()
    kb_cfg = config.load_keyboard()
    if target_override:
        kb_cfg.window = target_override
    curses.wrapper(run, cfg, kb_cfg)
