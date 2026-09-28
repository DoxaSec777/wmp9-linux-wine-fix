#!/usr/bin/env python3
"""Exercise staged WMP transport, mpv synchronization, and GUI lifecycle.

The acceptance run uses the real desktop and audio session.  It verifies pause,
seek, resume, minimize/restore, and resize alignment while collecting
screenshots.  To avoid disturbing a user's session, it refuses to run when WMP
or the overlay is already open and closes only the window it launched.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import ImageGrab

from service import BASE, mpv_ipc, x_window_info


SOURCE = Path(os.environ.get('WMP9_TEST_SOURCE', str(Path.home() / 'Videos/test-video.mp4'))).expanduser()
STAGING = BASE / 'wmp9-compat-staging'
PROBE = BASE / 'bridge/wmp_remote_probe.exe'


def command(*args, timeout=4):
    """Run a bounded diagnostic command and return its standard output."""
    return subprocess.run(
        args, check=True, text=True, capture_output=True, timeout=timeout
    ).stdout


def eventually(label, predicate, seconds=8):
    """Poll *predicate* until it returns a truthy value or fail with context."""
    until = time.monotonic() + seconds
    last = None
    while time.monotonic() < until:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
            # Live X11 clients can disappear between discovery and inspection.
            last = repr(exc)
        time.sleep(0.2)
    raise AssertionError(f'{label}: {last!r}')


def window_id(title):
    """Return the newest matching X11 client ID, or ``None`` when absent."""
    if title == 'HAL-WMP-Overlay':
        result = subprocess.run(['xdotool', 'search', '--name', title],
                                text=True, capture_output=True, timeout=3)
        lines = result.stdout.splitlines()
        return int(lines[-1]) if lines else None
    return next((int(line.split()[0], 16) for line in command('wmctrl', '-l').splitlines()
                 if line.endswith(' ' + title)), None)


def remote_sample():
    """Read one state sample from the WMP remote COM probe."""
    stdout = command('wine-stable', str(PROBE), '1', '100', timeout=7)
    return next(json.loads(line) for line in stdout.splitlines()
                if line.startswith('{') and json.loads(line).get('type') == 'sample')


def mpv_state():
    """Return the pause flag and playback position from the live overlay."""
    for sock in BASE.glob('mpv-top-*.sock'):
        try:
            return (mpv_ipc(sock, ['get_property', 'pause'], request_id=601),
                    mpv_ipc(sock, ['get_property', 'time-pos'], request_id=602))
        except (OSError, RuntimeError):
            continue
    raise RuntimeError('No live mpv IPC socket')


def key(xid, text):
    """Send one cleared-modifier shortcut to the selected WMP window."""
    command('xdotool', 'windowactivate', str(xid), 'key', '--clearmodifiers', text)


def video_rect(sample):
    """Extract the visible WMP visualization rectangle from a probe sample."""
    return next(tuple(win['rect']) for win in sample['windows']
                if win['class'].startswith('WMP Visualization Window') and win['visible'])


def main():
    """Run the end-to-end GUI acceptance sequence and save visual evidence."""
    if not PROBE.is_file():
        raise FileNotFoundError(PROBE)
    if window_id('Windows Media Player') or window_id('HAL-WMP-Overlay'):
        raise RuntimeError('Test requires no existing WMP or overlay window')

    env = os.environ.copy()
    env.update(WMP9_OVERLAY_DIR=str(BASE), WMP_OVERLAY_PROBE=str(PROBE),
               WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
               WINEDEBUG='-all')
    log = (BASE / 'gui-acceptance-launch.log').open('w')
    launcher = subprocess.Popen([sys.executable, str(STAGING), str(SOURCE)],
                                env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT)
    xid = None
    evidence = BASE / ('transport-' + str(time.time_ns()))
    evidence.mkdir()
    try:
        xid = eventually('WMP window', lambda: window_id('Windows Media Player'), 15)
        command('wmctrl', '-ia', hex(xid))
        wid = eventually('mpv overlay window', lambda: window_id('HAL-WMP-Overlay'), 16)
        command('wmctrl', '-ia', hex(xid))
        eventually('WMP playing', lambda: remote_sample()['playState'] == 3, 6)
        command('wmctrl', '-ia', hex(xid))
        eventually('overlay visible', lambda: x_window_info(wid)[4], 4)
        before = remote_sample()['currentPosition']
        ImageGrab.grab(xdisplay=':0').save(evidence / 'before-seek.png')

        # Validate that WMP remains the transport authority for the muted video.
        key(xid, 'ctrl+p')
        eventually('WMP paused', lambda: remote_sample()['playState'] == 2, 5)
        eventually('mpv paused', lambda: mpv_state()[0] is True, 4)
        geo = x_window_info(xid)
        seek_x, seek_y = geo[0] + int(geo[2] * 0.7), geo[1] + geo[3] - 54
        command('xdotool', 'mousemove', str(seek_x), str(seek_y), 'click', '1')
        after = eventually(
            'WMP seek',
            lambda: (s if abs((s := remote_sample()['currentPosition']) - before) > 3 else None),
            6,
        )
        eventually('mpv follows seek', lambda: abs(mpv_state()[1] - after) < 1.5, 5)
        ImageGrab.grab(xdisplay=':0').save(evidence / 'after-seek.png')
        key(xid, 'ctrl+p')
        eventually('WMP resumed', lambda: remote_sample()['playState'] == 3, 5)
        eventually('mpv resumed', lambda: mpv_state()[0] is False, 4)

        # Minimize and resize checks prove the overlay tracks WMP without focus theft.
        command('xdotool', 'windowminimize', str(xid))
        eventually('overlay hidden on minimize', lambda: x_window_info(wid)[4] is False, 5)
        command('wmctrl', '-ia', hex(xid))
        eventually('overlay visible on restore', lambda: x_window_info(wid)[4] is True, 5)
        command('wmctrl', '-ir', hex(xid), '-b', 'remove,maximized_vert,maximized_horz')
        # Xfwm applies state changes asynchronously; do not race unmaximize/resize.
        time.sleep(0.4)
        command('wmctrl', '-ir', hex(xid), '-e', '0,110,70,1260,880')
        time.sleep(0.3)
        command('wmctrl', '-ia', hex(xid))
        expected = eventually('WMP resized', lambda: (r if r[2] > 800 else None)
                              if (r := video_rect(remote_sample())) else None, 6)
        eventually('overlay aligned', lambda: x_window_info(wid)[:4] == expected, 6)
        print('ACCEPTANCE', json.dumps({'evidence': str(evidence), 'pause': True,
                                        'seek_from': round(before, 2),
                                        'seek_to': round(after, 2), 'resume': True,
                                        'minimize_restore': True, 'video_rect': expected,
                                        'overlay_rect': x_window_info(wid)[:4]}), flush=True)
    finally:
        # Never use global Wine cleanup; close this run's GUI and launcher only.
        if xid:
            subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=4)
        try:
            launcher.wait(timeout=8)
        except subprocess.TimeoutExpired:
            launcher.terminate()
            launcher.wait(timeout=3)
        log.close()
        print('CLEANUP', json.dumps({'launcher_rc': launcher.returncode,
                                     'wmp': window_id('Windows Media Player'),
                                     'overlay': window_id('HAL-WMP-Overlay')}), flush=True)


if __name__ == '__main__':
    main()
