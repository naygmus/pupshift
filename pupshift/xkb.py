# -*- coding: utf-8 -*-
"""Состояние и переключение группы раскладок через XKB (ctypes, libX11).

python-xlib 0.33 не содержит XKB-расширение, поэтому используется отдельное
X-соединение через libX11: XkbGetState (текущая группа) и XkbLockGroup.
"""
import ctypes
import subprocess

XkbUseCoreKbd = 0x100


class XkbError(RuntimeError):
    pass


class Xkb:
    def __init__(self, display=":0"):
        self._x11 = ctypes.CDLL("libX11.so.6")
        self._x11.XOpenDisplay.restype = ctypes.c_void_p
        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._dpy = self._x11.XOpenDisplay(display.encode())
        if not self._dpy:
            raise XkbError("не удалось открыть дисплей %s" % display)
        self._x11.XkbGetState.restype = ctypes.c_int
        self._x11.XkbGetState.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        self._x11.XkbLockGroup.restype = ctypes.c_int
        self._x11.XkbLockGroup.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        self._x11.XSync.restype = ctypes.c_int
        self._x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def get_group(self):
        buf = (ctypes.c_ubyte * 32)()
        st = self._x11.XkbGetState(self._dpy, XkbUseCoreKbd, buf)
        if st != 0:
            raise XkbError("XkbGetState вернул %s" % st)
        return buf[0]

    def lock_group(self, group):
        self._x11.XkbLockGroup(self._dpy, XkbUseCoreKbd, group)
        self._x11.XSync(self._dpy, 0)   # гарантируем применение до печати

    def close(self):
        if self._dpy:
            self._x11.XCloseDisplay(self._dpy)
            self._dpy = None


def get_layouts():
    """Список раскладок из setxkbmap -query, например ['us', 'ru']."""
    try:
        out = subprocess.run(["setxkbmap", "-query"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    for line in out.splitlines():
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[0].strip() == "layout":
            return [x.strip() for x in parts[1].split(",") if x.strip()]
    return []


def group_index(layouts, name):
    """Индекс группы раскладки name или None."""
    try:
        return layouts.index(name)
    except ValueError:
        return None
