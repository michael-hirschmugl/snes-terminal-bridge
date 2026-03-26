import time
from evdev import UInput, AbsInfo, ecodes as e

# SNES button name → evdev constant
BUTTON_MAP = {
    "A":      e.BTN_EAST,
    "B":      e.BTN_SOUTH,
    "X":      e.BTN_NORTH,
    "Y":      e.BTN_WEST,
    "L":      e.BTN_TL,
    "R":      e.BTN_TR,
    "Start":  e.BTN_START,
    "Select": e.BTN_SELECT,
}

# D-pad buttons → (axis, value)
DPAD_MAP = {
    "Up":    (e.ABS_HAT0Y, -1),
    "Down":  (e.ABS_HAT0Y, +1),
    "Left":  (e.ABS_HAT0X, -1),
    "Right": (e.ABS_HAT0X, +1),
}

CAPABILITIES = {
    e.EV_KEY: list(BUTTON_MAP.values()),
    e.EV_ABS: [
        (e.ABS_HAT0X, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
        (e.ABS_HAT0Y, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
    ],
}


class Gamepad:
    def __init__(self, name: str = "SNES Terminal Bridge"):
        self._ui = UInput(CAPABILITIES, name=name, version=0x1)

    def press_combo(self, buttons: list[str], hold_ms: int, release_gap_ms: int) -> None:
        """Press all buttons simultaneously, hold, then release."""
        # Collect axis changes needed (deduplicate: last one wins per axis)
        axes: dict[int, int] = {}
        keys: list[int] = []

        for btn in buttons:
            if btn in BUTTON_MAP:
                keys.append(BUTTON_MAP[btn])
            elif btn in DPAD_MAP:
                axis, val = DPAD_MAP[btn]
                axes[axis] = val

        # Press all — atomic via single syn()
        for key in keys:
            self._ui.write(e.EV_KEY, key, 1)
        for axis, val in axes.items():
            self._ui.write(e.EV_ABS, axis, val)
        self._ui.syn()

        time.sleep(hold_ms / 1000)

        # Release all — atomic via single syn()
        for key in keys:
            self._ui.write(e.EV_KEY, key, 0)
        for axis in axes:
            self._ui.write(e.EV_ABS, axis, 0)
        self._ui.syn()

        time.sleep(release_gap_ms / 1000)

    def close(self) -> None:
        self._ui.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
