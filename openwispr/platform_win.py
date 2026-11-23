"""
Windows integration: global keyboard hook, text insertion and sound cues.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import struct
import sys
import threading
import time
from enum import Enum, auto
from queue import Queue
from openwispr.hotkey import Action, Chord, Machine

# Win32 Constants
OUR_INPUT: int = 0x00574953  # "WIS"
VK_MASK: int = 0xE8

WH_KEYBOARD_LL: int = 13
HC_ACTION: int = 0

WM_QUIT: int = 0x0012
WM_KEYDOWN: int = 0x0100
WM_KEYUP: int = 0x0101
WM_SYSKEYDOWN: int = 0x0104
WM_SYSKEYUP: int = 0x0105

VK_RETURN: int = 0x0D
VK_SHIFT: int = 0x10
VK_CONTROL: int = 0x11
VK_MENU: int = 0x12
VK_LWIN: int = 0x5B
VK_RWIN: int = 0x5C

INPUT_KEYBOARD: int = 1
KEYEVENTF_KEYUP: int = 0x0002
KEYEVENTF_UNICODE: int = 0x0004

CF_UNICODETEXT: int = 13
GMEM_MOVEABLE: int = 0x0002

SND_ASYNC: int = 0x0001
SND_NODEFAULT: int = 0x0002
SND_MEMORY: int = 0x0004

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm

# Setup Win32 function signatures
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT

user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL

user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL

user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL

user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE

user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL

kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p

kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL

# Structures for WH_KEYBOARD_LL and SendInput
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("padding", ctypes.c_byte * 32),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTunion),
    ]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_longlong

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL

user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_longlong

# With SND_MEMORY the first argument points at an in-memory WAV, not a string.
winmm.PlaySoundW.argtypes = [ctypes.c_void_p, wintypes.HMODULE, wintypes.DWORD]
winmm.PlaySoundW.restype = wintypes.BOOL


# Keep hook proc reference alive to prevent GC
_GLOBAL_HOOK_PROC: HOOKPROC | None = None
_HOOK_HANDLE: wintypes.HHOOK | None = None
_HOOK_THREAD: threading.Thread | None = None


def is_pressed(vk: int) -> bool:
    """Queries global key state."""
    return user32.GetAsyncKeyState(int(vk)) < 0


def _key_input(vk: int, scan: int, flags: int) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = scan
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = OUR_INPUT
    return inp


def send_inputs(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def send_keys(keys: list[tuple[int, bool]]) -> None:
    inputs = [
        _key_input(vk, 0, 0 if down else KEYEVENTF_KEYUP)
        for vk, down in keys
    ]
    send_inputs(inputs)


def wait_for_modifiers_released() -> None:
    """
    Waits (briefly) until the user lets go of modifier keys, so that our Ctrl+V
    isn't turned into e.g. Win+Ctrl+V.
    """
    deadline = time.time() + 1.5
    mods = [VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN]
    while time.time() < deadline and any(is_pressed(k) for k in mods):
        time.sleep(0.010)


def get_clipboard_text() -> str | None:
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str, exclude_from_history: bool = True) -> bool:
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        encoded = (text + "\0").encode("utf-16le")
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        ptr = kernel32.GlobalLock(h_mem)
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)

        if exclude_from_history:
            fmt1 = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
            h1 = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            p1 = kernel32.GlobalLock(h1)
            ctypes.memset(p1, 0, 4)
            kernel32.GlobalUnlock(h1)
            user32.SetClipboardData(fmt1, h1)

            fmt2 = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
            h2 = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            p2 = kernel32.GlobalLock(h2)
            ctypes.memset(p2, 0, 4)
            kernel32.GlobalUnlock(h2)
            user32.SetClipboardData(fmt2, h2)

            fmt3 = user32.RegisterClipboardFormatW("CanUploadToCloud")
            h3 = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            p3 = kernel32.GlobalLock(h3)
            ctypes.memset(p3, 0, 4)
            kernel32.GlobalUnlock(h3)
            user32.SetClipboardData(fmt3, h3)

        return True
    finally:
        user32.CloseClipboard()


def paste_text(text: str) -> None:
    """Inserts text into the focused app by pasting it, then restores clipboard."""
    previous = get_clipboard_text()
    set_clipboard_text(text, exclude_from_history=True)
    wait_for_modifiers_released()
    # Send Ctrl+V
    send_keys([(VK_CONTROL, True), (0x56, True), (0x56, False), (VK_CONTROL, False)])
    # Give target app time to read clipboard before restoring it
    time.sleep(0.250)
    if previous is not None:
        set_clipboard_text(previous, exclude_from_history=True)


def type_text(text: str) -> None:
    """Inserts text by synthesizing Unicode keystrokes (leaves clipboard alone)."""
    wait_for_modifiers_released()
    inputs: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            inputs.append(_key_input(VK_RETURN, 0, 0))
            inputs.append(_key_input(VK_RETURN, 0, KEYEVENTF_KEYUP))
            continue
        units = ch.encode("utf-16le")
        for i in range(0, len(units), 2):
            unit = struct.unpack("<H", units[i : i + 2])[0]
            inputs.append(_key_input(0, unit, KEYEVENTF_UNICODE))
            inputs.append(_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    send_inputs(inputs)


def spawn_hotkey_listener(chord: Chord, action_queue: Queue[Action]) -> None:
    """
    Installs a low-level keyboard hook on a dedicated thread and puts hotkey
    actions into action_queue.
    """
    global _GLOBAL_HOOK_PROC, _HOOK_HANDLE, _HOOK_THREAD

    ready_event = threading.Event()
    machine = Machine(chord)

    def hook_procedure(code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION:
            info = KBDLLHOOKSTRUCT.from_address(lparam)
            if info.dwExtraInfo != OUR_INPUT:
                vk = info.vkCode
                is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                machine.retain_down(lambda k: k == vk or is_pressed(k))
                outcome = machine.on_key(vk, is_down, time.time())
                if outcome.action is not None:
                    action_queue.put(outcome.action)
                if outcome.mask:
                    send_keys([(VK_MASK, True), (VK_MASK, False)])
                if outcome.suppress:
                    return 1

        return user32.CallNextHookEx(_HOOK_HANDLE, code, wparam, lparam)

    _GLOBAL_HOOK_PROC = HOOKPROC(hook_procedure)

    def listener_thread():
        global _HOOK_HANDLE
        _HOOK_HANDLE = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, _GLOBAL_HOOK_PROC, None, 0
        )
        if not _HOOK_HANDLE:
            ready_event.set()
            return

        ready_event.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    th = threading.Thread(target=listener_thread, name="hotkey", daemon=True)
    _HOOK_THREAD = th
    th.start()

    ready_event.wait(timeout=5.0)
    if not _HOOK_HANDLE:
        raise RuntimeError("could not install the keyboard hook")


def stop_hotkey_listener() -> None:
    global _GLOBAL_HOOK_PROC, _HOOK_HANDLE, _HOOK_THREAD

    handle = _HOOK_HANDLE
    _HOOK_HANDLE = None
    if handle:
        user32.UnhookWindowsHookEx(handle)

    thread = _HOOK_THREAD
    if thread is not None and thread.is_alive():
        user32.PostThreadMessageW(thread.ident, WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
    _HOOK_THREAD = None
    _GLOBAL_HOOK_PROC = None


class Cue(Enum):
    Start = auto()
    Stop = auto()
    Cancel = auto()


def tone_wav(freqs: list[float], rate: int = 22_050) -> bytes:
    """Builds an in-memory 16-bit mono WAV of consecutive short tones."""
    per_tone = rate * 45 // 1000
    pcm = bytearray()
    for f in freqs:
        for i in range(per_tone):
            t = i / rate
            env = min(min(i, per_tone - i) / (rate * 0.005), 1.0)  # 5 ms fades
            s = math.sin(t * f * 2.0 * math.pi) * env * 0.18
            val = int(max(-32768, min(32767, s * 32767.0)))
            pcm.extend(struct.pack("<h", val))

    header = bytearray()
    header.extend(b"RIFF")
    header.extend(struct.pack("<I", 36 + len(pcm)))
    header.extend(b"WAVEfmt ")
    header.extend(struct.pack("<I", 16))
    header.extend(struct.pack("<H", 1))  # PCM
    header.extend(struct.pack("<H", 1))  # mono
    header.extend(struct.pack("<I", rate))
    header.extend(struct.pack("<I", rate * 2))
    header.extend(struct.pack("<H", 2))
    header.extend(struct.pack("<H", 16))
    header.extend(b"data")
    header.extend(struct.pack("<I", len(pcm)))
    return bytes(header + pcm)


# Module-level buffers: with SND_ASYNC the memory must outlive the call.
_CUE_SOUNDS: dict[Cue, ctypes.Array] = {
    Cue.Start: ctypes.create_string_buffer(tone_wav([880.0, 1320.0])),
    Cue.Stop: ctypes.create_string_buffer(tone_wav([1320.0, 880.0])),
    Cue.Cancel: ctypes.create_string_buffer(tone_wav([440.0, 330.0])),
}


def play_cue(cue: Cue) -> None:
    """Plays a short, quiet beep without blocking."""
    buf = _CUE_SOUNDS.get(cue)
    if buf is not None:
        winmm.PlaySoundW(ctypes.addressof(buf), None, SND_MEMORY | SND_ASYNC | SND_NODEFAULT)
