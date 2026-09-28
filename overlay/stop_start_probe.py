#!/usr/bin/env python3
"""Probe stopping WMP at first sound and resuming after video appears.

The staging-only diagnostic tests whether a transport stop can suppress early
WMP audio until the overlay is mapped, then verifies WMP and mpv resume.  It
refuses an existing WMP GUI or sink and limits cleanup to the launched process,
its observed window, and a verified WMP owner.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from close_audio_probe import is_wmp_owner, wmp_audio_sink
from gui_acceptance import BASE, PROBE, SOURCE, STAGING, command, eventually, remote_sample, window_id
from service import mpv_ipc, x_window_info


def overlay_ids():
    """Return all managed overlay window IDs visible to Xdotool."""
    result = subprocess.run(['xdotool', 'search', '--name', 'HAL-WMP-Overlay'],
                            text=True, capture_output=True, timeout=2)
    return [int(value) for value in result.stdout.splitlines() if value.strip()]


def live_mpv():
    """Return the pause state from a responsive overlay IPC socket."""
    for sock in BASE.glob('mpv-top-*.sock'):
        try:
            return mpv_ipc(sock, ['get_property', 'pause'], request_id=913)
        except OSError:
            pass
    raise RuntimeError('mpv IPC unavailable')


def main():
    """Run the stop-before-picture experiment and report transport timing."""
    if window_id('Windows Media Player') or wmp_audio_sink():
        raise RuntimeError('Existing WMP GUI/audio; abort')

    env = os.environ.copy()
    env.update(WMP9_OVERLAY_DIR=str(BASE), WMP_OVERLAY_PROBE=str(PROBE),
               WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
               WINEDEBUG='-all')
    with (BASE / 'stop-start-launch.log').open('w') as log:
        launcher = subprocess.Popen([sys.executable, str(STAGING), str(SOURCE)],
                                    env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
        start = time.monotonic()
        events = {}
        xid = owner = None
        try:
            while time.monotonic() - start < 12:
                if xid is None:
                    xid = window_id('Windows Media Player')
                    if xid:
                        events['gui'] = round(time.monotonic() - start, 2)
                        owner = int(command('xdotool', 'getwindowpid', str(xid)).strip())
                if xid and 'stop' not in events and wmp_audio_sink():
                    events['stop'] = round(time.monotonic() - start, 2)
                    command('xdotool', 'windowactivate', str(xid), 'key',
                            '--clearmodifiers', 'ctrl+s')
                if 'stop' in events:
                    for wid in overlay_ids():
                        rect = x_window_info(wid)
                        if rect and rect[4]:
                            events['picture'] = round(time.monotonic() - start, 2)
                            break
                if 'picture' in events:
                    break
                time.sleep(0.1)
            if 'picture' not in events:
                raise AssertionError(f'picture missing: {events}')

            stopped = remote_sample()
            events['stopped_state'] = stopped.get('playState')
            events['stopped_position'] = stopped.get('currentPosition')
            command('xdotool', 'windowactivate', str(xid), 'key',
                    '--clearmodifiers', 'ctrl+p')
            playing = eventually(
                'WMP resumed',
                lambda: (sample if sample.get('playState') == 3 else None)
                if (sample := remote_sample()) else None,
                6,
            )
            eventually('mpv resumed', lambda: live_mpv() is False, 5)
            events['resumed_position'] = playing['currentPosition']
            events['resumed_time'] = round(time.monotonic() - start, 2)
            print('STOP_START', json.dumps(events), flush=True)
        finally:
            # Do not disturb unrelated Wine processes during cleanup.
            if xid and window_id('Windows Media Player') == xid:
                subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=3)
            try:
                launcher.wait(timeout=8)
            except subprocess.TimeoutExpired:
                launcher.terminate()
                launcher.wait(timeout=3)
            if owner and window_id('Windows Media Player') is None and is_wmp_owner(owner):
                os.kill(owner, signal.SIGTERM)
            time.sleep(0.5)
            print('CLEANUP', json.dumps({
                'launcher_rc': launcher.returncode,
                'sink': wmp_audio_sink(),
                'overlay': overlay_ids(),
            }), flush=True)


if __name__ == '__main__':
    main()
