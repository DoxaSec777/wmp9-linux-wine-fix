#!/usr/bin/env python3
"""Run the legacy X11-child WMP9/mpv overlay service.

The real WMP9 GUI owns playback and audio.  A separate native bridge attaches
to that GUI through WMP remoting and emits newline-delimited COM samples; this
process consumes those samples and drives a muted mpv instance.  mpv is placed
inside an X11 child of Wine's top-level client, so all reported Win32 screen
coordinates must be converted to X11 client coordinates before use.

This embedded mode is retained for diagnostics.  Some Wine render paths paint
over X11 children, for which :mod:`top_service` uses a separately managed
top-level window.  Neither service changes WMP DLLs, registry data, media
databases, or source files.
"""
from __future__ import annotations
import argparse
import ctypes as C
import ctypes.util
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import subprocess
import sys
import time
import uuid
from controller import matching_media, mpv_command, mpv_updates, parse_xwininfo, remote_video_rect

BASE = Path.home() / '.cache' / 'hal-wmp-overlay'
PROBE = Path(os.environ.get('WMP_OVERLAY_PROBE', str(Path.home() / '.local/share/wmp9-compat/wmp_remote_probe.exe')))
PREFIX = Path(os.environ.get('WMP9_PREFIX',
                             os.environ.get('WINEPREFIX',
                                            str(Path.home() / '.wine-wmp9-real'))))


def info(text: str) -> None:
    """Write one immediately flushed, machine-searchable overlay log line."""
    print('WMP_OVERLAY ' + text, file=sys.stderr, flush=True)


def x_window_info(xid: int) -> tuple[int, int, int, int, bool] | None:
    """Read the Wine X11 client's screen origin, client size, and map state."""
    output = subprocess.run(['xwininfo', '-id', hex(xid)], text=True,
                            capture_output=True, timeout=2)
    if output.returncode:
        return None
    width, height, mapped = parse_xwininfo(output.stdout)
    def number(name: str) -> int:
        """Extract one signed integer field from the current xwininfo output."""
        match = re.search(r'^\s*' + re.escape(name) + r':\s*(-?\d+)', output.stdout, re.MULTILINE)
        if match is None:
            raise RuntimeError('xwininfo missing ' + name)
        return int(match.group(1))
    return number('Absolute upper-left X'), number('Absolute upper-left Y'), width, height, mapped


class XChild:
    """Own an X11 child whose lifetime is tied to a private Display connection.

    Coordinates supplied to this object are relative to ``parent``.  Closing
    the Display connection is the reliable cleanup path: X11 destroys resources
    owned by that client even when the Wine parent disappeared first.
    """

    def __init__(self, parent: int, rect: tuple[int, int, int, int]):
        """Create and map a black child window inside the selected Wine client."""
        xlib = C.CDLL(ctypes.util.find_library('X11'))
        xlib.XOpenDisplay.argtypes = [C.c_char_p]
        xlib.XOpenDisplay.restype = C.c_void_p
        xlib.XCreateSimpleWindow.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int,
                                              C.c_uint, C.c_uint, C.c_uint, C.c_ulong, C.c_ulong]
        xlib.XCreateSimpleWindow.restype = C.c_ulong
        xlib.XMoveResizeWindow.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint]
        xlib.XMapWindow.argtypes = [C.c_void_p, C.c_ulong]
        xlib.XRaiseWindow.argtypes = [C.c_void_p, C.c_ulong]
        xlib.XFlush.argtypes = [C.c_void_p]
        xlib.XCloseDisplay.argtypes = [C.c_void_p]
        self.xlib = xlib
        self.display = xlib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('X11 display unavailable')
        self.parent = parent
        self.rect = rect
        self.wid = xlib.XCreateSimpleWindow(self.display, parent, *rect, 0, 0, 0)
        if not self.wid:
            xlib.XCloseDisplay(self.display)
            raise RuntimeError('X11 video child could not be created')
        xlib.XMapWindow(self.display, self.wid)
        xlib.XRaiseWindow(self.display, self.wid)
        xlib.XFlush(self.display)

    def resize(self, rect: tuple[int, int, int, int]) -> None:
        """Move and resize the child only when its target rectangle changed."""
        if rect != self.rect:
            self.rect = rect
            self.xlib.XMoveResizeWindow(self.display, self.wid, *rect)
            self.xlib.XFlush(self.display)
            info('VIDEO_RECT ' + repr(rect))

    def close(self) -> None:
        """Close the owning Display connection and thereby destroy the child."""
        # Closing this X connection destroys the X child even if WMP exited.
        if self.display:
            self.xlib.XCloseDisplay(self.display)
            self.display = None


def probe_samples(process: subprocess.Popen):
    """Yield complete JSON samples while keeping probe reads bounded.

    The selector prevents shutdown from blocking forever on a quiet COM host.
    This legacy service consumes every sample synchronously; the top-level
    service uses a separate drain thread and a latest-value queue because its
    window-manager operations can be slower than the probe cadence.
    """
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while process.poll() is None:
            if not selector.select(timeout=1):
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get('type') == 'sample':
                yield value
    finally:
        selector.close()


