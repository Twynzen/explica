#!/usr/bin/env python
"""Minimal XTEST input injection via ctypes (no xdotool needed).

Usage: xtest.py :2 [key g | key ctrl+s | move X Y | click 1 | sleep S] ...
Used by scripts/kdenlive_headless_roundtrip.sh against a private Xwayland display.
"""

from __future__ import annotations

import ctypes
import sys
import time

X11 = ctypes.CDLL("libX11.so.6")
XT = ctypes.CDLL("libXtst.so.6")
X11.XOpenDisplay.restype = ctypes.c_void_p
X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
X11.XStringToKeysym.restype = ctypes.c_ulong
X11.XStringToKeysym.argtypes = [ctypes.c_char_p]
X11.XKeysymToKeycode.restype = ctypes.c_ubyte
X11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
X11.XFlush.argtypes = [ctypes.c_void_p]
X11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
XT.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
XT.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
XT.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]

MODS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L"}


def main(argv: list[str]) -> int:
    display = X11.XOpenDisplay(argv[0].encode())
    if not display:
        raise SystemExit("no display")

    def keycode(name: str) -> int:
        return X11.XKeysymToKeycode(display, X11.XStringToKeysym(name.encode()))

    args = list(argv[1:])
    while args:
        cmd = args.pop(0)
        if cmd == "key":
            combo = args.pop(0).split("+")
            mods = [MODS[m] for m in combo[:-1]]
            key = combo[-1]
            for m in mods:
                XT.XTestFakeKeyEvent(display, keycode(m), 1, 0)
            XT.XTestFakeKeyEvent(display, keycode(key), 1, 0)
            XT.XTestFakeKeyEvent(display, keycode(key), 0, 0)
            for m in reversed(mods):
                XT.XTestFakeKeyEvent(display, keycode(m), 0, 0)
        elif cmd == "move":
            x, y = int(args.pop(0)), int(args.pop(0))
            XT.XTestFakeMotionEvent(display, -1, x, y, 0)
        elif cmd == "click":
            button = int(args.pop(0))
            XT.XTestFakeButtonEvent(display, button, 1, 0)
            XT.XTestFakeButtonEvent(display, button, 0, 0)
        elif cmd == "sleep":
            X11.XFlush(display)
            time.sleep(float(args.pop(0)))
        else:
            raise SystemExit(f"unknown command {cmd!r}")
        X11.XFlush(display)
        X11.XSync(display, 0)
        time.sleep(0.15)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
