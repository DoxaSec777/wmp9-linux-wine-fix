#!/usr/bin/env python3
"""Verify that closing the staged WMP GUI also stops owned audio.

This live regression probe launches one known fixture through the staging
launcher, closes only the WMP window created for that fixture, and checks that
its Wine process and PulseAudio/PipeWire sink input disappear.  Cleanup is
restricted to processes proven to belong to the configured test prefix; the
probe refuses to start when a WMP GUI is already open.
"""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from gui_acceptance import BASE, PROBE, SOURCE, STAGING, command, eventually, remote_sample, window_id


WINEPREFIX = Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))


def proxy_marker(source: Path = SOURCE) -> str:
    """Return the stable, human-readable part of the launcher's proxy basename."""
    stem = re.sub(r'[^A-Za-z0-9._ -]+', '_', source.stem).strip(' ._')
    return f'{(stem or "Title")[:80]} overlay-audio'


def matching_wmp_processes():
    """Return WMP processes whose command line names this probe's fixture."""
    lines = command('ps', '-eo', 'pid=,args=').splitlines()
    return [(int(row.split(maxsplit=1)[0]), row.split(maxsplit=1)[1]) for row in lines
            if 'wmplayer.exe' in row and proxy_marker() in row]


def wmp_audio_sink():
    """Report whether WMP currently owns an audio sink input."""
    return 'Microsoft(R) Windows Media Player' in command(
        'pactl', 'list', 'sink-inputs', timeout=5
    )


def is_wmp_owner(pid):
    """Confirm that *pid* maps WMP from the configured disposable test prefix."""
    proc = Path('/proc') / str(pid)
    expected_prefix = f'WINEPREFIX={WINEPREFIX}'.encode()
    try:
        return (expected_prefix in proc.joinpath('environ').read_bytes().split(b'\0')
                and 'wmplayer.exe' in proc.joinpath('maps').read_text(errors='replace').lower())
    except (OSError, ValueError):
        # A disappearing or unreadable process is not safe to signal.
        return False


def main():
    """Run the close-to-silence regression and clean up only verified owners."""
    if window_id('Windows Media Player'):
        raise RuntimeError('WMP GUI already open')

    env = os.environ.copy()
    env.update(WMP9_OVERLAY_DIR=str(BASE), WMP_OVERLAY_PROBE=str(PROBE),
               WINEPREFIX=str(WINEPREFIX), WINEDEBUG='-all')
    with (BASE / 'close-audio-launch.log').open('w') as log:
        launcher = subprocess.Popen([sys.executable, str(STAGING), str(SOURCE)],
                                    env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
        xid = owner_pid = None
        try:
            xid = eventually('WMP GUI', lambda: window_id('Windows Media Player'), 16)
            command('wmctrl', '-ia', hex(xid))
            owner_pid = int(command('xdotool', 'getwindowpid', str(xid)).strip())
            print('X_OWNER', json.dumps({'pid': owner_pid, 'wmp': is_wmp_owner(owner_pid),
                                         'clients': command('wmctrl', '-lxp')}), flush=True)
            sinks = command('pactl', 'list', 'sink-inputs', timeout=5)
            wmp_sinks = [block for block in sinks.split('Sink Input #')[1:]
                         if 'Microsoft(R) Windows Media Player' in block]
            print('DEBUG_START', json.dumps({'launcher': launcher.pid,
                 'xpid': subprocess.run(['xdotool', 'getwindowpid', str(xid)],
                                       text=True, capture_output=True, timeout=3).stdout.strip(),
                 'wine': [row for row in command('ps', '-eo', 'pid=,ppid=,comm=,args=').splitlines()
                          if 'wmplayer' in row.lower() or 'wmp_remote_probe' in row.lower()],
                 'sinks': [';'.join(x.strip() for x in block.splitlines()
                                    if 'application.name' in x or 'application.process.' in x)
                           for block in wmp_sinks]}), flush=True)
            eventually('WMP GUI owner', lambda: is_wmp_owner(owner_pid), 8)
            eventually('WMP audio stream', wmp_audio_sink, 5)
            before = matching_wmp_processes()
            parent = {pid: command('ps', '-o', 'ppid=', '-p', str(pid)).strip()
                      for pid, _ in before}
            print('BEFORE', json.dumps({'launcher_pid': launcher.pid,
                                        'wmplayer': before, 'parent': parent,
                                        'sink': wmp_audio_sink()}), flush=True)

            # Close the specific GUI client rather than terminating Wine globally.
            command('wmctrl', '-ic', hex(xid))
            eventually('GUI closed', lambda: window_id('Windows Media Player') is None, 5)
            time.sleep(2)
            after = matching_wmp_processes()
            sink = wmp_audio_sink()
            owner_alive = is_wmp_owner(owner_pid)
            print('AFTER', json.dumps({'wmplayer': after, 'owner_alive': owner_alive,
                                       'sink': sink, 'launcher_rc': launcher.poll(),
                                       'clients': [line for line in command('wmctrl', '-lxp').splitlines()
                                                   if 'wmplayer.exe' in line.lower()],
                                       'xid_exists': subprocess.run(['xwininfo', '-id', hex(xid)],
                                                                     text=True, capture_output=True,
                                                                     timeout=3).returncode == 0}), flush=True)
            assert not sink and not after and not owner_alive, 'WMP audio/owner persists after GUI close'
        finally:
            # Emergency cleanup remains scoped to the observed window and fixture.
            if xid and window_id('Windows Media Player') == xid:
                subprocess.run(['wmctrl', '-ic', hex(xid)], timeout=4)
            for pid, args in matching_wmp_processes():
                if proxy_marker() in args:
                    os.kill(pid, signal.SIGTERM)
            if owner_pid and window_id('Windows Media Player') is None and is_wmp_owner(owner_pid):
                os.kill(owner_pid, signal.SIGTERM)
            try:
                launcher.wait(timeout=8)
            except subprocess.TimeoutExpired:
                launcher.terminate()
                launcher.wait(timeout=3)
            print('CLEANUP', json.dumps({'launcher_rc': launcher.returncode,
                                         'remaining': matching_wmp_processes(),
                                         'sink': wmp_audio_sink()}), flush=True)


if __name__ == '__main__':
    main()