def mpv_ipc(socket_path: Path, command: list, *, request_id: int = 1):
    """Send one mpv JSON IPC request and wait for its matching response."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(0.5)
        conn.connect(str(socket_path))
        conn.sendall((json.dumps({'command': command, 'request_id': request_id}) + '\n').encode())
        data = b''
        while len(data) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            while b'\n' in data:
                line, data = data.split(b'\n', 1)
                reply = json.loads(line)
                if reply.get('request_id') == request_id:
                    if reply.get('error') != 'success':
                        raise RuntimeError(f'mpv {command[0]}: {reply.get("error")}')
                    return reply.get('data')
        raise RuntimeError('mpv IPC response missing')


def discover_wmp_window() -> int:
    """Find the visible X11 client for WMP's full-mode main window."""
    result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class', 'wmplayer.exe'],
                            text=True, capture_output=True, timeout=3)
    if result.returncode:
        raise RuntimeError('WMP9 GUI is not open')
    for raw in reversed(result.stdout.splitlines()):
        xid = int(raw)
        title = subprocess.run(['xdotool', 'getwindowname', raw], text=True,
                               capture_output=True, timeout=2)
        if title.returncode == 0 and title.stdout.strip() == 'Windows Media Player':
            return xid
    raise RuntimeError('WMP9 main window not found')


def run(source: Path, audio: Path, xid: int | None, seconds: float) -> int:
    """Attach one embedded overlay to the matching remote WMP media session."""
    if not source.is_file() or not audio.is_file() or not PROBE.is_file():
        raise FileNotFoundError(f'input/probe unavailable: {source} | {audio} | {PROBE}')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize owners so two overlays cannot race for the same WMP GUI/audio.
    lockfile = (BASE / 'overlay.lock').open('w')
    fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
    child = None
    player = None
    probe = None
    socket_path = BASE / ('mpv-' + uuid.uuid4().hex[:14] + '.sock')
    probe_log = (BASE / 'probe.stderr').open('a')
    mpv_log = (BASE / 'mpv.stderr').open('a')
    start = time.monotonic()
    matched = False
    mismatch_since = None
    last_wmp_state = None
    try:
        env = os.environ.copy()
        env.update(WINEPREFIX=str(PREFIX),
                   WINEDEBUG='-all', WINEDLLOVERRIDES='winedbg.exe=d')
        # The bridge owns its COM apartment.  Keeping COM out of this Python
        # process also isolates Wine/ActiveX teardown from X11 and mpv cleanup.
        probe = subprocess.Popen(['wine-stable', str(PROBE), '0', '100'], env=env,
                                 stdout=subprocess.PIPE, stderr=probe_log,
                                 text=True, bufsize=1)
        for sample in probe_samples(probe):
            if seconds and time.monotonic() - start > seconds:
                break
            if not matching_media(sample, audio):
                if matched:
                    if mismatch_since is None:
                        mismatch_since = time.monotonic()
                    if time.monotonic() - mismatch_since > 1.5:
                        info('WMP changed media; detaching')
                        break
                elif time.monotonic() - start > 30:
                    raise RuntimeError('WMP did not open the expected audio/video cache')
                continue
            matched = True
            mismatch_since = None
            if xid is None:
                xid = discover_wmp_window()
            geo = x_window_info(xid)
            if geo is None:
                info('WMP X11 window closed; detaching')
                break
            x_origin, y_origin, width, height, mapped = geo
            # The bridge rectangle is Win32 screen space; mpv's child needs X11
            # client-relative coordinates validated against the current client.
            target = remote_video_rect(sample, x_origin, y_origin, width, height)
            if not target:
                if child is None and time.monotonic() - start > 30:
                    raise RuntimeError('actual WMP video surface was not located')
                continue
            if child is None:
                child = XChild(xid, target)
                command = mpv_command(source, child.wid, socket_path)
                player = subprocess.Popen(command, stdout=mpv_log,
                                          stderr=subprocess.STDOUT)
                info(f'EMBEDDED child=0x{child.wid:x} wmp=0x{xid:x} rect={target} mpv_pid={player.pid}')
            if player.poll() is not None:
                raise RuntimeError('embedded mpv terminated unexpectedly')
            child.resize(target)
            if not mapped or not socket_path.exists():
                continue
            try:
                position = mpv_ipc(socket_path, ['get_property', 'time-pos'], request_id=10)
                pause = mpv_ipc(socket_path, ['get_property', 'pause'], request_id=11)
                if position is None or pause is None:
                    continue
                state = sample.get('playState')
                seconds_wmp = sample.get('currentPosition')
                if not isinstance(state, int) or not isinstance(seconds_wmp, (int, float)):
                    continue
                if state != last_wmp_state:
                    info(f'WMP_STATE {state} at {seconds_wmp:.3f}')
                    last_wmp_state = state
                for i, change in enumerate(mpv_updates(state, seconds_wmp, position, pause)):
                    mpv_ipc(socket_path, change, request_id=20+i)
                    info('SYNC ' + json.dumps(change))
            except (OSError, RuntimeError, ValueError) as exc:
                info(f'IPC_RETRY {exc}')
        return 0 if matched and child is not None else 1
    finally:
        # Stop producers before destroying the X child and unlinking IPC state.
        # Every wait is bounded so a stuck decoder or COM host cannot orphan the
        # per-run lock indefinitely.
        if player and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=3)
            except subprocess.TimeoutExpired:
                player.kill()
                player.wait()
        if probe and probe.poll() is None:
            probe.terminate()
            try:
                probe.wait(timeout=3)
            except subprocess.TimeoutExpired:
                probe.kill()
                probe.wait()
        if child:
            child.close()
        socket_path.unlink(missing_ok=True)
        probe_log.close()
        mpv_log.close()
        lockfile.close()


def main() -> int:
    """Parse the diagnostic CLI and return a conventional process status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--attach-xid', type=lambda x: int(x, 0))
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    try:
        return run(args.source, args.audio, args.attach_xid, args.seconds)
    except Exception as exc:
        info('ERROR ' + repr(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
