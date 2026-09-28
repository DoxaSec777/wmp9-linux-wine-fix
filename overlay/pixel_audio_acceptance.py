#!/usr/bin/env python3
"""Accept real video pixels and physical audio timing for staging.

This live test records the default output monitor while scanning desktop pixels
for a generated magenta fixture.  It proves that sound does not precede the
first visible frame, that resize/skin/transport operations preserve rendering,
and that no WMP audio or overlay remains afterward.  The test refuses to run
when WMP already owns a GUI or audio stream and signals only a verified owner
from the configured test prefix during emergency cleanup.
"""

import array
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from PIL import ImageGrab

from close_audio_probe import is_wmp_owner, wmp_audio_sink
from gui_acceptance import (
    BASE,
    PROBE,
    STAGING as DEFAULT_STAGING,
    command,
    eventually,
    mpv_state,
    remote_sample,
    window_id,
)
from service import x_window_info


FIXTURE = BASE / 'magenta-tone.mp4'
STAGING = Path(os.environ.get('WMP9_TEST_LAUNCHER', str(DEFAULT_STAGING))).expanduser()


def magenta(image):
    """Return the fraction of sampled pixels matching the fixture's magenta."""
    pixels = list(image.resize((160, 100)).convert('RGB').getdata())
    return sum(r > 180 and g < 65 and b > 180 for r, g, b in pixels) / len(pixels)


