"""Windows'a özel yardımcılar: pencere gömme, sistem tepsisi, sürükle-bırak.

Hepsi ctypes ile yazıldı; ek paket gerekmez. Windows dışında sessizce devre dışı kalır.
"""
import ctypes
import os
import threading
from ctypes import wintypes

IS_WIN = os.name == "nt"

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]

GWL_STYLE, GWLP_WNDPROC = -16, -4
WS_CHILD, WS_POPUP, WS_CAPTION, WS_THICKFRAME, WS_VISIBLE = 0x40000000, 0x80000000, 0x00C00000, 0x00040000, 0x10000000
WM_CLOSE, WM_DROPFILES = 0x0010, 0x0233
WM_APP = 0x8000


# ===================================================================== gömme
def find_window(title):
    if not IS_WIN:
        return None
    return user32.FindWindowW(None, title) or None


def embed(hwnd, parent_hwnd, w, h):
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style = (style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD | WS_VISIBLE
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    user32.SetParent(hwnd, parent_hwnd)
    user32.MoveWindow(hwnd, 0, 0, max(w, 10), max(h, 10), True)
    user32.ShowWindow(hwnd, 5)


def move(hwnd, w, h):
    if IS_WIN and hwnd and user32.IsWindow(hwnd):
        user32.MoveWindow(hwnd, 0, 0, max(w, 10), max(h, 10), True)


def focus(hwnd):
    if IS_WIN and hwnd and user32.IsWindow(hwnd):
        user32.SetFocus(hwnd)


def close_window(hwnd):
    if IS_WIN and hwnd and user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def is_window(hwnd):
    return bool(IS_WIN and hwnd and user32.IsWindow(hwnd))


# ============================================================ sürükle-bırak
class DropTarget:
    """Bir tk penceresine Windows Gezgini'nden dosya bırakılmasını sağlar."""

    def __init__(self, tk_toplevel, callback):
        self.cb = callback
        self.ok = False
        if not IS_WIN:
            return
        self.hwnd = int(tk_toplevel.wm_frame(), 16)
        self._proc = WNDPROC(self._wndproc)
        self._old = user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                             ctypes.cast(self._proc, ctypes.c_void_p).value)
        shell32.DragAcceptFiles(self.hwnd, True)
        self.ok = True

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_DROPFILES:
            try:
                n = shell32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(n):
                    ln = shell32.DragQueryFileW(wp, i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(ln)
                    shell32.DragQueryFileW(wp, i, buf, ln)
                    files.append(buf.value)
                shell32.DragFinish(wp)
                self.cb(files)
            except Exception:  # noqa: BLE001
                pass
            return 0
        return user32.CallWindowProcW(self._old, hwnd, msg, wp, lp)


# ============================================================ sistem tepsisi
if IS_WIN:
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]

    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]


class Tray(threading.Thread):
    """Sistem tepsisi simgesi. Geri çağrılar tepsi iş parçacığından gelir."""

    MSG_CB, MSG_QUIT, MSG_BALLOON = WM_APP + 1, WM_APP + 2, WM_APP + 3

    def __init__(self, icon_path, tooltip, on_show, on_exit):
        super().__init__(daemon=True)
        self.icon_path, self.tooltip = icon_path, tooltip
        self.on_show, self.on_exit = on_show, on_exit
        self.hwnd = None
        self._balloon = None
        self.ready = threading.Event()

    def run(self):
        if not IS_WIN:
            return
        try:
            self._run()
        except Exception:  # noqa: BLE001
            self.ready.set()

    def _nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "irbaTestTray"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "irbaTestTray", "irbaTestTray", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        hicon = user32.LoadImageW(None, self.icon_path, 1, 0, 0, 0x10 | 0x40)
        nid = self._nid()
        nid.uFlags = 0x1 | 0x2 | 0x4
        nid.uCallbackMessage = self.MSG_CB
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(0, ctypes.byref(nid))
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == self.MSG_CB:
                if lp in (0x0202, 0x0203):        # sol tık / çift tık
                    self.on_show()
                elif lp == 0x0205:                # sağ tık
                    menu = user32.CreatePopupMenu()
                    user32.AppendMenuW(menu, 0, 1, "Göster")
                    user32.AppendMenuW(menu, 0x800, 0, None)
                    user32.AppendMenuW(menu, 0, 2, "Çıkış")
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    user32.SetForegroundWindow(hwnd)
                    cmd = user32.TrackPopupMenu(menu, 0x100 | 0x80, pt.x, pt.y, 0, hwnd, None)
                    user32.DestroyMenu(menu)
                    if cmd == 1:
                        self.on_show()
                    elif cmd == 2:
                        self.on_exit()
                return 0
            if msg == self.MSG_BALLOON and self._balloon:
                title, text = self._balloon
                nid = self._nid()
                nid.uFlags = 0x10
                nid.szInfoTitle = title[:63]
                nid.szInfo = text[:255]
                nid.dwInfoFlags = 0x1
                shell32.Shell_NotifyIconW(1, ctypes.byref(nid))
                return 0
            if msg == self.MSG_QUIT:
                shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid()))
                user32.DestroyWindow(hwnd)
                user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def notify(self, title, text):
        if self.hwnd:
            self._balloon = (title, text)
            user32.PostMessageW(self.hwnd, self.MSG_BALLOON, 0, 0)

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, self.MSG_QUIT, 0, 0)
