#!/usr/bin/env python3
"""Accept physical audio, rendered pixels, handoff, close, and reopen.

The staging-only test creates two color/tone fixtures, records the physical
output monitor, and proves that each visible frame precedes its corresponding
sound.  It also validates live media handoff, skin rendering, close-to-silence,
and a clean reopen.  No cleanup is counted as success: close silence is asserted
before emergency cleanup, which is limited to PIDs observed from this run and
verified as WMP owners in the configured test prefix.
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
from gui_acceptance import BASE, PROBE, STAGING, command, eventually, mpv_state, remote_sample, window_id


def fraction(image, color):
    """Return the sampled fraction matching the named fixture color."""
    data = image.resize((240, 160)).convert('RGB').getdata()
    if color == 'magenta':
        return sum(r > 180 and g < 65 and b > 180 for r, g, b in data) / 38400
    return sum(r < 65 and g > 180 and b > 180 for r, g, b in data) / 38400


def main():
    """Run the complete repair acceptance sequence and save its evidence."""
    if (window_id('Windows Media Player') or wmp_audio_sink()
            or window_id('HAL-WMP-Overlay')):
        raise RuntimeError('WMP/overlay already in use; refusing test')

    root = BASE / ('repair-acceptance-' + str(time.time_ns()))
    root.mkdir()
    logs = [Path.home() / '.cache/wmp9-compat/wmp9-compat.log', BASE / 'top-probe.stderr']
    offsets = {path: path.stat().st_size if path.exists() else 0 for path in logs}
    env = dict(
        os.environ,
        WMP9_OVERLAY_DIR=str(BASE),
        WMP_OVERLAY_PROBE=str(PROBE),
        WINEPREFIX=str(Path(os.environ.get('WMP9_PREFIX', Path.home() / '.wine-wmp9-real'))),
        WINEDEBUG='-all',
    )
    launcher = Path(os.environ.get('WMP9_TEST_LAUNCHER', str(STAGING))).expanduser()

    # Distinct colors and frequencies reveal stale audio or video during handoff.
    fixtures = {}
    for color, frequency in [('magenta', 440), ('cyan', 1000)]:
        path = BASE / ('repair-' + color + '.mp4')
        if not path.exists():
            command(
                'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                f'color=c={color}:s=640x480:r=25', '-f', 'lavfi', '-i',
                f'sine=frequency={frequency}:sample_rate=44100', '-vf',
                'drawtext=text=%{pts}:fontsize=40:fontcolor=white:x=20:y=20',
                '-t', '60', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-c:a', 'aac', str(path), timeout=30,
            )
        fixtures[color] = path

    sink = command('pactl', 'get-default-sink').strip()
    audio = subprocess.Popen([
        'parec', '--device=' + sink + '.monitor', '--format=s16le', '--rate=8000',
        '--channels=1', '--latency-msec=10', '--process-time-msec=10',
    ], stdout=subprocess.PIPE, stderr=(root / 'audio.stderr').open('w'))
    samples = []
    stop = threading.Event()
    start = time.monotonic()
    waves = {
        frequency: [
            (math.cos(2 * math.pi * frequency * sample / 8000),
             math.sin(2 * math.pi * frequency * sample / 8000))
            for sample in range(160)
        ]
        for frequency in (440, 1000)
    }

    def record():
        """Record raw output and per-block RMS/tone correlation evidence."""
        with (root / 'physical-output.s16le').open('wb') as raw:
            while not stop.is_set():
                chunk = audio.stdout.read(320)
                if len(chunk) != 320:
                    break
                raw.write(chunk)
                values = array.array('h', chunk)
                rms = math.sqrt(sum(value * value for value in values) / 160)
                powers = {
                    frequency: math.hypot(
                        sum(value * cosine for value, (cosine, sine) in zip(values, wave)),
                        sum(value * sine for value, (cosine, sine) in zip(values, wave)),
                    ) / 80
                    for frequency, wave in waves.items()
                }
                samples.append([
                    time.monotonic() - start, rms, powers[440], powers[1000]
                ])

    thread = threading.Thread(target=record, daemon=True)
    thread.start()
    processes = []
    owners = set()
    report = {'root': str(root), 'runs': []}
    failure = None

    def spawn(color, label):
        """Launch one generated fixture and retain its process and log handle."""
        handle = (root / (label + '.log')).open('w')
        process = subprocess.Popen(
            [sys.executable, str(launcher), str(fixtures[color])],
            env=env,
            stdout=handle,
            stderr=handle,
        )
        processes.append((process, handle))
        return process

    def watch(color, label, begin):
        """Wait for this run's matching visible color and physical tone."""
        picture = None
        sound = None
        seen = set()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                xid = window_id('Windows Media Player')
            except subprocess.CalledProcessError:
                # X clients can disappear halfway through wmctrl enumeration.
                time.sleep(0.03)
                continue
            if xid and xid not in seen:
                seen.add(xid)
                owners.add(int(command('xdotool', 'getwindowpid', str(xid)).strip()))
                command('wmctrl', '-ia', hex(xid))
            image = ImageGrab.grab(xdisplay=':0')
            if picture is None and fraction(image, color) > 0.004:
                picture = time.monotonic() - start
                image.save(root / (label + '-first.png'))
            for timestamp, rms, amplitude_440, amplitude_1000 in samples:
                if timestamp < begin:
                    continue
                hit = (rms > 50 if color == 'magenta'
                       else amplitude_1000 > 100 and amplitude_1000 > 3 * amplitude_440)
                if hit:
                    sound = timestamp
                    break
            if sound is not None and picture is not None:
                break
            time.sleep(0.02)
        result = {'label': label, 'picture': picture, 'sound': sound, 'begin': begin}
        report['runs'].append(result)
        assert picture is not None and sound is not None, result
        result['sound_minus_picture'] = sound - picture
        assert sound >= picture, result
        return eventually('current WMP', lambda: window_id('Windows Media Player'), 3)

    def close_and_check(label):
        """Close the current GUI and prove physical and logical audio silence."""
        xid = window_id('Windows Media Player')
        assert xid is not None
        closed_at = time.monotonic() - start
        command('wmctrl', '-ic', hex(xid))
        eventually('closed GUI', lambda: not window_id('Windows Media Player'), 5)
        eventually('overlay gone', lambda: not window_id('HAL-WMP-Overlay'), 5)
        time.sleep(1.5)
        levels = [rms for timestamp, rms, *_ in samples if timestamp > closed_at + 1.0]
        audible = [timestamp for timestamp, rms, *_ in samples
                   if timestamp >= closed_at and rms > 50]
        report[label] = {
            'close_at': closed_at,
            'last_sound_after_close': max(audible, default=closed_at) - closed_at,
            'max_rms_after_1s': max(levels, default=-1),
            'remaining_audio': wmp_audio_sink(),
        }
        assert levels and max(levels) < 50, report[label]
        assert not report[label]['remaining_audio'], report[label]

    try:
        # Refuse contaminated measurements before launching any media.
        time.sleep(0.4)
        assert samples and max(sample[1] for sample in samples) < 50, 'physical audio busy'
        begin = time.monotonic() - start
        first_process = spawn('magenta', 'first')
        xid = watch('magenta', 'first', begin)
        eventually('first video playing', lambda: mpv_state()[0] is False, 5)

        # Switch while the first item is playing; the harness must not close it.
        begin = time.monotonic() - start
        second_process = spawn('cyan', 'switch')
        xid = watch('cyan', 'switch', begin)
        eventually('old launcher exited', lambda: first_process.poll() is not None, 5)
        report['old_launcher_rc'] = first_process.returncode
        sample = remote_sample()
        report['switch_source'] = sample['sourceURL']
        assert 'repair-cyan overlay-audio' in sample['sourceURL'], sample
        assert mpv_state()[0] is False

        # Validate rendering in skin mode before measuring close-to-silence.
        subprocess.run(['xdotool', 'key', '--window', str(xid), 'ctrl+2'],
                       capture_output=True, timeout=3)
        time.sleep(0.7)
        sample = remote_sample()
        rect = next(window['rect'] for window in sample['windows']
                    if window['visible']
                    and window['class'].startswith('WMP Visualization Window'))
        x_pos, y_pos, width, height = rect
        image = ImageGrab.grab(xdisplay=':0')
        image.save(root / 'switch-skin.png')
        report['skin_fraction'] = fraction(
            image.crop((x_pos, y_pos, x_pos + width, y_pos + height)), 'cyan'
        )
        assert report['skin_fraction'] > 0.6, report
        close_and_check('skin_close')
        second_process.wait(timeout=5)

        # Persisted skin preference must also reopen with a visible first frame.
        begin = time.monotonic() - start
        third_process = spawn('magenta', 'reopen')
        xid = watch('magenta', 'reopen', begin)
        ImageGrab.grab(xdisplay=':0').save(root / 'reopened.png')
        # Return to full mode for future tests without editing the Wine prefix.
        subprocess.run(['xdotool', 'key', '--window', str(xid), 'ctrl+1'],
                       capture_output=True, timeout=3)
        time.sleep(0.6)
        close_and_check('reopen_close')
        third_process.wait(timeout=5)
        report['passed'] = True
    except BaseException as exc:
        failure = exc
        report['failure'] = repr(exc)
        try:
            report['failure_processes'] = [
                line for line in command('ps', '-eo', 'pid=,ppid=,args=').splitlines()
                if any(name in line for name in (
                    'wmplayer.exe', 'wmp_remote_probe', 'top_service.py'
                ))
            ]
        except Exception as diagnostic:
            report['diagnostic_error'] = repr(diagnostic)
        ImageGrab.grab(xdisplay=':0').save(root / 'failure.png')
    finally:
        # Emergency cleanup is isolated to PIDs observed from this test's GUI.
        current = window_id('Windows Media Player')
        if current:
            pid = int(command('xdotool', 'getwindowpid', str(current)).strip())
            if (is_wmp_owner(pid)
                    and b'repair-' in (Path('/proc') / str(pid) / 'cmdline').read_bytes()):
                owners.add(pid)
            if pid in owners:
                subprocess.run(['wmctrl', '-ic', hex(current)], timeout=3)
        for process, handle in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
            handle.close()
        for pid in owners:
            if is_wmp_owner(pid):
                os.kill(pid, signal.SIGTERM)
        stop.set()
        audio.terminate()
        audio.wait(timeout=3)
        thread.join(timeout=2)
        (root / 'samples.json').write_text(json.dumps(samples))
        for path, offset in offsets.items():
            if path.exists():
                with path.open('rb') as source:
                    source.seek(offset)
                    (root / path.name).write_bytes(source.read())
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    if failure:
        raise failure


if __name__ == '__main__':
    main()
