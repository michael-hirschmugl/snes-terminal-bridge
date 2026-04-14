"""Injects SNES button combos as X11 key events into a target window.

Focus management strategy is chosen automatically at runtime:
- WSL2:         PowerShell SetForegroundWindow (X11 focus APIs non-functional under WSLg)
- Native Linux: xdotool windowactivate via _NET_ACTIVE_WINDOW (requires a real X11 WM)
"""
import os
import subprocess
import time


def _is_wsl2() -> bool:
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except OSError:
        return False


# PowerShell init script — loaded once into the persistent PS process.
_PS_INIT = (
    "Add-Type -Name WF -Namespace '' -MemberDefinition '"
    "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h); "
    "[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();'; "
    "Write-Host ready"
)


class KeyboardInjector:
    def __init__(self, window_pattern: str, button_map: dict[str, str]):
        self._window_pattern = window_pattern
        self._button_map = button_map
        self._wsl2 = _is_wsl2()

        # WSL2 state
        self._target_hwnd: str | None = None
        self._our_hwnd: str | None = None
        self._ps: subprocess.Popen | None = None

        # Native Linux state
        self._target_xid: str | None = None
        self._our_xid: str | None = None

    # ------------------------------------------------------------------
    # WSL2: persistent PowerShell process + SetForegroundWindow
    # ------------------------------------------------------------------

    def _ensure_ps(self) -> None:
        if self._ps is not None:
            return
        self._ps = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._ps.stdin.write(_PS_INIT + "\n")
        self._ps.stdin.flush()
        self._ps.stdout.readline()  # wait for "ready"

    def _ps_cmd(self, cmd: str) -> str:
        self._ensure_ps()
        self._ps.stdin.write(cmd + "\n")
        self._ps.stdin.flush()
        return self._ps.stdout.readline().strip()

    def _find_target_hwnd(self) -> str:
        pat = self._window_pattern.replace("'", "\\'")
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                f"$p = Get-Process | Where-Object {{ $_.MainWindowTitle -like '*{pat}*' }}"
                f" | Select-Object -First 1; if ($p) {{ $p.MainWindowHandle }} else {{ 0 }}",
            ],
            capture_output=True, text=True,
        )
        hwnd = result.stdout.strip()
        if not hwnd or hwnd == "0":
            raise RuntimeError(
                f"No window with title matching '*{self._window_pattern}*' found. "
                "Is the emulator running?"
            )
        return hwnd

    def _win_activate(self, hwnd: str) -> None:
        self._ps_cmd(f"[void][WF]::SetForegroundWindow([IntPtr]{hwnd}); Write-Host ok")

    # ------------------------------------------------------------------
    # Native Linux: xdotool windowactivate
    # ------------------------------------------------------------------

    def _find_target_xid(self) -> str:
        result = subprocess.run(
            ["xdotool", "search", "--name", self._window_pattern],
            capture_output=True, text=True,
        )
        ids = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if not ids:
            raise RuntimeError(
                f"No window matching '{self._window_pattern}' found. "
                "Is the emulator running?"
            )
        return ids[0]

    def _x11_activate(self, xid: str) -> None:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", xid],
            check=False, capture_output=True,
        )

    # ------------------------------------------------------------------
    # Shared: key injection via XTest
    # ------------------------------------------------------------------

    def press_combo(self, buttons: list[str], hold_ms: int, release_gap_ms: int) -> None:
        keys = [self._button_map[b] for b in buttons if b in self._button_map]
        if not keys:
            return

        if self._wsl2:
            self._press_wsl2(keys, hold_ms, release_gap_ms)
        else:
            self._press_x11(keys, hold_ms, release_gap_ms)

    def _press_wsl2(self, keys: list[str], hold_ms: int, release_gap_ms: int) -> None:
        if self._our_hwnd is None:
            self._our_hwnd = self._ps_cmd("Write-Host ([WF]::GetForegroundWindow())")
        if self._target_hwnd is None:
            self._target_hwnd = self._find_target_hwnd()

        self._win_activate(self._target_hwnd)
        time.sleep(0.03)
        self._inject_keys(keys, hold_ms)
        time.sleep(release_gap_ms / 1000)
        self._win_activate(self._our_hwnd)

    def _press_x11(self, keys: list[str], hold_ms: int, release_gap_ms: int) -> None:
        if self._our_xid is None:
            result = subprocess.run(
                ["xdotool", "getactivewindow"], capture_output=True, text=True
            )
            self._our_xid = result.stdout.strip() or None
        if self._target_xid is None:
            self._target_xid = self._find_target_xid()

        self._x11_activate(self._target_xid)
        time.sleep(0.03)
        self._inject_keys(keys, hold_ms)
        time.sleep(release_gap_ms / 1000)
        if self._our_xid:
            self._x11_activate(self._our_xid)

    def _inject_keys(self, keys: list[str], hold_ms: int) -> None:
        for key in keys:
            subprocess.run(["xdotool", "keydown", key], check=False, capture_output=True)
        time.sleep(hold_ms / 1000)
        for key in keys:
            subprocess.run(["xdotool", "keyup", key], check=False, capture_output=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._ps is not None:
            try:
                self._ps.terminate()
            except OSError:
                pass
            self._ps = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
