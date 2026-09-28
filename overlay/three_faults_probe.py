#!/usr/bin/env python3
"""Reproduce startup, resize, and skin-overlay faults in one bounded run.

The staging diagnostic captures timestamps, geometry changes, screenshots, and
remote samples for three historically coupled failure modes: early audio,
lagging resize alignment, and skin-mode overlay placement.  It refuses to run
beside an active WMP session and leaves no owned playback behind; process-level
cleanup is limited to the verified owner discovered from this run's GUI.
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
from service import x_window_info


def main():
    """Collect evidence for all three faults and clean up the staged launch."""
    if window_id('Windows Media Player') or wmp_audio_sink():
        raise RuntimeError('WMP in use')

    evidence = BASE / 'three-faults'
    evidence.mkdir(exist_ok=True)
    env = dict(
        os.environ,
        WMP9_OVERLAY_DIR=str(BASE),
        WMP_OVERLAY_PROBE=str(PROBE),
        WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
        WINEDEBUG='-all',
    )
    log = (evidence / 'launch.log').open('w')
    process = subprocess.Popen(
        [sys.executable, str(STAGING), str(SOURCE)],
        env=env,
        stdout=log,
        stderr=log,
    )
    xid = owner = None
    report = {}
    start = time.monotonic()
    try:
        xid = eventually('WMP', lambda: window_id('Windows Media Player'), 15)
        owner = int(command('xdotool', 'getwindowpid', str(xid)).strip())
        command('wmctrl', '-ia', hex(xid))

        # Record relative startup order; a mapped overlay is diagnostic evidence,
        # not proof that pixels are visible or physical audio is audible.
        while time.monotonic() - start < 20:
            if wmp_audio_sink() and 'audio_stream' not in report:
                report['audio_stream'] = time.monotonic() - start
            wid = window_id('HAL-WMP-Overlay')
            if wid and x_window_info(wid) and x_window_info(wid)[4]:
                report['overlay_mapped'] = time.monotonic() - start
                break
            time.sleep(0.05)
        command('import', '-window', 'root', str(evidence / 'normal.png'))
        sample = remote_sample()
        (evidence / 'normal.json').write_text(json.dumps(sample, indent=2))

        # Capture every geometry transition after representative WMP resizes.
        command('wmctrl', '-ir', hex(xid), '-b', 'remove,maximized_vert,maximized_horz')
        time.sleep(0.5)
        report['resize'] = []
        for width, height in [(1000, 740), (1400, 920), (1100, 780)]:
            begin = time.monotonic()
            command('wmctrl', '-ir', hex(xid), '-e', f'0,100,70,{width},{height}')
            old = x_window_info(wid)
            changes = []
            while time.monotonic() - begin < 2:
                geometry = x_window_info(wid)
                if geometry != old:
                    changes.append([time.monotonic() - begin, geometry])
                    old = geometry
                time.sleep(0.015)
            report['resize'].append({'size': [width, height], 'changes': changes})
        command('import', '-window', 'root', str(evidence / 'resize.png'))

        # Skin mode can replace the WMP client; preserve window and COM evidence.
        command('xdotool', 'key', '--window', str(xid), 'ctrl+2')
        time.sleep(2)
        report['skin_clients'] = command('wmctrl', '-lpGx')
        sample = remote_sample()
        (evidence / 'skin.json').write_text(json.dumps(sample, indent=2))
        command('import', '-window', 'root', str(evidence / 'skin.png'))
        report['skin_overlay'] = x_window_info(wid)
        command('xdotool', 'key', '--window', str(xid), 'ctrl+1')
        time.sleep(1)
    finally:
        # Close and signal only the client and verified owner observed above.
        if xid:
            subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=3)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=3)
        if owner and is_wmp_owner(owner):
            os.kill(owner, signal.SIGTERM)
        report['launcher_rc'] = process.returncode
        (evidence / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        log.close()


if __name__ == '__main__':
    main()
