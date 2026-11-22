from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

MUTEX_NAME = "Local\\OpenWispr-SingleInstance"
WINDOW_TITLE = "OpenWispr"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class SessionInstanceGuard:
    def __init__(self) -> None:
        self._handle: int | None = None
        self._owned = False

    def acquire(self) -> bool:
        if self._owned:
            return True
        if sys.platform != "win32":
            self._owned = True
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            return False
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self._owned = True
        return True

    def focus_existing(self) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.BringWindowToTop.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

    def release(self) -> None:
        if not self._owned:
            return
        if self._handle is not None and sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle(self._handle)
        self._handle = None
        self._owned = False
