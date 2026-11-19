"""
Visual indicator for OpenWispr:
A sleek, minimal floating wave pill (inspired by Wispr Flow).
Displays only when recording, with smooth, subtle, flicker-free animated waves.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
from queue import Queue
import threading
from enum import Enum, auto

# Colors (Win32 COLORREF: 0x00BBGGRR)
def rgb(r: int, g: int, b: int) -> int:
    return (b << 16) | (g << 8) | r

CLR_TRANSPARENT: int = rgb(0x01, 0x01, 0x01)  # Color-key for rounded transparency
CLR_PILL_BG: int = rgb(15, 15, 20)           # Deep obsidian dark glass
CLR_BORDER_ACTIVE: int = rgb(14, 165, 233)   # Electric cyan/sky glow
CLR_WAVE_BAR: int = rgb(56, 189, 248)        # Vibrant cyan-blue wave bars

OVERLAY_W: int = 56
OVERLAY_H: int = 26
BOTTOM_OFFSET: int = 60
ICON_SIZE: int = 32

WM_TRAY: int = 0x0400 + 1
WM_APP_CMD: int = 0x0400 + 2

WM_ERASEBKGND: int = 0x0014
WM_PAINT: int = 0x000F
WM_TIMER: int = 0x0113

SM_CXSCREEN: int = 0
SM_CYSCREEN: int = 1

WS_POPUP: int = 0x80000000
WS_EX_TOOLWINDOW: int = 0x00000080
WS_EX_TOPMOST: int = 0x00000008
WS_EX_LAYERED: int = 0x00080000
WS_EX_NOACTIVATE: int = 0x08000000

SW_HIDE: int = 0
SW_SHOWNOACTIVATE: int = 4

SWP_NOSIZE: int = 0x0001
SWP_NOMOVE: int = 0x0002
SWP_NOACTIVATE: int = 0x0010
HWND_TOPMOST: int = -1

LWA_COLORKEY: int = 0x00000001
LWA_ALPHA: int = 0x00000002

PS_SOLID: int = 0
SRCCOPY: int = 0x00CC0020
BI_RGB: int = 0
DIB_RGB_COLORS: int = 0

NIM_ADD: int = 0x00000000
NIM_MODIFY: int = 0x00000001
NIM_DELETE: int = 0x00000002

NIF_MESSAGE: int = 0x00000001
NIF_ICON: int = 0x00000002
NIF_TIP: int = 0x00000004

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]

class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 1),
    ]

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]

class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]

# GDI and User32 setup
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT), wintypes.BOOL]
user32.InvalidateRect.restype = wintypes.BOOL
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.RoundRect.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int
]
gdi32.RoundRect.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD
]
gdi32.BitBlt.restype = wintypes.BOOL

gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP

user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wintypes.HICON

user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_longlong

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayCommand(Enum):
    StartListening = auto()
    StopListening = auto()
    Quit = auto()


_OVERLAY_FRAME = 0


def render_idle_icon() -> bytes:
    buf = bytearray(ICON_SIZE * ICON_SIZE * 4)
    w = ICON_SIZE
    cx = w // 2

    for y in range(w):
        for x in range(w):
            idx = (y * w + x) * 4
            in_mic = (cx - 2 <= x <= cx + 2) and (7 <= y <= 16)
            in_stand = (cx - 1 <= x <= cx + 1) and (19 < y <= 23)
            in_base = (cx - 4 <= x <= cx + 4) and (23 <= y <= 24)
            dx = float(x - cx)
            dy = float(y - 7)
            in_top = (dx * dx + dy * dy <= 8.0) and (y <= 8)

            if in_mic or in_top or in_stand or in_base:
                buf[idx] = 0x34      # B
                buf[idx + 1] = 0xD3  # G
                buf[idx + 2] = 0x99  # R
                buf[idx + 3] = 0xFF  # A (Emerald green)
    return bytes(buf)


def render_wave_icon(phase: float) -> bytes:
    buf = bytearray(ICON_SIZE * ICON_SIZE * 4)
    w = ICON_SIZE
    cx = w // 2

    for y in range(w):
        for x in range(w):
            idx = (y * w + x) * 4
            in_mic = (cx - 2 <= x <= cx + 2) and (7 <= y <= 16)
            in_stand = (cx - 1 <= x <= cx + 1) and (19 < y <= 23)
            in_base = (cx - 4 <= x <= cx + 4) and (23 <= y <= 24)

            if in_mic or in_stand or in_base:
                buf[idx] = 0x0E      # B
                buf[idx + 1] = 0xA5  # G
                buf[idx + 2] = 0xE9  # R
                buf[idx + 3] = 0xFF  # A (Sky blue)

    cx_f = float(cx)
    cy_f = 12.0
    for wave_i in range(3):
        r = 6.0 + wave_i * 3.5 + phase * 2.5
        alpha_val = int(max(50.0, min(255.0, (1.0 - (phase + wave_i * 0.3) % 1.0) * 255.0)))
        for y in range(w):
            for x in range(w):
                dx = float(x) - cx_f
                dy = float(y) - cy_f
                dist = math.sqrt(dx * dx + dy * dy)
                if abs(dist - r) <= 1.2 and abs(dx) > 2.5:
                    idx = (y * w + x) * 4
                    if buf[idx + 3] == 0:
                        buf[idx] = 0x38
                        buf[idx + 1] = 0xBD
                        buf[idx + 2] = 0xF8
                        buf[idx + 3] = alpha_val
    return bytes(buf)


def rgba_to_hicon(rgba: bytes) -> wintypes.HICON:
    hdc = gdi32.CreateCompatibleDC(None)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = ICON_SIZE
    bmi.bmiHeader.biHeight = -ICON_SIZE
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    bits = ctypes.c_void_p()
    color_bmp = gdi32.CreateDIBSection(
        hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )

    if bits.value:
        dst = (ctypes.c_ubyte * len(rgba)).from_address(bits.value)
        for i in range(0, len(rgba), 4):
            b, g, r, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
            dst[i] = (b * a) // 255
            dst[i + 1] = (g * a) // 255
            dst[i + 2] = (r * a) // 255
            dst[i + 3] = a

    mask_size = ((ICON_SIZE + 15) // 16 * 2 * ICON_SIZE)
    mask_bmi = BITMAPINFO()
    mask_bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    mask_bmi.bmiHeader.biWidth = ICON_SIZE
    mask_bmi.bmiHeader.biHeight = -ICON_SIZE
    mask_bmi.bmiHeader.biPlanes = 1
    mask_bmi.bmiHeader.biBitCount = 1
    mask_bmi.bmiHeader.biCompression = BI_RGB

    mask_bits = ctypes.c_void_p()
    mask_bmp = gdi32.CreateDIBSection(
        hdc, ctypes.byref(mask_bmi), DIB_RGB_COLORS, ctypes.byref(mask_bits), None, 0
    )
    if mask_bits.value:
        ctypes.memset(mask_bits.value, 0, mask_size)

    icon_info = ICONINFO()
    icon_info.fIcon = True
    icon_info.xHotspot = 0
    icon_info.yHotspot = 0
    icon_info.hbmMask = mask_bmp
    icon_info.hbmColor = color_bmp

    hicon = user32.CreateIconIndirect(ctypes.byref(icon_info))
    gdi32.DeleteObject(color_bmp)
    gdi32.DeleteObject(mask_bmp)
    gdi32.DeleteDC(hdc)
    return hicon


_OVERLAY_WNDPROC_KEEP = None
_DEF_WNDPROC_KEEP = None


def overlay_wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    global _OVERLAY_FRAME
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        mem_dc = gdi32.CreateCompatibleDC(hdc)
        mem_bmp = gdi32.CreateCompatibleBitmap(hdc, OVERLAY_W, OVERLAY_H)
        old_bmp = gdi32.SelectObject(mem_dc, mem_bmp)

        # 1. Transparency key fill
        bg_brush = gdi32.CreateSolidBrush(CLR_TRANSPARENT)
        rc = RECT(0, 0, OVERLAY_W, OVERLAY_H)
        user32.FillRect(mem_dc, ctypes.byref(rc), bg_brush)
        gdi32.DeleteObject(bg_brush)

        # 2. Rounded capsule body
        pill_brush = gdi32.CreateSolidBrush(CLR_PILL_BG)
        pill_pen = gdi32.CreatePen(PS_SOLID, 1, CLR_BORDER_ACTIVE)
        ob = gdi32.SelectObject(mem_dc, pill_brush)
        op = gdi32.SelectObject(mem_dc, pill_pen)
        gdi32.RoundRect(mem_dc, 0, 0, OVERLAY_W, OVERLAY_H, 13, 13)
        gdi32.SelectObject(mem_dc, ob)
        gdi32.SelectObject(mem_dc, op)
        gdi32.DeleteObject(pill_brush)
        gdi32.DeleteObject(pill_pen)

        # 3. Wave bars
        bar_brush = gdi32.CreateSolidBrush(CLR_WAVE_BAR)
        bar_pen = gdi32.CreatePen(PS_SOLID, 1, CLR_WAVE_BAR)
        ob = gdi32.SelectObject(mem_dc, bar_brush)
        op = gdi32.SelectObject(mem_dc, bar_pen)

        bar_w = 3
        bar_gap = 3
        start_x = 14
        center_y = OVERLAY_H // 2

        for i in range(5):
            t = (_OVERLAY_FRAME * 0.075) + (i * 0.65)
            amp = 0.35 + 0.65 * abs(math.sin(t))
            half_h = int(2.0 + amp * 4.5)
            bx = start_x + i * (bar_w + bar_gap)
            gdi32.RoundRect(
                mem_dc, bx, center_y - half_h, bx + bar_w, center_y + half_h, 2, 2
            )

        gdi32.SelectObject(mem_dc, ob)
        gdi32.SelectObject(mem_dc, op)
        gdi32.DeleteObject(bar_brush)
        gdi32.DeleteObject(bar_pen)

        # 4. Blit to screen
        gdi32.BitBlt(hdc, 0, 0, OVERLAY_W, OVERLAY_H, mem_dc, 0, 0, SRCCOPY)

        gdi32.SelectObject(mem_dc, old_bmp)
        gdi32.DeleteObject(mem_bmp)
        gdi32.DeleteDC(mem_dc)

        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0

    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def ui_thread(cmd_queue: Queue[TrayCommand], hotkey_label: str = "Ctrl+Win") -> None:
    global _OVERLAY_WNDPROC_KEEP, _DEF_WNDPROC_KEEP, _OVERLAY_FRAME

    _DEF_WNDPROC_KEEP = WNDPROC(user32.DefWindowProcW)
    tray_class_name = "OpenWisprPyTray"
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _DEF_WNDPROC_KEEP
    wc.lpszClassName = tray_class_name
    user32.RegisterClassExW(ctypes.byref(wc))

    tray_hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW, tray_class_name, None, 0,
        0, 0, 0, 0, None, None, None, None
    )

    _OVERLAY_WNDPROC_KEEP = WNDPROC(overlay_wndproc)
    overlay_class_name = "OpenWisprPyOverlay"
    oc = WNDCLASSEXW()
    oc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    oc.lpfnWndProc = _OVERLAY_WNDPROC_KEEP
    oc.lpszClassName = overlay_class_name
    user32.RegisterClassExW(ctypes.byref(oc))

    scr_w = user32.GetSystemMetrics(SM_CXSCREEN)
    scr_h = user32.GetSystemMetrics(SM_CYSCREEN)
    ox = (scr_w - OVERLAY_W) // 2
    oy = scr_h - OVERLAY_H - BOTTOM_OFFSET

    overlay_hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_NOACTIVATE,
        overlay_class_name, None, WS_POPUP,
        ox, oy, OVERLAY_W, OVERLAY_H,
        None, None, None, None
    )

    user32.SetLayeredWindowAttributes(overlay_hwnd, CLR_TRANSPARENT, 250, LWA_COLORKEY | LWA_ALPHA)
    user32.SetWindowPos(
        overlay_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    )

    idle_hicon = rgba_to_hicon(render_idle_icon())
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = tray_hwnd
    nid.uID = 1
    nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = idle_hicon
    ready_tip = f"OpenWispr – Ready ({hotkey_label} to speak)"[:127]
    nid.szTip = ready_tip
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    user32.ShowWindow(overlay_hwnd, SW_HIDE)

    is_listening = False
    was_listening = False
    quit_flag = False
    frame = 0
    current_hicon = idle_hicon

    def watcher():
        while True:
            cmd = cmd_queue.get()
            user32.PostMessageW(tray_hwnd, WM_APP_CMD, cmd.value, 0)
            if cmd == TrayCommand.Quit:
                break

    threading.Thread(target=watcher, daemon=True).start()
    user32.SetTimer(tray_hwnd, 1, 33, None)

    msg = wintypes.MSG()
    while not quit_flag:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret <= 0:
            break

        if msg.message == WM_APP_CMD:
            cmd_val = msg.wParam
            if cmd_val == TrayCommand.StartListening.value:
                is_listening = True
            elif cmd_val == TrayCommand.StopListening.value:
                is_listening = False
            elif cmd_val == TrayCommand.Quit.value:
                quit_flag = True
                break

        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

        if is_listening and not was_listening:
            frame = 0
            _OVERLAY_FRAME = 0
            user32.ShowWindow(overlay_hwnd, SW_SHOWNOACTIVATE)
        elif not is_listening and was_listening:
            user32.ShowWindow(overlay_hwnd, SW_HIDE)

        if is_listening:
            frame = (frame + 1) % 1_000_000
            _OVERLAY_FRAME = frame
            user32.InvalidateRect(overlay_hwnd, None, False)

        was_listening = is_listening

        if is_listening and frame % 4 == 0:
            phase = (frame % 30) / 30.0
            new_icon = rgba_to_hicon(render_wave_icon(phase))
            nid.hIcon = new_icon
            nid.szTip = "OpenWispr – Listening..."
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            if current_hicon != idle_hicon:
                user32.DestroyIcon(current_hicon)
            current_hicon = new_icon
        elif not is_listening and current_hicon != idle_hicon:
            nid.hIcon = idle_hicon
            nid.szTip = ready_tip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            user32.DestroyIcon(current_hicon)
            current_hicon = idle_hicon

    user32.KillTimer(tray_hwnd, 1)
    user32.ShowWindow(overlay_hwnd, SW_HIDE)
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    if current_hicon != idle_hicon:
        user32.DestroyIcon(current_hicon)
    user32.DestroyIcon(idle_hicon)


class TrayHandle:
    def __init__(self, queue: Queue[TrayCommand]) -> None:
        self.queue = queue

    def send(self, cmd: TrayCommand) -> None:
        self.queue.put(cmd)


def spawn_tray(hotkey_label: str = "Ctrl+Win") -> TrayHandle:
    q: Queue[TrayCommand] = Queue()
    t = threading.Thread(target=ui_thread, args=(q, hotkey_label), name="ui-indicator", daemon=True)
    t.start()
    return TrayHandle(q)
