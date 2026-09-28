#!/usr/bin/env python3
"""Experimentally embed muted mpv inside a selected Wine WMP X11 client.

This throwaway probe creates one X11 child window, asks mpv to render into it,
tracks the parent's video rectangle, and then destroys only that child.  Set
``WMP_XID`` to an isolated WMP test window before use.  The probe never changes
the Wine prefix or sends signals to WMP.
"""

import ctypes as c
import ctypes.util
import os
from pathlib import Path
import subprocess
import time

from controller import parse_xwininfo, video_rect


x = c.CDLL(ctypes.util.find_library('X11'))
x.XOpenDisplay.argtypes = [c.c_char_p]
x.XOpenDisplay.restype = c.c_void_p
x.XCloseDisplay.argtypes = [c.c_void_p]
x.XCreateSimpleWindow.argtypes = [
    c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint,
    c.c_uint, c.c_ulong, c.c_ulong,
]
x.XCreateSimpleWindow.restype = c.c_ulong
x.XMapWindow.argtypes = [c.c_void_p, c.c_ulong]
x.XRaiseWindow.argtypes = [c.c_void_p, c.c_ulong]
x.XFlush.argtypes = [c.c_void_p]
x.XDestroyWindow.argtypes = [c.c_void_p, c.c_ulong]
x.XMoveResizeWindow.argtypes = [
    c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint,
]


def geometry():
    """Return the child-video rectangle derived from the current parent size."""
    text = subprocess.check_output(['xwininfo', '-id', hex(parent)], text=True)
    width, height, mapped = parse_xwininfo(text)
    assert mapped
    return video_rect(width, height)


# All following resources are scoped to the selected parent and released below.
display = x.XOpenDisplay(None)
assert display, 'DISPLAY connection failed'
if 'WMP_XID' not in os.environ:
    raise RuntimeError('Set WMP_XID to an isolated WMP test window before use')
parent = int(os.environ['WMP_XID'], 0)
child = x.XCreateSimpleWindow(display, parent, *geometry(), 0, 0, 0)
assert child
x.XMapWindow(display, child)
x.XRaiseWindow(display, child)
x.XFlush(display)
print('EMBED_XID', hex(child), flush=True)
media = os.environ.get('MEDIA', str(Path.home() / 'Videos/test-video.mp4'))
command = [
    'mpv', '--wid=' + str(child), '--vo=x11', '--no-audio', '--pause=yes',
    '--start=3', '--input-vo-keyboard=no', '--no-osc',
    '--no-input-default-bindings', '--keep-open=always', '--', media,
]
logfile = (Path.home() / '.cache/hal-wmp-xembed.log').open('w')
process = subprocess.Popen(command, stdout=logfile, stderr=subprocess.STDOUT)
try:
    for iteration in range(30):
        target = geometry()
        x.XMoveResizeWindow(display, child, *target)
        x.XFlush(display)
        if iteration == 8:
            # Resize only the selected parent to test child geometry tracking.
            subprocess.run([
                'wmctrl', '-ir', hex(parent), '-b',
                'remove,maximized_vert,maximized_horz',
            ], check=True)
            subprocess.run([
                'wmctrl', '-ir', hex(parent), '-e', '0,1000,100,800,600',
            ], check=True)
            subprocess.run(['wmctrl', '-ia', hex(parent)], check=True)
        if iteration == 15:
            subprocess.run([
                'xfce4-screenshooter', '-f', '-s',
                str(Path.home() / '.cache/hal-wmp-xembed-resized.png'),
            ], check=True)
            print('RESIZED_GEOMETRY', target, flush=True)
        time.sleep(0.2)
    print('SCREENSHOT_OK', process.poll(), flush=True)
finally:
    # Restore the selected parent and remove only resources created by this probe.
    subprocess.run([
        'wmctrl', '-ir', hex(parent), '-b', 'add,maximized_vert,maximized_horz'
    ], check=False)
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    x.XDestroyWindow(display, child)
    x.XCloseDisplay(display)
    logfile.close()
