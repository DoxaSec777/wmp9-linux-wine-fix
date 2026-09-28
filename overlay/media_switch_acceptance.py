#!/usr/bin/env python3
"""Verify safe handoff when a second video is opened through staging.

The acceptance run proves that the first launcher exits, exactly one replacement
overlay remains, WMP switches to the second cached audio source, and audio/video
continue in sync.  It refuses to run beside an existing WMP or overlay and
closes only windows and launcher processes created by this run.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from gui_acceptance import (
    BASE,
    PROBE,
    STAGING,
    SOURCE,
    command,
    eventually,
    mpv_state,
    remote_sample,
    window_id,
)
from service import x_window_info


SECOND = Path(os.environ.get(
    'WMP9_TEST_SECOND_SOURCE',
    str(Path.home() / 'Videos/test-video-2.mp4'),
)).expanduser()


def proxy_marker(source: Path) -> str:
    """Return the cache proxy's readable basename fragment for one source."""
    stem = re.sub(r'[^A-Za-z0-9._ -]+', '_', source.stem).strip(' ._')
    return f'{(stem or "Title")[:80]} overlay-audio'


def start(source, env, name):
    """Start one staged launcher and return its process and owned log handle."""
    handle = (BASE / name).open('w')
    process = subprocess.Popen([sys.executable, str(STAGING), str(source)], env=env,
                               stdin=subprocess.DEVNULL, stdout=handle,
                               stderr=subprocess.STDOUT)
    return process, handle


def mpv_overlay_windows():
    """Return all X11 window IDs currently titled as managed overlays."""
    result = subprocess.run(['xdotool', 'search', '--name', 'HAL-WMP-Overlay'],
                            text=True, capture_output=True, timeout=3)
    return [int(line) for line in result.stdout.splitlines() if line.strip()]


def main():
    """Run the two-media handoff acceptance test and report cleanup state."""
    if window_id('Windows Media Player') or mpv_overlay_windows():
        raise RuntimeError('Test requires no existing WMP or overlay window')
    if not PROBE.is_file():
        raise FileNotFoundError(PROBE)

    env = os.environ.copy()
    env.update(WMP9_OVERLAY_DIR=str(BASE), WMP_OVERLAY_PROBE=str(PROBE),
               WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
               WINEDEBUG='-all')
    processes = []
    xid = None
    try:
        processes.append(start(SOURCE, env, 'switch-first.log'))
        xid = eventually('first WMP GUI', lambda: window_id('Windows Media Player'), 16)
        command('wmctrl', '-ia', hex(xid))
        eventually('first WMP media', lambda: proxy_marker(SOURCE) in
                   remote_sample().get('sourceURL', ''), 16)
        eventually('first overlay', lambda: len(mpv_overlay_windows()) == 1, 12)
        command('wmctrl', '-ia', hex(xid))
        eventually('first overlay visible', lambda: x_window_info(mpv_overlay_windows()[0])[4], 5)
        first = remote_sample()
        if first.get('playState') != 3:
            raise AssertionError(f'First WMP did not play: {first.get("playState")}')

        # Starting the second source must transfer ownership, not create two players.
        processes.append(start(SECOND, env, 'switch-second.log'))
        second = eventually(
            'WMP switched to second media',
            lambda: (sample if proxy_marker(SECOND) in
                     sample.get('sourceURL', '') else None)
            if (sample := remote_sample()) else None,
            22,
        )
        eventually('old service exits', lambda: processes[0][0].poll() is not None, 8)
        eventually('exactly one new overlay', lambda: len(mpv_overlay_windows()) == 1, 12)
        command('wmctrl', '-ia', hex(xid))
        wid = mpv_overlay_windows()[0]
        eventually('new overlay visible', lambda: x_window_info(wid)[4], 5)
        playing = eventually(
            'second media playing',
            lambda: (sample if sample.get('playState') == 3
                     and proxy_marker(SECOND) in sample.get('sourceURL', '')
                     else None) if (sample := remote_sample()) else None,
            6,
        )
        eventually('new video synchronized', lambda: abs(
            mpv_state()[1] - remote_sample()['currentPosition']
        ) < 1.5, 5)
        eventually('new mpv playing', lambda: mpv_state()[0] is False, 4)
        audio_stream = 'Microsoft(R) Windows Media Player' in command(
            'pactl', 'list', 'sink-inputs', timeout=5
        )
        if not audio_stream:
            raise AssertionError('No WMP audio stream for second video')
        print('SWITCH', json.dumps({'old_service_rc': processes[0][0].returncode,
                                    'wmp_count': len([line for line in command('wmctrl', '-l').splitlines()
                                                      if line.endswith(' Windows Media Player')]),
                                    'new_source': playing['sourceURL'],
                                    'new_state': playing['playState'],
                                    'wmp_audio_stream': audio_stream,
                                    'new_overlay': wid}), flush=True)
    finally:
        # Close at most the original client and one replacement discovered after it.
        if xid:
            subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=4)
        for _ in range(2):
            other = window_id('Windows Media Player')
            if other:
                subprocess.run(['wmctrl', '-ic', hex(other)], timeout=4)
                time.sleep(0.4)
        for process, handle in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
            handle.close()
        print('CLEANUP', json.dumps({'launchers': [process.returncode for process, _ in processes],
                                     'wmp': window_id('Windows Media Player'),
                                     'overlay': mpv_overlay_windows()}), flush=True)


if __name__ == '__main__':
    main()
