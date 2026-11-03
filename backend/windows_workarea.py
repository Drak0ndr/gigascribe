"""Windows-only taskbar-safe resize for pywebview's frameless window.

Avoid FormWindowState.Maximized on borderless WinForms: that can overlap the
Windows taskbar. Use the *current monitor's* work area, and restore the former
window rectangle on the next click. Win32 coordinates remain in native physical
pixels and are not mixed with pywebview's logical-DPI window.resize() values.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def _configure_api(user32):
    """Set pointer-width-safe ctypes declarations on real Windows DLL calls."""
    signatures = (
        ('MonitorFromWindow', [wintypes.HWND, wintypes.DWORD], wintypes.HMONITOR),
        ('GetMonitorInfoW', [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)], wintypes.BOOL),
        ('GetWindowRect', [wintypes.HWND, ctypes.POINTER(RECT)], wintypes.BOOL),
        ('SetWindowPos', [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
    )
    for name, args, result in signatures:
        function = getattr(user32, name)
        # Tests may provide ordinary Python callables instead of ctypes functions.
        if hasattr(function, 'argtypes'):
            function.argtypes = args
            function.restype = result


def toggle_work_area(hwnd: int, old_bounds: tuple[int, int, int, int] | None,
                     user32=None) -> tuple[int, int, int, int] | None:
    """Toggle frameless maximize; return saved normal bounds or None if restored.

    Work area comes from the monitor containing *this window*, not the primary
    display. Handles taskbar positioned along any edge. The caller owns state.
    """
    if not hwnd:
        raise RuntimeError('Windows window handle is not ready')
    if user32 is None:
        user32 = ctypes.windll.user32
    _configure_api(user32)

    if old_bounds is None:
        bounds = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
            raise ctypes.WinError()
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            raise RuntimeError('Unable to identify the current monitor')
        monitor_info = MONITORINFO()
        monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            raise ctypes.WinError()
        target = monitor_info.rcWork
        width, height = target.right - target.left, target.bottom - target.top
        if width <= 0 or height <= 0:
            raise RuntimeError('Invalid Windows monitor work area')
        restore = (bounds.left, bounds.top, bounds.right - bounds.left,
                   bounds.bottom - bounds.top)
        new_rect = (target.left, target.top, width, height)
    else:
        restore = None
        new_rect = old_bounds

    # SWP_NOZORDER | SWP_NOACTIVATE; do not force fullscreen or change z-order.
    if not user32.SetWindowPos(hwnd, None, *new_rect, 0x0004 | 0x0010):
        raise ctypes.WinError()
    return restore
