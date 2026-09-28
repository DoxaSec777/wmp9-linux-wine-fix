#!/usr/bin/env python3
"""Measure the staged interval between WMP audio and overlay mapping.

This diagnostic records GUI discovery, WMP sink creation, and the first mapped
overlay window.  Mapping is intentionally treated only as a timing signal, not
proof of rendered pixels or audible samples.  The probe refuses an active WMP
session and cleans up only the GUI, launcher, and verified owner it observed.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from close_audio_probe import is_wmp_owner, wmp_audio_sink
from gui_acceptance import BASE, PROBE, SOURCE, STAGING, command, window_id
from service import x_window_info


def overlay_ids():
    """Return all X11 window IDs carrying the managed overlay title."""
    result = subprocess.run(['xdotool', 'search', '--name', 'HAL-WMP-Overlay'],
                            text=True, capture_output=True, timeout=2)
    return [int(line) for line in result.stdout.splitlines() if line.strip()]


def main():
    """Launch one fixture, timestamp startup milestones, and clean up safely."""
    if window_id('Windows Media Player') or wmp_audio_sink():
        raise RuntimeError('WMP GUI/audio already in use; timing test aborted')

    env = os.environ.copy()
    env.update(WMP9_OVERLAY_DIR=str(BASE), WMP_OVERLAY_PROBE=str(PROBE),
               WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
               WINEDEBUG='-all')
    with (BASE / 'startup-timing-launch.log').open('w') as log:
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
                        events['gui'] = time.monotonic() - start
                        owner = int(command('xdotool', 'getwindowpid', str(xid)).strip())
                        command('wmctrl', '-ia', hex(xid))
                if 'audio' not in events and wmp_audio_sink():
                    events['audio'] = time.monotonic() - start
                if 'picture' not in events:
                    for wid in overlay_ids():
                        rect = x_window_info(wid)
                        if rect and rect[4]:
                            events['picture'] = time.monotonic() - start
                            break
                if 'audio' in events and 'picture' in events:
                    break
                time.sleep(0.1)
            events['sound_before_picture'] = (
                round(events['picture'] - events['audio'], 2)
                if 'audio' in events and 'picture' in events else None
            )
            print('STARTUP', json.dumps(events), flush=True)
            if not all(key in events for key in ('gui', 'audio', 'picture')):
                raise AssertionError('WMP/overlay did not start within 12 seconds')
        finally:
            # Avoid global Wine termination; signal only this run's verified owner.
            if xid and window_id('Windows Media Player') == xid:
                subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=3)
            try:
                launcher.wait(timeout=8)
            except subprocess.TimeoutExpired:
                launcher.terminate()
                launcher.wait(timeout=3)
            if owner and window_id('Windows Media Player') is None and is_wmp_owner(owner):
                os.kill(owner, signal.SIGTERM)
            time.sleep(0.4)
            print('CLEANUP', json.dumps({
                'launcher_rc': launcher.returncode,
                'wmp_owner': is_wmp_owner(owner) if owner else None,
                'sink': wmp_audio_sink(),
                'overlay': overlay_ids(),
            }), flush=True)


if __name__ == '__main__':
    main()
