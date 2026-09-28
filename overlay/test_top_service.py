"""Unit regressions for top-level mpv window and WMP lifecycle policy.

The tests exercise pure helpers plus one short heartbeat subprocess.  They do
not launch Wine, WMP, mpv, or any real X11 client.
"""

import subprocess
import sys
import unittest

from top_service import (
    focus_command,
    focus_restore_target,
    managed_client_ids,
    needs_alignment,
    overlay_action,
    parse_mpv_window_ids,
    samples_with_heartbeat,
    window_closed,
)


class MPlayerWindowTests(unittest.TestCase):
    """Protect overlay discovery, focus, stacking, alignment, and close checks."""

    def test_uses_last_wid_for_mpv_process(self):
        """When Xdotool returns several IDs, the newest mpv client must win."""
        self.assertEqual(parse_mpv_window_ids('7560232\n7560233\n'), 7560233)

    def test_rejects_empty_or_invalid_window_search(self):
        """Missing or malformed X11 search output must fail closed."""
        with self.assertRaises(ValueError):
            parse_mpv_window_ids('')
        with self.assertRaises(ValueError):
            parse_mpv_window_ids('not-a-window\n')

    def test_stacking_only_when_player_active(self):
        """The overlay must show only over an active, mapped WMP window."""
        self.assertEqual(overlay_action(True, 42, 42, False), 'show')
        self.assertIsNone(overlay_action(True, 42, 42, True))
        self.assertEqual(overlay_action(True, 51, 42, True), 'hide')
        self.assertEqual(overlay_action(False, 42, 42, True), 'hide')
        self.assertIsNone(overlay_action(False, 42, 42, False))

    def test_own_overlay_focus_does_not_unmap_video(self):
        """Transient mpv focus must not be mistaken for focus on a foreign app."""
        self.assertIsNone(overlay_action(True, 88, 42, True, mpv_xid=88))
        self.assertEqual(overlay_action(True, 99, 42, True, mpv_xid=88), 'hide')
        self.assertEqual(overlay_action(False, 88, 42, True, mpv_xid=88), 'hide')

    def test_restore_wmp_focus_only_if_mpv_stole_it(self):
        """Focus repair must target WMP only when mpv actually became active."""
        self.assertEqual(focus_restore_target(88, 88, 42), 42)
        self.assertIsNone(focus_restore_target(42, 88, 42))
        self.assertIsNone(focus_restore_target(99, 88, 42))

    def test_focus_restore_does_not_wait_for_xfwm_focus_change(self):
        """Focus restoration must remain asynchronous to avoid blocking sync."""
        self.assertEqual(focus_command(42), ('windowactivate', '42'))
        self.assertNotIn('--sync', focus_command(42))

    def test_realigns_mpv_after_window_manager_shifts_borderless_window(self):
        """Mapped geometry drift must trigger repair, while hidden drift must not."""
        target = (97, 127, 1622, 820)
        self.assertTrue(needs_alignment(target, (101, 157, 1622, 820, True)))
        self.assertFalse(needs_alignment(target, (97, 127, 1622, 820, True)))
        self.assertFalse(needs_alignment(target, (101, 157, 1622, 820, False)))

    def test_unmapped_closed_wmp_detaches_but_minimized_wmp_stays_attached(self):
        """Client-list membership must distinguish minimize from real closure."""
        listing = (
            '0x04400001  0 host Windows Media Player\n'
            '0x00000120  0 host Terminal\n'
        )
        self.assertEqual(managed_client_ids(listing), {0x04400001, 0x120})
        unmapped = (0, 54, 1024, 741, False)
        self.assertFalse(window_closed(unmapped, {0x04400001, 0x120}, 0x04400001))
        self.assertTrue(window_closed(unmapped, {0x120}, 0x04400001))
        self.assertTrue(window_closed(None, {0x04400001}, 0x04400001))

    def test_probe_silence_still_triggers_health_check(self):
        """A quiet probe must yield heartbeat events instead of hanging forever."""
        process = subprocess.Popen(
            [sys.executable, '-c', 'import time;time.sleep(1)'],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertIsNone(next(samples_with_heartbeat(process)))
        finally:
            process.terminate()
            process.wait(timeout=2)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
