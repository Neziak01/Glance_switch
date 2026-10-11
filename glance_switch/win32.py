"""Thin ctypes layer over the Win32 APIs GlanceSwitch needs.

Monitors, top-level windows in z-order, forcing focus, cursor control,
a low-level keyboard/mouse hook, autostart and single-instance handling.
Importing this module on another OS is harmless; calling into it is not.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
else:  # pragma: no cover - lets tests import the module elsewhere
    user32 = kernel32 = dwmapi = None

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

# --- constants ---------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
GA_ROOT = 2
SW_RESTORE = 9
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN = 0x201, 0x204, 0x207, 0x20B
WM_MOUSEWHEEL = 0x20A
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
VK_CONTROL, VK_LCONTROL, VK_RCONTROL, VK_MENU = 0x11, 0xA2, 0xA3, 0x12
VK_MASK = 0xE8  # unassigned; stops a synthetic Alt from opening menus
CTRL_KEYS = (VK_CONTROL, VK_LCONTROL, VK_RCONTROL)
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x2
ERROR_ALREADY_EXISTS = 183
MONITORINFOF_PRIMARY = 1
MB_ICONINFORMATION, MB_ICONERROR = 0x40, 0x10
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


# --- structures --------------------------------------------------------------
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
MONITORENUMPROC = _FUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
WNDENUMPROC = _FUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
HOOKPROC = _FUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def _declare() -> None:
    sig = [
        (user32.EnumDisplayMonitors, [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        (user32.GetMonitorInfoW, [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)], wintypes.BOOL),
        (user32.EnumWindows, [WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        (user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
        (user32.IsWindow, [wintypes.HWND], wintypes.BOOL),
        (user32.IsIconic, [wintypes.HWND], wintypes.BOOL),
        (user32.GetWindowTextLengthW, [wintypes.HWND], ctypes.c_int),
        (user32.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.GetClassNameW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user32.GetForegroundWindow, [], wintypes.HWND),
        (user32.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
        (user32.BringWindowToTop, [wintypes.HWND], wintypes.BOOL),
        (user32.ShowWindow, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        (user32.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user32.AttachThreadInput, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL),
        (user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32.SetCursorPos, [ctypes.c_int, ctypes.c_int], wintypes.BOOL),
        (user32.SendInput, [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int], wintypes.UINT),
        (user32.SetWindowsHookExW, [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD], wintypes.HHOOK),
        (user32.UnhookWindowsHookEx, [wintypes.HHOOK], wintypes.BOOL),
        (user32.CallNextHookEx, [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
        (user32.GetMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        (user32.PostThreadMessageW, [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        (user32.MessageBoxW, [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT], ctypes.c_int),
        (user32.GetParent, [wintypes.HWND], wintypes.HWND),
        (user32.GetAsyncKeyState, [ctypes.c_int], ctypes.c_short),
        (kernel32.GetCurrentThreadId, [], wintypes.DWORD),
        (kernel32.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE),
        (kernel32.CreateMutexW, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        (dwmapi.DwmGetWindowAttribute, [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long),
    ]
    for fn, args, res in sig:
        fn.argtypes = args
        fn.restype = res
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


if IS_WINDOWS:
    _declare()


def _get_exstyle(hwnd: int) -> int:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        return user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return user32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def _set_exstyle(hwnd: int, style: int) -> None:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# --- DPI ---------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    """Use physical pixels everywhere (must run before any window is created)."""
    try:
        ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctx):
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()


# --- monitors ----------------------------------------------------------------
Rect = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass(frozen=True)
class Monitor:
    rect: Rect
    work: Rect
    primary: bool
    name: str

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def _rect(r: wintypes.RECT) -> Rect:
    return (r.left, r.top, r.right, r.bottom)


def monitors() -> list[Monitor]:
    found: list[Monitor] = []

    def callback(hmon, _hdc, _rect_ptr, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            found.append(
                Monitor(
                    rect=_rect(info.rcMonitor),
                    work=_rect(info.rcWork),
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    name=info.szDevice,
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    found.sort(key=lambda m: (m.rect[0], m.rect[1]))
    return found


# --- windows -----------------------------------------------------------------
@dataclass(frozen=True)
class Window:
    hwnd: int
    rect: Rect
    title: str


def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return hr == 0 and cloaked.value != 0


def window_rect(hwnd: int) -> Rect:
    """Visible bounds (without the invisible resize borders of Windows 10/11)."""
    r = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)
    )
    if hr != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _rect(r)


def _is_switchable(hwnd: int, own_pid: int) -> bool:
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return False
    ex = _get_exstyle(hwnd)
    if ex & (WS_EX_NOACTIVATE | WS_EX_TRANSPARENT):
        return False  # click-through overlays (game/GPU HUDs) are never targets
    if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
        return False
    if _class_name(hwnd) in SHELL_CLASSES or _is_cloaked(hwnd):
        return False
    return bool(_text(hwnd))


def windows_z_order() -> list[Window]:
    """Switchable top-level windows, topmost first."""
    own_pid = os.getpid()
    result: list[Window] = []

    def callback(hwnd, _data):
        if _is_switchable(hwnd, own_pid):
            rect = window_rect(hwnd)
            if rect[2] - rect[0] > 40 and rect[3] - rect[1] > 40:
                result.append(Window(hwnd=hwnd, rect=rect, title=_text(hwnd)))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


def foreground_window() -> Optional[int]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return user32.GetAncestor(hwnd, GA_ROOT) or hwnd


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def _send_keys(*events: tuple[int, bool]) -> None:
    arr = (INPUT * len(events))()
    for i, (vk, up) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def activate(hwnd: int) -> bool:
    """Bring `hwnd` to the foreground despite Windows' focus-stealing guard."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if foreground_window() == hwnd:
        return True

    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    if foreground_window() == hwnd:
        return True

    # A synthetic Alt press grants foreground rights. The masking key in
    # between stops Alt from activating the menu bar of the old window.
    _send_keys((VK_MENU, False), (VK_MASK, False), (VK_MASK, True), (VK_MENU, True))
    user32.SetForegroundWindow(hwnd)
    return foreground_window() == hwnd


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def mouse_button_down() -> bool:
    """True while the left or right mouse button is held (e.g. a drag)."""
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02))


