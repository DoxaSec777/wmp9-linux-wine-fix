#!/usr/bin/env python3
"""Probe real WMP controls against a muted mpv picture overlay.

This live integration diagnostic attaches the top-level overlay service to an
already running WMP window, exercises pause, seek, and resume through WMP's GUI,
and compares both players' state.  It does not create or terminate WMP; the
operator must provide an isolated test window and media through environment
variables when the defaults are unsuitable.
"""

import json
import os
from pathlib import Path
import subprocess
import time

from service import BASE, PROBE, discover_wmp_window, mpv_ipc


SOURCE = Path(os.environ.get('WMP9_TEST_SOURCE', str(Path.home() / 'Videos/test-video.mp4'))).expanduser()
AUDIO = Path(os.environ.get(
    'AUDIO_PATH',
    str(Path.home() / '.cache/wmp9-compat/test-video-overlay-audio.avi'),
)).expanduser()
# Discovery at startup intentionally binds every action to one existing WMP GUI.
XID = discover_wmp_window()
LOG = BASE / 'integration.log'


def key(*args):
    """Send a keyboard shortcut to the WMP window selected at startup."""
    subprocess.run(
        ['xdotool', 'windowactivate', str(XID), 'key', '--clearmodifiers', *args],
        check=True,
        timeout=3,
    )


def wmp_state():
    """Return the WMP transport fields needed for synchronization checks."""
    result = subprocess.run(['wine-stable', str(PROBE), '1', '100'],
                            capture_output=True, text=True, timeout=8, check=True)
    samples = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    return {key: samples[0][key] for key in ('playState', 'currentPosition', 'sourceURL')}


def mpv_state():
    """Return pause and position from the single overlay IPC socket."""
    sockets = list(BASE.glob('mpv-top-*.sock'))
    assert len(sockets) == 1, sockets
    sock = sockets[0]
    return {'pause': mpv_ipc(sock, ['get_property', 'pause'], request_id=71),
            'position': mpv_ipc(sock, ['get_property', 'time-pos'], request_id=72)}


def check(label):
    """Capture, print, and return paired WMP/mpv state for one checkpoint."""
    state = {'wmp': wmp_state(), 'mpv': mpv_state()}
    print(label, json.dumps(state, ensure_ascii=False), flush=True)
    return state


if __name__ == '__main__':
    BASE.mkdir(exist_ok=True)
    with LOG.open('w') as log:
        process = subprocess.Popen([
            'python3', str(BASE / 'top_service.py'), str(SOURCE), str(AUDIO),
            '--attach-xid', hex(XID), '--seconds', '24',
        ], stdout=log, stderr=subprocess.STDOUT)
        try:
            # Wait only for this service's IPC endpoint; do not probe unrelated mpv instances.
            deadline = time.monotonic() + 8
            while not list(BASE.glob('mpv-top-*.sock')) and time.monotonic() < deadline:
                time.sleep(0.1)
            key('ctrl+p')
            time.sleep(3)
            key('ctrl+p')
            time.sleep(1)
            paused = check('PAUSED')
            subprocess.run(['xdotool', 'mousemove', '700', '997', 'click', '1'],
                           check=True, timeout=3)
            time.sleep(1.5)
            seek = check('SEEK')
            subprocess.run([
                'xfce4-screenshooter', '-f', '-s', str(BASE / 'integration-seek.png')
            ], check=True, timeout=5)
            key('ctrl+p')
            time.sleep(2)
            resumed = check('RESUMED')
            assert paused['wmp']['playState'] == 2 and paused['mpv']['pause'] is True, 'pause failed'
            assert abs(seek['wmp']['currentPosition'] - paused['wmp']['currentPosition']) > 3, 'WMP GUI seek failed'
            assert abs(seek['wmp']['currentPosition'] - seek['mpv']['position']) < 1.5, 'picture seek mismatch'
            assert resumed['wmp']['playState'] == 3 and resumed['mpv']['pause'] is False, 'resume failed'
            print('SYNC_OK', flush=True)
        finally:
            # The bounded service exits itself; this probe deliberately leaves WMP untouched.
            return_code = process.wait(timeout=30)
            print('SERVICE_EXIT', return_code, flush=True)
