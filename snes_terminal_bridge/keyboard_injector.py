"""Injects SNES button combos as X11 key events via xdotool XTest.

bSNES+ receives XTest events regardless of X11 focus (it polls the X11
keyboard state directly rather than relying on KeyPress events delivered
to the focused window).  No focus switching is performed — the bridge
terminal keeps focus throughout so the user can keep typing.

On native Linux the same approach applies; xdotool XTest events reach
the emulator without a windowactivate call.
"""
import subprocess
import time


def _is_wsl2() -> bool:
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except OSError:
        return False


class KeyboardInjector:
    def __init__(self, window_pattern: str, button_map: dict[str, str]):
        self._window_pattern = window_pattern
        self._button_map = button_map

    # ------------------------------------------------------------------
    # Key injection via XTest (no focus switching)
    # ------------------------------------------------------------------

    def press_combo(self, buttons: list[str], hold_ms: int, release_gap_ms: int) -> None:
        keys = [self._button_map[b] for b in buttons if b in self._button_map]
        if not keys:
            return
        self._inject_keys(keys, hold_ms)
        time.sleep(release_gap_ms / 1000)

    def _inject_keys(self, keys: list[str], hold_ms: int) -> None:
        pressed = []
        try:
            for key in keys:
                subprocess.run(
                    ["xdotool", "keydown", "--clearmodifiers", key],
                    check=False, capture_output=True,
                )
                pressed.append(key)
            time.sleep(hold_ms / 1000)
        finally:
            for key in pressed:
                subprocess.run(
                    ["xdotool", "keyup", "--clearmodifiers", key],
                    check=False, capture_output=True,
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