def make_click_through(hwnd: int) -> None:
    style = _get_exstyle(hwnd)
    _set_exstyle(
        hwnd,
        style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
    )


def toplevel_hwnd(tk_widget) -> int:
    """HWND of a Tk toplevel (winfo_id is the inner client window)."""
    return user32.GetParent(tk_widget.winfo_id()) or tk_widget.winfo_id()


def message_box(text: str, title: str, error: bool = False) -> None:
    user32.MessageBoxW(None, text, title, MB_ICONERROR if error else MB_ICONINFORMATION)


# --- input hook --------------------------------------------------------------
class InputHook:
    """Global low-level keyboard + mouse hook on its own message-loop thread.

    `on_key(vk, is_down)` and `on_mouse_button()` run on the hook thread and
    must return quickly. Synthetic (injected) input is ignored.
    """

    def __init__(self, on_key: Callable[[int, bool], None], on_mouse_button: Callable[[], None]):
        self.on_key = on_key
        self.on_mouse_button = on_mouse_button
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="input-hook", daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        def kb_proc(n_code, w_param, l_param):
            if n_code == 0:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not data.flags & LLKHF_INJECTED:
                    try:
                        self.on_key(data.vkCode, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN))
                    except Exception:  # noqa: BLE001 - never break the hook chain
                        log.exception("key handler failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def mouse_proc(n_code, w_param, l_param):
            if n_code == 0 and w_param in (
                WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN, WM_MOUSEWHEEL
            ):
                data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not data.flags & LLMHF_INJECTED:
                    try:
                        self.on_mouse_button()
                    except Exception:  # noqa: BLE001
                        log.exception("mouse handler failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        # Keep references: ctypes callbacks must outlive the hooks.
        self._kb_cb = HOOKPROC(kb_proc)
        self._mouse_cb = HOOKPROC(mouse_proc)
        module = kernel32.GetModuleHandleW(None)
        kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_cb, module, 0)
        mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, module, 0)
        if not kb:
            log.error("keyboard hook failed: %s", ctypes.get_last_error())
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if kb:
                user32.UnhookWindowsHookEx(kb)
            if mouse:
                user32.UnhookWindowsHookEx(mouse)


# --- process -----------------------------------------------------------------
_mutex = None


def acquire_single_instance(name: str) -> bool:
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{exe}" -m glance_switch'


def autostart_enabled(name: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
            return True
    except OSError:
        return False


def set_autostart(name: str, enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass

