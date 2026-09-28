#!/usr/bin/env python3
"""Regression tests for the public WMP9 compatibility launcher."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import signal
import sys
import time
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

SCRIPT = Path(__file__).with_name("wmp9-compat")
loader = importlib.machinery.SourceFileLoader("wmp9_compat", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
wmp = importlib.util.module_from_spec(spec)
loader.exec_module(wmp)


class VideoCompatibilityTests(unittest.TestCase):
    """Regression tests for media conversion and video dispatch."""
    def test_landscape_video_is_capped_at_720p_and_30fps(self):
        """Verify that landscape video is capped at 720p and 30fps."""
        info = {"width": 3840, "height": 2160, "fps": 60.0}
        self.assertEqual(wmp.video_parameters(info), (1280, 720, 30.0))

    def test_portrait_video_is_capped_without_distortion(self):
        """Verify that portrait video is capped without distortion."""
        info = {"width": 2160, "height": 3840, "fps": 30.0}
        self.assertEqual(wmp.video_parameters(info), (720, 1280, 30.0))

    def test_small_video_is_not_upscaled_and_dimensions_stay_even(self):
        """Verify that small video is not upscaled and dimensions stay even."""
        info = {"width": 481, "height": 853, "fps": 25.0}
        self.assertEqual(wmp.video_parameters(info), (480, 852, 25.0))

    def test_video_command_uses_wmp9_compatible_avi_codecs(self):
        """Verify that video command uses wmp9 compatible avi codecs."""
        source = Path("/media/input.mkv")
        output = Path("/cache/output.part.avi")
        info = {"width": 1920, "height": 1080, "fps": 59.94}
        command = wmp.build_video_command(source, output, info)
        joined = " ".join(command)
        self.assertIn("-map 0:v:0", joined)
        self.assertIn("-map 0:a:0?", joined)
        self.assertIn("-c:v msmpeg4v2", joined)
        self.assertIn("-c:a libmp3lame", joined)
        self.assertIn("-f avi", joined)
        self.assertIn("scale=1280:720", joined)
        self.assertIn("fps=30", joined)

    def test_cache_pruning_includes_video_and_audio_outputs(self):
        """Verify that cache pruning includes video and audio outputs."""
        self.assertEqual(wmp.CACHE_SUFFIXES, {".wav", ".avi"})

    def test_bare_player_launch_synchronizes_the_media_library(self):
        """Verify that bare player launch synchronizes the media library."""
        self.assertTrue(wmp.should_sync_library([]))
        self.assertFalse(wmp.should_sync_library(["/music/song.mp3"]))
        self.assertFalse(wmp.should_sync_library(["--prepare-only", "/music/song.mp3"]))

    def test_bare_player_launch_opens_media_library_instead_of_white_now_playing(self):
        """Verify that bare player launch opens media library instead of white now playing."""
        self.assertEqual(wmp.player_command(None)[-2:], ["/Task", "MediaLibrary"])
        self.assertNotIn("/Task", wmp.player_command(Path("/cache/song.wav")))

    def test_overlay_proxy_encodes_only_sound_with_tiny_black_avi_video(self):
        """Verify that overlay proxy encodes only sound with tiny black avi video."""
        command = wmp.build_overlay_audio_command(Path('/videos/clip.mkv'), Path('/cache/audio.avi'))
        joined = ' '.join(command)
        self.assertIn('-f lavfi -i color=c=black:s=32x32:r=1', joined)
        self.assertIn('-map 0:v:0 -map 1:a:0', joined)
        self.assertIn('-c:v msmpeg4v2', joined)
        self.assertIn('-c:a libmp3lame', joined)
        self.assertIn('-shortest', joined)
        self.assertNotIn('scale=', joined)

    def test_overlay_audio_cache_tracks_source_changes(self):
        """Verify that overlay audio cache tracks source changes."""
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / 'clip.mkv'
            source.write_bytes(b'a')
            first = wmp.overlay_audio_cache_path(source)
            source.write_bytes(b'ab')
            second = wmp.overlay_audio_cache_path(source)
            self.assertNotEqual(first, second)
            self.assertEqual(first.suffix, '.avi')
            self.assertIn('overlay-audio', first.name)

    def test_overlay_audio_proxy_is_real_and_cached(self):
        """Verify that overlay audio proxy is real and cached."""
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'clip.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=10',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                            '-t', '2', '-c:v', 'mpeg4', '-c:a', 'aac', str(source)],
                           check=True, timeout=15)
            original = source.read_bytes()
            with patch.object(wmp, 'CACHE', root / 'cache'):
                first = wmp.prepare_overlay_audio(source, quiet=True)
                self.assertEqual(wmp.probe(first)['codec'], 'mp3')
                self.assertTrue(wmp.probe(first)['has_video'])
                self.assertLess(first.stat().st_size, 1_000_000)
                self.assertEqual(first, wmp.prepare_overlay_audio(source, quiet=True))
            self.assertEqual(source.read_bytes(), original)

    def test_video_without_audio_or_force_fallback_keeps_mpv(self):
        """Verify that video without audio or force fallback keeps mpv."""
        self.assertTrue(wmp.prefer_overlay({'has_video': True, 'has_audio': True}, True, False))
        self.assertFalse(wmp.prefer_overlay({'has_video': True, 'has_audio': False}, True, False))
        self.assertFalse(wmp.prefer_overlay({'has_video': True, 'has_audio': True}, True, True))
        self.assertFalse(wmp.prefer_overlay({'has_video': True, 'has_audio': True}, False, False))

    def test_overlay_waits_for_live_wmp_gui_media_and_visualization(self):
        """Verify that overlay waits for live wmp gui media and visualization."""
        audio = Path('/cache/audio.avi')
        sample = {'type': 'sample', 'isRemote': True,
                  'sourceURL': wmp.windows_path(audio),
                  'windows': [{'class': 'WMP Visualization Window123', 'visible': True}]}
        self.assertTrue(wmp.wmp_video_ready(sample, audio))
        self.assertFalse(wmp.wmp_video_ready({**sample, 'isRemote': False}, audio))
        self.assertFalse(wmp.wmp_video_ready({**sample, 'sourceURL': 'Z:\\wrong.avi'}, audio))
        self.assertFalse(wmp.wmp_video_ready({**sample, 'windows': []}, audio))

    def test_overlay_retries_probe_until_actual_gui_is_ready(self):
        """Verify that overlay retries probe until actual gui is ready."""
        audio = Path('/cache/audio.avi')
        ready = {'type': 'sample', 'isRemote': True,
                 'sourceURL': wmp.windows_path(audio),
                 'windows': [{'class': 'WMP Visualization Window123', 'visible': True}]}
        replies = [subprocess.CompletedProcess([], 0, '{"type":"sample","isRemote":false,"windows":[]}\n', ''),
                   subprocess.CompletedProcess([], 0, __import__('json').dumps(ready) + '\n', '')]
        with patch.object(wmp, 'run_wmp_probe', side_effect=[(reply, True) for reply in replies]) as runner:
            self.assertTrue(wmp.wait_for_wmp_video(audio, Path('/probe.exe'), {},
                                                    max_wait=5, poll_interval=0,
                                                    gui_present=lambda: True))
            self.assertEqual(runner.call_count, 2)

    def test_overlay_start_cancels_if_user_closes_wmp_during_probe_wait(self):
        """Verify that overlay start cancels if user closes wmp during probe wait."""
        audio = Path('/cache/audio.avi')
        gui = iter((True, False))
        reply = subprocess.CompletedProcess([], 0,
                                            '{"type":"sample","isRemote":false,"windows":[]}\n', '')
        with patch.object(wmp, 'run_wmp_probe', return_value=(reply, True)) as runner:
            with self.assertRaises(wmp.OverlayCancelled):
                wmp.wait_for_wmp_video(audio, Path('/probe.exe'), {}, max_wait=5,
                                       poll_interval=0, gui_present=lambda: next(gui))
            self.assertEqual(runner.call_count, 1)

    def test_probe_wait_interrupts_sleeping_process_when_gui_closes(self):
        """Verify that probe wait interrupts sleeping process when gui closes."""
        present = iter((True, False))
        started = time.monotonic()
        with self.assertRaises(wmp.OverlayCancelled):
            wmp.run_wmp_probe([sys.executable, '-c', 'import time;time.sleep(4)'], {},
                              lambda: next(present), require_gui=True)
        self.assertLess(time.monotonic() - started, 2)

    def test_closing_wmp_during_start_terminates_its_owned_audio_process(self):
        """Verify that closing wmp during start terminates its owned audio process."""
        self.check_close_cleanup(96025, None, False)

    def test_closing_wmp_stops_wine_gui_owner_even_if_launch_child_exited(self):
        """Verify that closing wmp stops wine gui owner even if launch child exited."""
        self.check_close_cleanup(96020, 0, True)

    def test_launch_loop_honors_cooperative_handoff(self):
        """Verify that launch loop honors cooperative handoff."""
        self.check_close_cleanup(96025, None, False, handoff=True)

    def check_close_cleanup(self, launch_pid, launch_status, kills_gui_owner, handoff=False):
        """Exercise shutdown behavior for launch-child, GUI-owner, and handoff cases."""
        gate=Mock();gate.name='test';gate.environment.return_value={}
        player=Mock(pid=launch_pid);player.poll.return_value=launch_status
        service=Mock(returncode=0);service.poll.side_effect=[None,0]
        with tempfile.TemporaryDirectory() as directory:
            if handoff:
                gate.open.side_effect = lambda: (Path(directory)/'overlay-switch.request').touch()
            with (patch.object(wmp, 'close_for_handoff') as closer,
                  patch.object(wmp,'prepare_overlay_audio',return_value=Path('/cache/audio.avi')),
                  patch.object(wmp,'overlay_commands',return_value=(['wine'],['service'])),
                  patch.object(wmp,'LOG',Path(directory)/'launcher.log'),
                  patch.object(wmp,'CACHE',Path(directory)),
                  patch.dict(sys.modules,{'startup_gate':SimpleNamespace(AudioGate=lambda:gate)}),
                  patch.object(wmp.subprocess,'Popen',side_effect=[player,service]),
                  patch.object(wmp,'wmp_gui_present',return_value=False),
                  patch.object(wmp,'wmp_gui_owner',return_value=96025),
                  patch.object(wmp,'verified_wmp_owner',return_value=True),
                  patch.object(wmp.os,'kill') as kill):
                with self.assertRaises(wmp.OverlayCancelled):
                    wmp.launch_integrated_video(Path('/video.mp4'),Path('/overlay'))
                gate.close.assert_called_once()
                if handoff:
                    closer.assert_called_once_with(96025)
                else:
                    closer.assert_not_called()
                if kills_gui_owner:
                    kill.assert_called_once_with(96025,signal.SIGTERM)
                else:
                    player.terminate.assert_called_once()
                    player.wait.assert_called_once()

    def test_failed_service_stops_owned_audio_before_unloading_silent_sink(self):
        """Verify that failed service stops owned audio before unloading silent sink."""
        events=[]
        gate=Mock();gate.name='test';gate.environment.return_value={}
        gate.close.side_effect=lambda:events.append('gate-close')
        player=Mock(pid=96020);player.poll.return_value=0
        service=Mock(returncode=1);service.poll.side_effect=[None,1]
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(wmp,'prepare_overlay_audio',return_value=Path('/cache/audio.avi')),
                  patch.object(wmp,'overlay_commands',return_value=(['wine'],['service'])),
                  patch.object(wmp,'LOG',Path(directory)/'launcher.log'),
                  patch.object(wmp,'CACHE',Path(directory)),
                  patch.dict(sys.modules,{'startup_gate':SimpleNamespace(AudioGate=lambda:gate)}),
                  patch.object(wmp.subprocess,'Popen',side_effect=[player,service]),
                  patch.object(wmp,'wmp_gui_present',side_effect=[False,True,True]),
                  patch.object(wmp,'wmp_gui_owner',return_value=96025),
                  patch.object(wmp,'verified_wmp_owner',return_value=True),
                  patch.object(wmp.os,'kill',side_effect=lambda *args:events.append('kill'))):
                with self.assertRaises(RuntimeError):
                    wmp.launch_integrated_video(Path('/video.mp4'),Path('/overlay'))
        self.assertEqual(events,['kill','gate-close'])

    def test_user_closing_wmp_does_not_launch_separate_mpv(self):
        """Verify that user closing wmp does not launch separate mpv."""
        with (patch.object(wmp.sys, 'argv', ['wmp9-compat', '/video.mp4']),
              patch.object(wmp, 'normalize_input', return_value=Path('/video.mp4')),
              patch.object(wmp, 'probe', return_value={'has_video': True, 'has_audio': True}),
              patch.object(wmp, 'overlay_available', return_value=True),
              patch.object(wmp, 'launch_integrated_video',
                           side_effect=wmp.OverlayCancelled('closed')),
              patch.object(wmp, 'launch_video') as fallback,
              patch.object(wmp, 'log')):
            self.assertEqual(wmp.main(), 0)
            fallback.assert_not_called()

    def test_wmp_gui_owner_ignores_mpv_and_tracks_real_wine_pid(self):
        """Verify that wmp gui owner ignores mpv and tracks real wine pid."""
        listing = ('0x05600001  2 96025  wmplayer.exe.wmplayer.exe  host Windows Media Player\n'
                   '0x07200002  2 96026  mpv.mpv  host Windows Media Player 9 — clip\n')
        self.assertEqual(wmp.parse_wmp_gui_owner(listing), 96025)
        self.assertIsNone(wmp.parse_wmp_gui_owner(listing.splitlines()[1] + '\n'))

    def test_wmp_owner_must_match_prefix_and_executable_mapping(self):
        """Verify that wmp owner must match prefix and executable mapping."""
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / '96025'
            proc.mkdir()
            prefix = Path(directory) / 'wine-prefix'
            (proc / 'environ').write_bytes(
                b'WINEPREFIX=' + str(prefix).encode() + b'\0DISPLAY=:0\0'
            )
            (proc / 'maps').write_text(
                f'abc {prefix}/drive_c/Program Files/'
                'Windows Media Player/wmplayer.exe\n'
            )
            self.assertTrue(wmp.verified_wmp_owner(96025, prefix, Path(directory)))
            self.assertFalse(wmp.verified_wmp_owner(96025, Path('/different'), Path(directory)))
            (proc / 'maps').write_text(
                f'abc {prefix}/drive_c/windows/explorer.exe\n'
            )
            self.assertFalse(wmp.verified_wmp_owner(96025, prefix, Path(directory)))

    def test_launcher_opens_actual_wmp_and_muted_video_service(self):
        """Verify that launcher opens actual wmp and muted video service."""
        source = Path('/videos/source.mp4')
        audio = Path('/cache/audio.avi')
        player, overlay = wmp.overlay_commands(source, audio, Path('/overlay'))
        self.assertEqual(player, ['wine-stable', wmp.PLAYER, '/Task', 'NowPlaying', wmp.windows_path(audio)])
        self.assertEqual(overlay[1:], ['/overlay/top_service.py', str(source), str(audio)])

    def test_second_launcher_requests_cooperative_close_before_acquiring_session(self):
        """Verify that second launcher requests cooperative close before acquiring session."""
        self.assertTrue(callable(getattr(wmp, 'overlay_session', None)), 'cooperative session handoff is missing')
        with tempfile.TemporaryDirectory() as directory, patch.object(wmp, 'CACHE', Path(directory)):
            with wmp.overlay_session():
                program = (
                    'import importlib.machinery,pathlib;'
                    f'w=importlib.machinery.SourceFileLoader("w",{str(SCRIPT)!r}).load_module();'
                    f'w.CACHE=pathlib.Path({directory!r});'
                    '\nwith w.overlay_session(): print("ACQUIRED",flush=True)'
                )
                child = subprocess.Popen([sys.executable, '-c', program], stdout=subprocess.PIPE, text=True)
                try:
                    until = time.monotonic() + 3
                    while not (Path(directory)/'overlay-switch.request').exists() and time.monotonic() < until:
                        time.sleep(.01)
                    self.assertTrue((Path(directory)/'overlay-switch.request').exists())
                    self.assertIsNone(child.poll(), 'new launcher must await old audio/video cleanup')
                except BaseException:
                    child.terminate();child.wait();child.stdout.close();raise
            output, _ = child.communicate(timeout=3)
            self.assertEqual(output.strip(), 'ACQUIRED')
            self.assertFalse((Path(directory)/'overlay-switch.request').exists())

    def test_handoff_closes_only_verified_owned_gui(self):
        """Verify that handoff closes only verified owned gui."""
        closer = getattr(wmp, 'close_for_handoff', None)
        self.assertTrue(callable(closer), 'owned GUI handoff is missing')
        listing = ('0x000000aa 0 123 wmplayer.exe.wmplayer.exe host Windows Media Player\n'
                   '0x000000bb 0 456 wmplayer.exe.wmplayer.exe host Windows Media Player\n')
        with patch.object(wmp, 'verified_wmp_owner', return_value=True), patch.object(wmp.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, listing)) as run:
            closer(123)
            self.assertEqual(run.call_args_list[-1].args[0], ['wmctrl', '-ic', '0x000000aa'])
            self.assertEqual(run.call_count, 2)
        with patch.object(wmp, 'verified_wmp_owner', return_value=False), patch.object(wmp.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                closer(123)
            run.assert_not_called()

    def test_existing_unmanaged_player_never_triggers_second_audio_player(self):
        """Verify that existing unmanaged player never triggers second audio player."""
        gate=Mock()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(wmp, 'CACHE', Path(directory)), \
             patch.object(wmp, 'LOG', Path(directory)/'log'), \
             patch.object(wmp.sys, 'argv', ['wmp9-compat', '/video.mp4']), \
             patch.object(wmp, 'normalize_input', return_value=Path('/video.mp4')), \
             patch.object(wmp, 'probe', return_value={'has_video':True, 'has_audio':True}), \
             patch.object(wmp, 'overlay_available', return_value=True), \
             patch.object(wmp, 'prepare_overlay_audio', return_value=Path('/audio.avi')), \
             patch.object(wmp, 'wmp_gui_present', return_value=True), \
             patch.object(wmp, 'notify'), patch.object(wmp, 'launch_video') as fallback:
            self.assertEqual(wmp.main(), 1)
            fallback.assert_not_called()

    def test_video_launch_uses_mpv_with_the_original_source(self):
        """Verify that video launch uses mpv with the original source."""
        source = Path("/videos/input.mkv")
        command = wmp.video_player_command(source)
        self.assertEqual(command[0], "mpv")
        self.assertIn("--osc=yes", command)
        self.assertIn("--keep-open=no", command)
        self.assertEqual(command[-2:], ["--", str(source)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
