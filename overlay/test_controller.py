"""Unit regressions for overlay geometry, ownership, and transport policy.

These tests are side-effect free: they exercise pure controller helpers and do
not launch WMP, Wine, X11 clients, or audio processes.
"""

import os
from pathlib import Path
import unittest

from controller import (
    expected_media_url,
    matching_media,
    mpv_command,
    mpv_updates,
    overlay_visible,
    parse_xwininfo,
    remote_video_rect,
    screen_video_rect,
    top_mpv_command,
    video_rect,
)


class OverlaySyncTests(unittest.TestCase):
    """Protect synchronization, geometry, and safe overlay command invariants."""

    def test_playing_video_starts_at_wmp_position(self):
        """A newly attached picture must seek to WMP before it is unpaused."""
        self.assertEqual(
            mpv_updates(3, 12.0, 0.0, True),
            [['set_property', 'time-pos', 12.0], ['set_property', 'pause', False]],
        )

    def test_small_playback_drift_does_not_cause_seek_loop(self):
        """Normal sub-second drift must not trigger repeated corrective seeks."""
        self.assertEqual(mpv_updates(3, 12.0, 11.75, False), [])

    def test_pause_holds_video_at_audio_position(self):
        """Pausing WMP must pause and align the muted picture overlay."""
        self.assertEqual(
            mpv_updates(2, 12.0, 11.0, False),
            [['set_property', 'pause', True], ['set_property', 'time-pos', 12.0]],
        )

    def test_seek_follows_wmp_timeline(self):
        """A material WMP timeline jump must seek mpv to the same position."""
        self.assertEqual(
            mpv_updates(3, 27.0, 10.0, False),
            [['set_property', 'time-pos', 27.0]],
        )

    def test_stopped_wmp_pauses_video(self):
        """A stopped WMP transport must never leave the picture playing."""
        self.assertEqual(
            mpv_updates(1, 0.0, 5.0, False),
            [['set_property', 'pause', True]],
        )

    def test_wmp_rect_keeps_navigation_and_controls_visible(self):
        """The fallback rectangle must exclude WMP navigation and controls."""
        self.assertEqual(video_rect(1920, 1023), (95, 53, 1624, 869))

    def test_wmp_rect_scales_with_window_and_is_clamped(self):
        """Small fallback rectangles must retain positive bounded dimensions."""
        left, top, width, height = video_rect(800, 600)
        self.assertEqual((left, top), (95, 53))
        self.assertEqual((width, height), (504, 446))
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_xwininfo_rect_is_read_without_outer_window_frame(self):
        """Xwininfo parsing must use client dimensions rather than decorations."""
        text = (
            'Absolute upper-left X:  0\nAbsolute upper-left Y:  27\n'
            'Width: 1920\nHeight: 1023\nMap State: IsViewable\n'
        )
        self.assertEqual(parse_xwininfo(text), (1920, 1023, True))

    def test_xwininfo_minimized_overlay_is_hidden(self):
        """An unmapped X11 client must be reported as hidden."""
        text = 'Width: 900\nHeight: 700\nMap State: IsUnMapped\n'
        self.assertEqual(parse_xwininfo(text), (900, 700, False))

    def test_actual_remote_video_window_bounds_ignore_wmp_toolbar(self):
        """Remote renderer bounds must replace the coarse toolbar-based fallback."""
        sample = {'isRemote': True, 'windows': [
            {'class': 'WMPlayerApp', 'visible': True, 'rect': [0, 0, 1920, 1050]},
            {'class': 'WMP Visualization Window124', 'visible': True,
             'rect': [97, 127, 1622, 820]},
        ]}
        self.assertEqual(
            remote_video_rect(sample, 0, 27, 1920, 1023),
            (97, 100, 1622, 820),
        )

    def test_no_foreign_com_engine_or_missing_video_window(self):
        """Foreign COM state and hidden renderers must never position an overlay."""
        sample = {'isRemote': False, 'windows': [
            {'class': 'WMP Visualization Window', 'visible': True,
             'rect': [1, 1, 200, 100]},
        ]}
        self.assertIsNone(remote_video_rect(sample, 0, 0, 1920, 1023))
        sample['isRemote'] = True
        sample['windows'][0]['visible'] = False
        self.assertIsNone(remote_video_rect(sample, 0, 0, 1920, 1023))

    def test_wine_media_url_must_match_exact_cached_audio(self):
        """Wine URL conversion must preserve spaces, brackets, and the home path."""
        audio = Path.home() / '.cache/wmp9-compat/a [123].avi'
        expected = 'Z:' + str(audio).replace('/', '\\')
        self.assertEqual(expected_media_url(audio), expected)

    def test_embedded_mpv_is_muted_and_ignores_keyboard(self):
        """Embedded mpv must not duplicate audio or intercept WMP controls."""
        runtime_dir = Path(os.environ.get(
            'XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'
        ))
        socket = runtime_dir / 'test.sock'
        command = mpv_command(Path('/videos/source.mp4'), 12345, socket)
        for option in (
            '--vo=x11', '--no-audio', '--pause=yes', '--input-vo-keyboard=no',
            '--no-osc', '--wid=12345', f'--input-ipc-server={socket}',
        ):
            self.assertIn(option, command)
        self.assertEqual(command[-2:], ['--', '/videos/source.mp4'])

    def test_borderless_video_command_keeps_wmp_audio_and_controls(self):
        """Top overlay options must preserve WMP audio, input, and focus ownership."""
        runtime_dir = Path(os.environ.get(
            'XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'
        ))
        command = top_mpv_command(
            Path('/videos/source.mp4'), runtime_dir / 'test.sock',
            (97, 127, 1622, 820),
        )
        for option in (
            '--no-audio', '--no-border', '--ontop', '--no-focus-on-open',
            '--input-cursor-passthrough=yes', '--input-vo-keyboard=no',
            '--geometry=1622x820+97+127',
        ):
            self.assertIn(option, command)
        self.assertFalse(any(option.startswith('--wid=') for option in command))

    def test_top_overlay_tracks_wmp_actual_visualization_bounds(self):
        """Screen geometry must use the visible remote visualization rectangle."""
        sample = {'isRemote': True, 'windows': [
            {'class': 'WMP Visualization Window123', 'visible': True,
             'rect': [197, 227, 600, 400]},
        ]}
        self.assertEqual(
            screen_video_rect(sample, 100, 200, 800, 600),
            (197, 227, 600, 400),
        )

    def test_top_overlay_hides_when_wmp_loses_focus_or_minimizes(self):
        """The overlay must be visible only for a mapped, active WMP client."""
        self.assertTrue(overlay_visible(True, 42, 42))
        self.assertFalse(overlay_visible(False, 42, 42))
        self.assertFalse(overlay_visible(True, 43, 42))

    def test_overlay_requires_same_source_in_actual_remote_player(self):
        """Overlay attachment must require both remote ownership and exact media."""
        path = Path.home() / '.cache/wmp9-compat/a [123].avi'
        self.assertTrue(matching_media({
            'isRemote': True, 'sourceURL': expected_media_url(path)
        }, path))
        self.assertFalse(matching_media({
            'isRemote': False, 'sourceURL': expected_media_url(path)
        }, path))
        self.assertFalse(matching_media({
            'isRemote': True, 'sourceURL': r'Z:\wrong.avi'
        }, path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