def main():
    """Run the pixel/audio acceptance sequence and persist raw evidence."""
    if window_id('Windows Media Player') or wmp_audio_sink():
        raise RuntimeError('WMP already in use')

    root = BASE / ('acceptance-' + str(time.time_ns()))
    root.mkdir()
    sink = command('pactl', 'get-default-sink').strip()
    audio = subprocess.Popen([
        'parec', '--device=' + sink + '.monitor', '--format=s16le', '--rate=8000',
        '--channels=1', '--latency-msec=10', '--process-time-msec=10',
    ], stdout=subprocess.PIPE, stderr=(root / 'audio.stderr').open('w'))
    events = {}
    stop = threading.Event()
    start = time.monotonic()
    rms = []

    def read_audio():
        """Continuously record monitor PCM and timestamp the first audible block."""
        with (root / 'physical-output.s16le').open('wb') as output:
            while not stop.is_set():
                data = audio.stdout.read(160)
                if len(data) != 160:
                    break
                output.write(data)
                values = array.array('h', data)
                level = math.sqrt(sum(value * value for value in values) / len(values))
                timestamp = time.monotonic() - start
                rms.append([timestamp, level])
                if level > 50 and 'sound' not in events:
                    events['sound'] = timestamp

    thread = threading.Thread(target=read_audio, daemon=True)
    thread.start()
    env = dict(
        os.environ,
        WMP9_OVERLAY_DIR=str(BASE),
        WMP_OVERLAY_PROBE=str(PROBE),
        WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
        WINEDEBUG='-all',
    )
    env['WMP9_OVERLAY_DIR'] = os.environ.get('WMP9_TEST_OVERLAY_DIR', str(BASE))
    env['WMP_OVERLAY_PROBE'] = os.environ.get('WMP9_TEST_PROBE', str(PROBE))
    env['WMP9_TRACE'] = str(root / 'missing-geometry.json')
    log = (root / 'launcher.log').open('w')
    process = None
    xid = owner = None
    report = {'events': events}
    failure = None
    try:
        # A quiet physical monitor is a prerequisite for meaningful timing data.
        time.sleep(0.3)
        if 'sound' in events:
            raise RuntimeError('Physical audio busy before test')
        process = subprocess.Popen(
            [sys.executable, str(STAGING), str(FIXTURE)],
            env=env,
            stdout=log,
            stderr=log,
        )
        while time.monotonic() - start < 25:
            if xid is None:
                xid = window_id('Windows Media Player')
                if xid:
                    owner = int(command('xdotool', 'getwindowpid', str(xid)).strip())
                    command('wmctrl', '-ir', hex(xid), '-b',
                            'remove,maximized_vert,maximized_horz')
                    command('wmctrl', '-ir', hex(xid), '-e', '0,100,70,1100,780')
                    command('wmctrl', '-ia', hex(xid))
            image = ImageGrab.grab(xdisplay=':0')
            if magenta(image) > 0.025 and 'picture' not in events:
                events['picture'] = time.monotonic() - start
                image.save(root / 'first-frame.png')
            if 'picture' in events and 'sound' in events:
                break
            time.sleep(0.025)
        assert 'picture' in events and 'sound' in events, events
        report['sound_minus_picture'] = events['sound'] - events['picture']
        assert report['sound_minus_picture'] >= 0, report

        time.sleep(0.5)
        wid = window_id('HAL-WMP-Overlay')
        sample = remote_sample()
        report['normal_state'] = sample['playState']
        report['normal_time'] = sample['currentPosition']
        ImageGrab.grab(xdisplay=':0').save(root / 'normal.png')
        assert sample['playState'] == 3, sample
        command('wmctrl', '-ir', hex(xid), '-b', 'remove,maximized_vert,maximized_horz')
        time.sleep(0.5)

        # Each resize must move the real overlay quickly and retain visible pixels.
        report['resize'] = []
        for width, height in [(1000, 740), (1400, 920), (1100, 780)]:
            old = x_window_info(wid)
            begin = time.monotonic()
            command('wmctrl', '-ir', hex(xid), '-e', f'0,100,70,{width},{height}')
            while time.monotonic() - begin < 2:
                actual = x_window_info(wid)
                if actual and actual[:4] != old[:4]:
                    break
                time.sleep(0.01)
            assert actual and actual[:4] != old[:4], actual
            latency = time.monotonic() - begin
            report['resize'].append({'latency': latency, 'rect': actual})
            assert latency < 0.35, report['resize'][-1]
            time.sleep(0.3)
            image = ImageGrab.grab(xdisplay=':0')
            image.save(root / f'resize-{width}.png')
            x_pos, y_pos, rect_width, rect_height = actual[:4]
            assert magenta(image.crop((
                x_pos, y_pos, x_pos + rect_width, y_pos + rect_height
            ))) > 0.4, 'resized overlay has no rendered video'

        # Skin switches replace WMP clients; validate pixels after rediscovery.
        command('xdotool', 'key', '--window', str(xid), 'ctrl+2')
        time.sleep(0.6)
        xid = window_id('Windows Media Player')
        sample = remote_sample()
        (root / 'skin-sample.json').write_text(json.dumps(sample, indent=2))
        rect = next(window['rect'] for window in sample['windows']
                    if window['visible']
                    and window['class'].startswith('WMP Visualization Window'))
        image = ImageGrab.grab(xdisplay=':0')
        image.save(root / 'skin.png')
        x_pos, y_pos, rect_width, rect_height = rect
        report['skin_magenta'] = magenta(image.crop((
            x_pos, y_pos, x_pos + rect_width, y_pos + rect_height
        )))
        assert report['skin_magenta'] > 0.6, report
        # Skin HWND is destroyed during keydown; sending keyup may return BadWindow.
        subprocess.run(['xdotool', 'key', '--window', str(xid), 'ctrl+1'],
                       capture_output=True, timeout=3)
        time.sleep(0.6)
        xid = window_id('Windows Media Player')
        assert xid is not None

        # WMP remains the sole transport controller for pause and resume.
        command('xdotool', 'key', '--window', str(xid), 'ctrl+p')
        eventually('pause', lambda: remote_sample()['playState'] == 2, 5)
        eventually('video pause', lambda: mpv_state()[0] is True, 3)
        report['pause'] = True
        command('xdotool', 'key', '--window', str(xid), 'ctrl+p')
        eventually('resume', lambda: mpv_state()[0] is False, 3)
        report['resume'] = True
        command('xdotool', 'windowminimize', str(xid))
        eventually('hide', lambda: not x_window_info(wid)[4], 3)
        command('wmctrl', '-ia', hex(xid))
        eventually('restore', lambda: x_window_info(wid)[4], 3)
        report['minimize_restore'] = True
    except Exception as exc:
        failure = exc
        report['failure'] = repr(exc)
        report['clients_at_failure'] = command('wmctrl', '-lpGx')
        report['sample_at_failure'] = remote_sample()
    finally:
        # Close the observed client before considering process-level cleanup.
        current = window_id('Windows Media Player')
        if current and owner:
            subprocess.run(['wmctrl', '-ic', hex(current)], timeout=3)
        if process:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
        if owner and is_wmp_owner(owner):
            os.kill(owner, signal.SIGTERM)
        stop.set()
        audio.terminate()
        audio.wait(timeout=3)
        thread.join(timeout=2)
        time.sleep(0.3)
        report['remaining_audio'] = wmp_audio_sink()
        report['remaining_overlay'] = window_id('HAL-WMP-Overlay')
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        (root / 'rms.json').write_text(json.dumps(rms))
        log.close()
        print(str(root))
        print(json.dumps(report, indent=2))
    if failure:
        raise failure
    assert not report['remaining_audio'] and not report['remaining_overlay']


if __name__ == '__main__':
    main()
