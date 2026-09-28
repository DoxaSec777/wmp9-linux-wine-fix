"""Synchronize WMP9 with a borderless top-level mpv video overlay.

WMP remains responsible for audio, transport controls, and library state.  mpv
renders only video and follows WMP through read-mostly COM samples from the
native bridge.  The mpv window is intentionally independent of Wine because
some Wine renderers repaint over X11 children; never attach this mode with
``--wid``.

The service also handles two races absent from a normal player.  WMP skin
changes replace the Wine X11 client and can briefly leave no client mapped, and
window-manager work can be slower than the COM sample stream.  Bounded client
rediscovery plus a one-element latest-sample queue prevent false close events
and stale geometry replay.  An optional private PulseAudio sink holds startup
sound until WMP is paused at zero, mpv has rendered a frame, and the overlay is
mapped at the exact renderer rectangle.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import queue
import threading
import subprocess
import sys
import time
import uuid

from controller import expected_media_url, matching_media, mpv_updates, overlay_visible, screen_video_rect, top_mpv_command
from service import BASE, PROBE, discover_wmp_window, info, mpv_ipc, x_window_info

PREFIX = Path(os.environ.get('WMP9_PREFIX',
                             os.environ.get('WINEPREFIX',
                                            str(Path.home() / '.wine-wmp9-real'))))


def managed_client_ids(output: str) -> set[int]:
    """Return managed X11 clients, including minimized windows."""
    result = set()
    for line in output.splitlines():
        fields = line.split(maxsplit=1)
        if fields and fields[0].startswith('0x'):
            result.add(int(fields[0], 16))
    return result


def window_closed(geo: tuple[int, int, int, int, bool] | None,
                  clients: set[int], xid: int) -> bool:
    """Distinguish a destroyed client from a still-managed minimized client."""
    return geo is None or (not geo[4] and xid not in clients)


def current_client_ids() -> set[int]:
    """Query the window manager for all currently managed X11 client IDs."""
    result = subprocess.run(['wmctrl', '-l'], text=True,
                            capture_output=True, check=True, timeout=2)
    return managed_client_ids(result.stdout)


def samples_with_heartbeat(process: subprocess.Popen):
    """Drain COM output continuously while consumers see only the latest sample.

    The reader thread must never wait for geometry or window-manager work.  A
    bounded queue intentionally drops superseded samples so a slow render loop
    cannot replay stale skin HWNDs or rectangles.  ``None`` heartbeats keep GUI
    closure detection active even when the COM host is temporarily silent.
    """
    updates = queue.Queue(maxsize=1)
    def read():
        """Continuously parse samples without blocking window-management work."""
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get('type') != 'sample':
                continue
            try:
                updates.get_nowait()
            except queue.Empty:
                pass
            updates.put(value)
    # A daemon is appropriate here: the bridge process owns the pipe, and run()
    # always stops that process before returning from its cleanup path.
    threading.Thread(target=read, daemon=True).start()
    while process.poll() is None or not updates.empty():
        try:
            yield updates.get(timeout=0.05)
        except queue.Empty:
            yield None


def shutdown_probe(probe, stop_file):
    """Request cooperative COM teardown, then use bounded process fallbacks.

    The marker lets the bridge stop only its owned proxy media and release the
    ActiveX object, client site, and OLE apartment in order.  Abrupt termination
    is reserved for a hung bridge because incomplete COM teardown can prevent a
    later warm-start probe from becoming remote.
    """
    stop_file.touch()
    try:
        probe.wait(timeout=3)
    except subprocess.TimeoutExpired:
        probe.terminate()
        try:
            probe.wait(timeout=2)
        except subprocess.TimeoutExpired:
            probe.kill()
            probe.wait()


def resolve_wmp_window(xid):
    """Follow skin-mode client replacement without delaying a real close.

    Compact/full skin transitions destroy one Wine X11 client and create
    another.  The 200 ms rediscovery window bridges that gap, but a still-managed
    unmapped client is treated as minimized rather than replaced or closed.
    """
    geo = x_window_info(xid) if xid is not None else None
    if geo is not None and geo[4]:
        return xid, geo
    deadline = time.monotonic() + 0.2
    while True:
        try:
            replacement = discover_wmp_window()
            return replacement, x_window_info(replacement)
        except RuntimeError:
            if xid in current_client_ids() or time.monotonic() >= deadline:
                return xid, geo
            time.sleep(0.03)


def restore_replacement_focus(previous, replacement):
    """Repair focus only when a skin switch left Xfwm with no active client."""
    if replacement != previous and replacement is not None and active_window() == 0:
        window_cmd(*focus_command(replacement))


def sync_transport(sock, sample):
    """Follow WMP transport even while a skin temporarily hides its renderer."""
    try:
        position = mpv_ipc(sock, ['get_property', 'time-pos'], request_id=10)
        pause = mpv_ipc(sock, ['get_property', 'pause'], request_id=11)
        state, seconds = sample.get('playState'), sample.get('currentPosition')
        if position is None or pause is None or not isinstance(state, int) or not isinstance(seconds, (int, float)):
            return
        for n, change in enumerate(mpv_updates(state, seconds, position, pause)):
            mpv_ipc(sock, change, request_id=20+n)
            info('SYNC ' + repr(change))
    except (OSError, RuntimeError, ValueError) as exc:
        info(f'IPC_RETRY {exc}')


def startup_ready(sample, position, frame_ready, visible, actual, target):
    """Require a held zero-time pair and an aligned visible frame before audio."""
    return bool(sample.get('startupHeld') and sample.get('playState') == 2
                and abs(sample.get('currentPosition', -1)) < 0.1
                and abs(position) < 0.1 and frame_ready and visible
                and actual and actual[4] and actual[:4] == target)


def needs_alignment(target: tuple[int, int, int, int],
                    current: tuple[int, int, int, int, bool] | None) -> bool:
    """Return whether Xfwm moved a visible borderless overlay off target."""
    return bool(current and current[4] and current[:4] != target)


def focus_command(wmp_xid: int) -> tuple[str, str]:
    """Return the non-blocking xdotool arguments used to refocus WMP."""
    return 'windowactivate', str(wmp_xid)


def focus_restore_target(active: int, mpv_xid: int, wmp_xid: int) -> int | None:
    """Request WMP focus only when the overlay itself became active."""
    return wmp_xid if active == mpv_xid else None


def overlay_action(mapped: bool, active: int, wmp_xid: int,
                   currently_visible: bool, mpv_xid: int | None = None) -> str | None:
    """Choose an idempotent map/unmap action from WMP focus and map state."""
    wanted = overlay_visible(mapped, active, wmp_xid, mpv_xid)
    if wanted == currently_visible:
        return None
    return 'show' if wanted else 'hide'


def parse_mpv_window_ids(output: str) -> int:
    """Return the newest mpv X11 client ID from xdotool search output."""
    values = [int(line) for line in output.splitlines() if line.strip()]
    if not values:
        raise ValueError('mpv window not found')
    return values[-1]


def mpv_window(pid: int) -> int:
    """Wait for mpv's X11 top-level client and return its most recent ID."""
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        result = subprocess.run(['xdotool', 'search', '--pid', str(pid),
                                 '--class', 'mpv'], text=True, capture_output=True, timeout=2)
        if result.returncode == 0:
            return parse_mpv_window_ids(result.stdout)
        time.sleep(0.1)
    raise RuntimeError('mpv top-level window not created')


