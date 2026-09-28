"""Regression tests for live-overlay lifecycle helpers.

Despite the historical module name, these tests are isolated unit regressions.
They use mocks and short Python subprocesses rather than launching WMP, Wine,
X11 clients, or audio devices.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import top_service


class LiveRegressions(unittest.TestCase):
    """Protect shutdown, focus, sampling, startup, and skin-gap behavior."""

    def test_probe_shutdown_allows_com_release_before_process_exit(self):
        """Graceful shutdown must give the probe time to release COM cleanly."""
        shutdown = getattr(top_service, 'shutdown_probe', None)
        self.assertTrue(callable(shutdown), 'graceful COM teardown is missing')
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / 'stop'
            program = (
                'import pathlib,time,sys\n'
                'p=pathlib.Path(sys.argv[1])\n'
                'while not p.exists(): time.sleep(.01)\n'
                'print("COM_RELEASED",flush=True)'
            )
            process = subprocess.Popen(
                [sys.executable, '-c', program, str(stop)],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                shutdown(process, stop)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(process.stdout.read().strip(), 'COM_RELEASED')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()

    def test_skin_replacement_restores_empty_focus_without_stealing_other_app(self):
        """Client replacement may repair empty focus but never steal foreign focus."""
        restore = getattr(top_service, 'restore_replacement_focus', None)
        self.assertTrue(callable(restore), 'skin replacement leaves active window empty')
        with (patch.object(top_service, 'active_window', return_value=0),
              patch.object(top_service, 'window_cmd') as command):
            restore(42, 88)
            command.assert_called_once_with('windowactivate', '88')
        with (patch.object(top_service, 'active_window', return_value=99),
              patch.object(top_service, 'window_cmd') as command):
            restore(42, 88)
            command.assert_not_called()
        with (patch.object(top_service, 'active_window', return_value=0),
              patch.object(top_service, 'window_cmd') as command):
            restore(42, 42)
            command.assert_not_called()

    def test_closed_gui_does_not_keep_audio_alive_for_long_rediscovery(self):
        """A true GUI close must be recognized before an audible tail develops."""
        with (patch.object(top_service, 'x_window_info', return_value=None),
              patch.object(top_service, 'discover_wmp_window',
                           side_effect=RuntimeError('gone')),
              patch.object(top_service, 'current_client_ids', return_value=set())):
            start = time.monotonic()
            self.assertEqual(top_service.resolve_wmp_window(42), (42, None))
            self.assertLess(
                time.monotonic() - start,
                0.35,
                'close detection must not hold audio for 600ms',
            )

    def test_latest_sample_not_oldest_when_render_loop_lags(self):
        """The consumer must skip stale queued geometry when rendering is slow."""
        program = (
            'import json,time\n'
            'for n in range(40):\n'
            ' print(json.dumps(dict(type="sample", tick=n)),flush=True);time.sleep(.01)\n'
            'time.sleep(1)'
        )
        process = subprocess.Popen(
            [sys.executable, '-u', '-c', program],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            samples = top_service.samples_with_heartbeat(process)
            first = next(samples)
            time.sleep(0.3)
            latest = next(samples)
            self.assertGreaterEqual(
                latest['tick'],
                20,
                'slow GUI processing must not replay queued obsolete geometries',
            )
        finally:
            process.terminate()
            process.wait()
            process.stdout.close()

    def test_startup_requires_rendered_aligned_held_frame(self):
        """Audio release requires a held, visible, aligned frame at position zero."""
        ready = getattr(top_service, 'startup_ready', lambda *args: False)
        sample = {'startupHeld': True, 'playState': 2, 'currentPosition': 0.0}
        target = (100, 100, 800, 600)
        self.assertTrue(ready(sample, 0.0, True, True, (*target, True), target))
        self.assertFalse(ready(sample, 0.0, False, True, (*target, True), target))
        self.assertFalse(ready(sample, 0.0, True, True, (104, 130, 800, 600, True), target))
        self.assertFalse(ready(sample, 1.0, True, True, (*target, True), target))

    def test_transport_updates_even_when_skin_has_no_visible_renderer(self):
        """Pause synchronization must continue through temporary skin geometry gaps."""
        update = getattr(top_service, 'sync_transport', lambda *args: None)
        with patch.object(top_service, 'mpv_ipc', side_effect=[6.7, False, None]) as ipc:
            update('socket', {'playState': 2, 'currentPosition': 6.7, 'windows': []})
            self.assertIn(
                ['set_property', 'pause', True],
                [call.args[1] for call in ipc.call_args_list],
            )

    def test_skin_gap_gets_bounded_rediscovery_before_close(self):
        """A transient missing skin client must receive one bounded rediscovery."""
        with (patch.object(
                top_service, 'x_window_info',
                side_effect=lambda xid: None if xid == 42 else (20, 30, 400, 300, True)),
              patch.object(top_service, 'discover_wmp_window',
                           side_effect=[RuntimeError('transition'), 88]),
              patch.object(top_service, 'current_client_ids', return_value=set())):
            self.assertEqual(
                top_service.resolve_wmp_window(42),
                (88, (20, 30, 400, 300, True)),
            )

    def test_skin_replacement_is_not_player_close(self):
        """A replacement WMP skin client must keep the overlay session attached."""
        with (patch.object(
                top_service, 'x_window_info',
                side_effect=lambda xid: None if xid == 42 else (20, 30, 400, 300, True)),
              patch.object(top_service, 'discover_wmp_window', return_value=88)):
            resolver = getattr(
                top_service, 'resolve_wmp_window', lambda xid: (xid, None)
            )
            self.assertEqual(
                resolver(42),
                (88, (20, 30, 400, 300, True)),
            )


if __name__ == '__main__':
    unittest.main()
