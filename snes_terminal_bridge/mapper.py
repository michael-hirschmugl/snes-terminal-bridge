from .config import Config

# curses key name → KEY_* name used in mappings.yaml
CURSES_KEY_NAMES = {
    "KEY_UP":     "KEY_UP",
    "KEY_DOWN":   "KEY_DOWN",
    "KEY_LEFT":   "KEY_LEFT",
    "KEY_RIGHT":  "KEY_RIGHT",
    "KEY_ENTER":  "KEY_ENTER",
    "KEY_BACKSPACE": "KEY_BACKSPACE",
    "\n":         "KEY_ENTER",
    "\r":         "KEY_ENTER",
    "\x7f":       "KEY_DELETE",
    "\x1b":       "KEY_ESCAPE",
    "\t":         "KEY_TAB",
}


def lookup(key: str, config: Config) -> list[str] | None:
    """Return the SNES button list for a key, or None if unmapped.

    key is either:
    - a single character (e.g. 'A', 'a', ' ')
    - a curses key name string (e.g. 'KEY_UP')
    """
    resolved = CURSES_KEY_NAMES.get(key, key)
    return config.mappings.get(resolved)