def active_window() -> int:
    """Return the active X11 window ID, or zero when no client is active."""
    result = subprocess.run(['xdotool', 'getactivewindow'], text=True,
                            capture_output=True, timeout=2)
    return int(result.stdout.strip()) if result.returncode == 0 else 0


def window_cmd(*args: str) -> None:
    """Run one bounded, silent xdotool window-management operation."""
    subprocess.run(['xdotool', *args], check=True, timeout=3,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(source: Path, audio: Path, xid: int | None, duration: float) -> int:
    """Run one serialized overlay session for an exact WMP-compatible media URL."""
    if not source.is_file() or not audio.is_file() or not PROBE.is_file():
        raise FileNotFoundError('source, WMP-compatible copy or remote probe missing')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Only one top overlay may own focus, geometry, and startup-gate release.
    lock = (BASE / 'top-overlay.lock').open('w')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    sock = BASE / ('mpv-top-' + uuid.uuid4().hex[:14] + '.sock')
    wine_log = (BASE / 'top-probe.stderr').open('a')
    mpv_log = (BASE / 'top-mpv.stderr').open('a')
    probe = player = None
    wid = None
    visible = False
    geometry_valid = False
    rect_old = None
    matched = False
    mismatch_since = None
    last_mismatch = None

    last_alignment = 0.0
    started = time.monotonic()
    # The launcher creates the gate before WMP starts, so PULSE_SINK is inherited
    # by the new Wine process.  The frame and stop markers are unique per run.
    gate_file = Path(os.environ['WMP9_GATE_FILE']) if os.environ.get('WMP9_GATE_FILE') else None
    frame_file = Path(str(sock) + '.frame')
    stop_file = Path(str(sock) + '.stop')
    released = gate_file is None
    frame_seen_at = None
    try:
        env = os.environ.copy()
        env.update(WINEPREFIX=str(PREFIX),
                   WINEDEBUG='-all', WINEDLLOVERRIDES='winedbg.exe=d')
        env['WMP9_PROBE_STOP'] = expected_media_url(stop_file)
        # WMP may map its X11 client after the launcher has started this service.
        while xid is None:
            try:
                xid = discover_wmp_window()
            except RuntimeError:
                if time.monotonic() - started > 15:
                    raise RuntimeError('WMP GUI did not open')
                time.sleep(0.05)
        probe_command = ['wine-stable', str(PROBE), '0', '30']
        if gate_file:
            probe_command += ['--startup-gate', expected_media_url(gate_file), expected_media_url(audio)]
        # The stop marker gives the bridge a graceful path through COM cleanup;
        # stdout is drained concurrently by samples_with_heartbeat().
        probe = subprocess.Popen(probe_command,
                                 env=env, stdout=subprocess.PIPE,
                                 stderr=wine_log, text=True, bufsize=1)
        for sample in samples_with_heartbeat(probe):
            if duration and time.monotonic() - started > duration:
                break
            if sample is None:
                # Probe silence is not geometry validity.  Continue checking the
                # X11 client so a user close or skin replacement is noticed now.
                if xid is not None:
                    previous_xid = xid
                    xid, idle_geo = resolve_wmp_window(xid)
                    restore_replacement_focus(previous_xid, xid)
                    ids = current_client_ids() if idle_geo is None or not idle_geo[4] else set()
                    if window_closed(idle_geo, ids, xid):
                        info('WMP window closed during probe silence; detaching')
                        break
                    if player is not None:
                        idle_action = overlay_action(idle_geo[4] and geometry_valid, active_window(), xid, visible, wid)
                        if idle_action:
                            window_cmd('windowmap' if idle_action == 'show' else 'windowunmap', str(wid))
                            visible = idle_action == 'show'
                            info('OVERLAY_' + idle_action.upper() + ' during probe silence')
                if not matched and time.monotonic() - started > 30:
                    raise RuntimeError('WMP did not open expected video copy')
                continue
            if not matching_media(sample, audio):
                value = (sample.get('isRemote'), sample.get('sourceURL'), sample.get('playState'))
                if value != last_mismatch:
                    info(f'WAIT_MEDIA remote={value[0]} url={value[1]!r} state={value[2]}')
                    last_mismatch = value
                if matched:
                    if mismatch_since is None:
                        mismatch_since = time.monotonic()
                    if time.monotonic() - mismatch_since > 1.5:
                        info('WMP changed media; detaching top overlay')
                        break
                elif time.monotonic() - started > 30:
                    raise RuntimeError('WMP did not open expected video copy')
                continue
            matched = True
            mismatch_since = None
            if released and player is not None and sock.exists():
                sync_transport(sock, sample)
            previous_xid = xid
            xid, geo = resolve_wmp_window(xid)
            restore_replacement_focus(previous_xid, xid)
            ids = current_client_ids() if geo is None or not geo[4] else set()
            if window_closed(geo, ids, xid):
                info('WMP window closed; detaching top overlay')
                break
            x, y, width, height, mapped = geo
            if not mapped:
                if player is not None and visible:
                    window_cmd('windowunmap', str(wid))
                    visible = False
                    info('OVERLAY_HIDE WMP minimized')
                continue
            # Win32 renderer bounds arrive in screen space.  Validation first
            # converts them through the current Wine client, then returns the
            # screen-space rectangle needed by the independent mpv window.
            target = screen_video_rect(sample, x, y, width, height)
            geometry_valid = target is not None
            if target is None:
                if os.environ.get('WMP9_TRACE'):
                    Path(os.environ['WMP9_TRACE']).write_text(json.dumps({'sample':sample,'xid':xid,'geo':geo,'clients':list(current_client_ids())},indent=2))
                if player is not None and visible:
                    window_cmd('windowunmap', str(wid))
                    visible = False
                if not player and time.monotonic() - started > 30:
                    raise RuntimeError('WMP visualization rectangle not found')
                continue
            if not player:
                mpv_command = top_mpv_command(source, sock, target)
                player_env = os.environ.copy()
                if gate_file:
                    mpv_command.insert(1, '--script=' + str(Path(__file__).with_name('frame_ready.lua')))
                    player_env['WMP9_FRAME_READY'] = str(frame_file)
                player = subprocess.Popen(mpv_command, env=player_env,
                                          stdout=mpv_log, stderr=subprocess.STDOUT)
                wid = mpv_window(player.pid)
                rect_old = target
                window_cmd(*focus_command(xid))
                info(f'TOP_OVERLAY wmp=0x{xid:x} mpv=0x{wid:x} rect={target} pid={player.pid}')
            if player.poll() is not None:
                raise RuntimeError('top-level mpv terminated unexpectedly')
            if target != rect_old:
                px, py, pw, ph = target
                window_cmd('windowsize', str(wid), str(pw), str(ph),
                           'windowmove', str(wid), str(px), str(py))
                rect_old = target
                info('VIDEO_RECT ' + repr(target))
                window_cmd('windowraise', str(wid))
            active = active_window()
            if sock.exists() and focus_restore_target(active, wid, xid):
                window_cmd(*focus_command(xid))
                active = active_window()
                info(f'FOCUS_WMP active=0x{active:x}')
                window_cmd('windowraise', str(wid))
            action = overlay_action(mapped, active, xid, visible, wid)
            if action:
                window_cmd('windowmap' if action == 'show' else 'windowunmap', str(wid))
                visible = action == 'show'
                if visible:
                    window_cmd('windowraise', str(wid))
                info(f'OVERLAY_{action.upper()} active=0x{active:x} mapped={mapped}')
            if visible and time.monotonic() - last_alignment >= 0.5:
                actual = x_window_info(wid)
                if needs_alignment(target, actual):
                    px, py, pw, ph = target
                    window_cmd('windowsize', str(wid), str(pw), str(ph),
                               'windowmove', str(wid), str(px), str(py))
                    info(f'REALIGN {actual[:4]} -> {target}')
                last_alignment = time.monotonic()
            if not sock.exists():
                continue
            if released:
                continue
            try:
                position = mpv_ipc(sock, ['get_property', 'time-pos'], request_id=10)
                pause = mpv_ipc(sock, ['get_property', 'pause'], request_id=11)
                state = sample.get('playState')
                seconds_wmp = sample.get('currentPosition')
                if position is None or pause is None or not isinstance(state, int) or not isinstance(seconds_wmp, (int, float)):
                    continue
                if not released:
                    # playback-restart means mpv configured video output.  A
                    # short settle interval plus exact X11 alignment prevents
                    # releasing WMP audio for a merely-created or misplaced window.
                    if frame_seen_at is None and frame_file.is_file():
                        frame_seen_at = time.monotonic()
                    frame_ready = frame_seen_at is not None and time.monotonic() - frame_seen_at >= 0.15
                    if startup_ready(sample, position, frame_ready, visible, x_window_info(wid), target):
                        from startup_gate import release_gate
                        release_gate(os.environ['WMP9_GATE_SINK'], os.environ['WMP9_GATE_TARGET'])
                        gate_file.write_text('ready\n')
                        released = True
                        info(f'STARTUP_RELEASE at={time.monotonic():.6f} wmp={seconds_wmp} video={position}')
                    elif time.monotonic() - started > 25:
                        raise RuntimeError('WMP synchronized startup gate timed out')
                    continue

            except (OSError, RuntimeError, ValueError) as exc:
                info(f'IPC_RETRY {exc}')
        return 0 if matched and player is not None else 1
    finally:
        # Tear down the visible overlay first, then let the bridge release COM,
        # and only afterward remove synchronization markers and close logs/lock.
        if player and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=3)
            except subprocess.TimeoutExpired:
                player.kill()
                player.wait()
        if probe and probe.poll() is None:
            shutdown_probe(probe, stop_file)
        sock.unlink(missing_ok=True)
        frame_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        wine_log.close()
        mpv_log.close()
        lock.close()


def main() -> int:
    """Parse the overlay CLI, report failures, and return a process status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--attach-xid', type=lambda text: int(text, 0))
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    try:
        return run(args.source, args.audio, args.attach_xid, args.seconds)
    except Exception as exc:
        info('TOP_ERROR ' + repr(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
