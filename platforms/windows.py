"""Windows: the game window is found by its window class (or title and program name) with
user32, and positions are in physical pixels (the process is made DPI aware at start)."""
import ctypes
import os
import platform
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path

import numpy as np

from .base import Hotkeys, Platform, parse_hotkey

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _api(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_EnumWindows = _api(_user32.EnumWindows, wintypes.BOOL, _EnumWindowsProc, wintypes.LPARAM)
_IsWindow = _api(_user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _api(_user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_IsIconic = _api(_user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_GetClassName = _api(_user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowText = _api(_user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetClientRect = _api(_user32.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_ClientToScreen = _api(_user32.ClientToScreen, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
_GetWindowThreadProcessId = _api(_user32.GetWindowThreadProcessId, wintypes.DWORD,
                                 wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_OpenProcess = _api(_kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageName = _api(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE,
                                  wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_CloseHandle = _api(_kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
_GetAncestor = _api(_user32.GetAncestor, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowRect = _api(_user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_ShowWindow = _api(_user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetForegroundWindow = _api(_user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_CreateMutex = _api(_kernel32.CreateMutexW, wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_MonitorFromPoint = _api(_user32.MonitorFromPoint, wintypes.HANDLE, wintypes.POINT, wintypes.DWORD)
_GetForegroundWindow = _api(_user32.GetForegroundWindow, wintypes.HWND)
_RegisterHotKey = _api(_user32.RegisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT,
                       wintypes.UINT)
_UnregisterHotKey = _api(_user32.UnregisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetTimer = _api(_user32.SetTimer, ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
_GetMessage = _api(_user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                   wintypes.UINT, wintypes.UINT)

# For drawing the see-through panels over the game (paint_overlay).
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _Blend(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


_GetDC = _api(_user32.GetDC, wintypes.HDC, wintypes.HWND)
_ReleaseDC = _api(_user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_CreateCompatibleDC = _api(_gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_DeleteDC = _api(_gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_CreateDIBSection = _api(_gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(_BitmapInfo),
                         wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
_SelectObject = _api(_gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_DeleteObject = _api(_gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_UpdateLayeredWindow = _api(_user32.UpdateLayeredWindow, wintypes.BOOL, wintypes.HWND, wintypes.HDC,
                            ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                            ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(_Blend), wintypes.DWORD)


def _process_name(hwnd):
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        ok = _QueryFullProcessImageName(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        _CloseHandle(handle)


def _windows_titled(*prefixes):
    """Visible windows whose title starts with one of the prefixes."""
    found = []

    def check(hwnd, _):
        title = ctypes.create_unicode_buffer(256)
        _GetWindowText(hwnd, title, 256)
        if title.value.startswith(prefixes) and _IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    _EnumWindows(_EnumWindowsProc(check), 0)
    return found


class WindowsHotkeys(Hotkeys):
    """Windows hotkeys, as Discord and OBS use, on their own thread: they work while you play
    even though the game runs as administrator, and they never touch the game. They're
    registered only while MapleStory or the overlay is the window in front, so other
    programs can still use those keys (e.g. to type "!")."""

    def __init__(self, on_press):
        super().__init__(on_press)
        self._wanted, self._paused, self._changed = {}, False, True
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, keys, on=True):
        with self._lock:
            self._wanted = {a: parse_hotkey(k) for a, k in keys.items() if on and parse_hotkey(k)}
            self._changed = True

    def pause(self, paused):
        with self._lock:
            self._paused, self._changed = paused, True

    def in_front(self):
        hwnd = _GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        cls = ctypes.create_unicode_buffer(64)
        _GetClassName(hwnd, cls, 64)
        return cls.value == "MapleStoryClass" or _process_name(hwnd) == "maplestory.exe"

    def _run(self):
        msg, active, was_on = wintypes.MSG(), {}, None    # active: hotkey id -> action
        self.thread_id = _kernel32.GetCurrentThreadId()
        _SetTimer(None, 0, 250, None)                     # a WM_TIMER every 250 ms, to check what's in front
        while _GetMessage(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                     # WM_HOTKEY
                action = active.get(msg.wParam)
                if action:
                    self.on_press(action)
            elif msg.message == 0x0113:                   # WM_TIMER
                try:
                    on = self.in_front()
                except Exception:
                    on = False
                with self._lock:
                    changed, wanted, paused, self._changed = self._changed, dict(self._wanted), self._paused, False
                on = on and not paused
                if not changed and on == was_on:
                    continue
                for hid in active:
                    _UnregisterHotKey(None, hid)
                active, was_on = {}, on
                if on:
                    for hid, (action, (mods, vk)) in enumerate(wanted.items(), start=1):
                        if _RegisterHotKey(None, hid, mods | 0x4000, vk):     # MOD_NOREPEAT
                            active[hid] = action
                            self.problems.pop(action, None)
                        else:
                            self.problems[action] = "used by another program"


class WindowsPlatform(Platform):
    name = "Windows"

    def setup_process(self):
        # Use physical pixels so window positions, tkinter and mss screenshots all agree on
        # scaled displays.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def version(self):
        return f"Windows {platform.version()}"

    def data_root(self):
        return Path(os.environ.get("APPDATA") or Path.home())

    def tesseract_candidates(self, home_dir, app_dir):
        # Windows' usual search looks in the current folder first, so a tesseract.exe someone
        # left in, say, Downloads could be run: only full paths from PATH are used.
        on_path = [Path(d) / "tesseract.exe" for d in os.environ.get("PATH", "").split(os.pathsep)
                   if d.strip() and os.path.isabs(d.strip())]
        return [
            home_dir / "tesseract" / "tesseract.exe",            # comes with the download
            app_dir / "tesseract" / "tesseract.exe",
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
            *on_path,
        ]

    def claim_single_instance(self, key, title, old_titles=()):
        self._mutex = _CreateMutex(None, False, "Local\\MapleEXP-" + key)    # kept while it runs
        if ctypes.get_last_error() != 183:                                   # ERROR_ALREADY_EXISTS
            return True
        found = _windows_titled(title, *old_titles)             # any version's main window
        if found:
            _ShowWindow(found[0], 9)                            # SW_RESTORE
            _SetForegroundWindow(found[0])
        else:
            ctypes.windll.user32.MessageBoxW(None, f"{title} is already running.", title, 0x40)
        return False

    def find_game_window(self):
        found = []

        def check(hwnd, _):
            if _IsWindowVisible(hwnd):
                cls = ctypes.create_unicode_buffer(256)
                title = ctypes.create_unicode_buffer(256)
                _GetClassName(hwnd, cls, 256)
                _GetWindowText(hwnd, title, 256)
                if cls.value == "MapleStoryClass" or (
                        "maple" in title.value.lower() and _process_name(hwnd) == "maplestory.exe"):
                    rect = self.client_rect(hwnd)
                    found.append((rect[2] * rect[3] if rect else 0, hwnd))
            return True

        _EnumWindows(_EnumWindowsProc(check), 0)
        return max(found)[1] if found else None

    def window_exists(self, hwnd):
        return bool(_IsWindow(hwnd))

    def client_rect(self, hwnd):
        if not _IsWindow(hwnd) or _IsIconic(hwnd):
            return None
        r, pt = wintypes.RECT(), wintypes.POINT(0, 0)
        if not _GetClientRect(hwnd, ctypes.byref(r)) or not _ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        if r.right < 100 or r.bottom < 100:
            return None
        return pt.x, pt.y, r.right, r.bottom

    def own_window_rect(self, root):
        hwnd = _GetAncestor(root.winfo_id(), 2)                 # GA_ROOT: the framed window
        r = wintypes.RECT()
        if _GetWindowRect(hwnd, ctypes.byref(r)):
            return r.left, r.top, r.right - r.left, r.bottom - r.top
        return None

    def screen_dpi(self, x, y):
        # Screens can have different Windows scaling (e.g. 200% on a 5K screen and 125% on a
        # 1440p one), so text is sized for each.
        try:
            dx, dy = ctypes.c_uint(), ctypes.c_uint()
            monitor = _MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)      # MONITOR_DEFAULTTONEAREST
            if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                return dx.value
        except Exception:
            pass
        return None

    def dpi_awareness(self):
        try:
            level = ctypes.c_int()
            if ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(level)) == 0:
                return {0: "not aware", 1: "system", 2: "per-monitor"}.get(level.value)
        except Exception:
            pass
        return None

    def create_hotkeys(self, on_press):
        return WindowsHotkeys(on_press)

    def register_fonts(self, paths):
        # Private to this program (FR_PRIVATE): nothing is installed, and they go when it closes.
        try:
            add = ctypes.windll.gdi32.AddFontResourceExW
            add.argtypes, add.restype = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p], ctypes.c_int
            return all(add(str(p), 0x10, None) for p in paths)
        except Exception:
            return False

    def style_window(self, root, background, text):
        """A dark title bar in the overlay's own colours (Windows 10 2004+ for dark, 11 for the colours)."""
        def bgr(hex_):
            r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
            return b << 16 | g << 8 | r
        try:
            root.update_idletasks()
            hwnd = _GetAncestor(root.winfo_id(), 2)
            set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
            for attr, value in ((20, 1), (35, bgr(background)), (34, bgr(background)), (36, bgr(text))):
                set_attr(hwnd, attr, ctypes.byref(ctypes.c_int(value)), 4)   # dark mode, caption, border, text
        except Exception:
            pass

    def make_overlay(self, window):
        try:
            window.update_idletasks()
            hwnd = _GetAncestor(window.winfo_id(), 2)
            get = ctypes.windll.user32.GetWindowLongPtrW
            put = ctypes.windll.user32.SetWindowLongPtrW
            get.restype, get.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
            put.restype, put.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            # WS_EX_LAYERED | WS_EX_TRANSPARENT (clicks go to the game) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            put(hwnd, -20, get(hwnd, -20) | 0x80000 | 0x20 | 0x80 | 0x8000000)
        except Exception:
            pass

    def set_click_through(self, window, on):
        try:
            hwnd = _GetAncestor(window.winfo_id(), 2)
            get = ctypes.windll.user32.GetWindowLongPtrW
            put = ctypes.windll.user32.SetWindowLongPtrW
            get.restype, get.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
            put.restype, put.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            style = get(hwnd, -20)
            put(hwnd, -20, (style | 0x20) if on else (style & ~0x20))      # WS_EX_TRANSPARENT
        except Exception:
            pass

    def paint_overlay(self, window, image, x, y):
        """Puts a picture with see-through parts (Pillow RGBA) in the overlay window at (x, y), pixel
        by pixel: rounded corners and a translucent background with solid text."""
        try:
            hwnd = _GetAncestor(window.winfo_id(), 2)
            w, h = image.size
            rgba = np.asarray(image, dtype=np.uint16)
            alpha = rgba[:, :, 3:4]
            bgra = np.concatenate([rgba[:, :, 2::-1] * alpha // 255, alpha], axis=2).astype(np.uint8)  # premultiplied
            screen = _GetDC(None)
            memory = _CreateCompatibleDC(screen)
            header = _BitmapInfo(ctypes.sizeof(_BitmapInfo), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)    # top-down, 32-bit
            bits = ctypes.c_void_p()
            bitmap = _CreateDIBSection(memory, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            try:
                ctypes.memmove(bits, bgra.tobytes(), w * h * 4)
                old = _SelectObject(memory, bitmap)
                blend = _Blend(0, 0, 255, 1)                         # AC_SRC_OVER, the picture's own alpha
                ok = _UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(w, h)),
                                          memory, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
                if not ok:
                    self.paint_error = ctypes.WinError(ctypes.get_last_error())
                _SelectObject(memory, old)
            finally:
                _DeleteObject(bitmap)
                _DeleteDC(memory)
                _ReleaseDC(None, screen)
            return bool(ok)
        except Exception as e:
            self.paint_error = e                                       # (for bug reports)
            return False

    def show_overlay(self, window, visible, rect=None):
        hwnd = _GetAncestor(window.winfo_id(), 2)
        if visible:          # HWND_TOPMOST, (SWP_NOSIZE | SWP_NOMOVE without a rect) | SWP_NOACTIVATE | SWP_SHOWWINDOW
            x, y, w, h = rect or (0, 0, 0, 0)
            ctypes.windll.user32.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, w, h, (0 if rect else 0x1 | 0x2) | 0x10 | 0x40)
        else:
            _ShowWindow(hwnd, 0)                                        # SW_HIDE

    def game_in_front(self):
        hwnd = _GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        cls = ctypes.create_unicode_buffer(64)
        _GetClassName(hwnd, cls, 64)
        return cls.value == "MapleStoryClass" or _process_name(hwnd) == "maplestory.exe"

    def open_folder(self, path):
        os.startfile(path)

    def show_file(self, path):
        subprocess.Popen(["explorer", f"/select,{path}"])
