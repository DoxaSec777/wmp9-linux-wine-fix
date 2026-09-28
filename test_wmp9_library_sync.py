#!/usr/bin/env python3
"""Unit tests for the music-library synchronizer and repository installer."""

import importlib.machinery
import importlib.util
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install_overlay as installer

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "wmp9-library-sync"
loader = importlib.machinery.SourceFileLoader("wmp9_library_sync", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
sync = importlib.util.module_from_spec(spec)
loader.exec_module(sync)


class LibrarySyncTests(unittest.TestCase):
    """Regression tests for mirrored media and Windows library paths."""
    def test_mp3_is_remuxed_without_quality_loss(self):
        """Verify that mp3 is remuxed without quality loss."""
        command = sync.build_audio_command(
            Path("/music/song.mp3"),
            Path("/library/song.wav"),
            {"codec": "mp3", "rate": 48000, "channels": 2},
        )
        joined = " ".join(command)
        self.assertIn("-c:a copy", joined)
        self.assertIn("-f wav", joined)
        self.assertNotIn("libmp3lame", joined)
    def test_flac_is_transcoded_to_compact_mp3_in_wav(self):
        """Verify that flac is transcoded to compact mp3 in wav."""
        command = sync.build_audio_command(
            Path("/music/song.flac"),
            Path("/library/song.wav"),
            {"codec": "flac", "rate": 96000, "channels": 6},
        )
        joined = " ".join(command)
        self.assertIn("-c:a libmp3lame", joined)
        self.assertIn("-b:a 192k", joined)
        self.assertIn("-ar 44100", joined)
        self.assertIn("-ac 2", joined)
        self.assertIn("-f wav", joined)
    def test_source_tree_is_mirrored_with_wav_extension(self):
        """Verify that source tree is mirrored with wav extension."""
        source_root = Path("/music")
        source = source_root / "Artist" / "Album" / "Song.flac"
        self.assertEqual(
            sync.relative_output(source, source_root),
            Path("Artist/Album/Song.wav"),
        )
    def test_unchanged_source_with_existing_output_is_reused(self):
        """Verify that unchanged source with existing output is reused."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "song.mp3"
            output = root / "song.wav"
            source.write_bytes(b"source")
            output.write_bytes(b"x" * 100)
            stat = source.stat()
            entry = {"source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns}
            self.assertFalse(sync.needs_conversion(source, output, entry))
    def test_library_item_uses_windows_music_path(self):
        """Verify that library item uses windows music path."""
        with patch.object(sync, "WINE_USER", "PublicUser", create=True):
            self.assertEqual(
                sync.windows_library_path(Path("Artist/Album/Song.wav")),
                r"C:\users\PublicUser\Music\Artist\Album\Song.wav",
            )
    def test_windows_invalid_characters_are_removed_from_output_path(self):
        """Verify that windows invalid characters are removed from output path."""
        source_root = Path("/music")
        output = sync.relative_output(source_root / "Artist" / "Was?!.mp3", source_root)
        self.assertEqual(output.parent, Path("Artist"))
        self.assertEqual(output.suffix, ".wav")
        self.assertNotIn("?", output.name)
        self.assertIn("Was_!", output.name)


class InstallerTests(unittest.TestCase):
    """Exercise installation planning without touching the real user profile."""

    def test_dry_run_lists_repository_payload_without_writing(self):
        """Verify that dry run lists repository payload without writing."""
        install_repository = getattr(installer, "install_repository", None)
        self.assertTrue(callable(install_repository), "portable installer API is missing")

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "profile"
            result = install_repository(ROOT, destination, dry_run=True)

            self.assertEqual(result["status"], "dry-run")
            self.assertFalse(destination.exists())
            targets = {Path(value) for value in result["targets"]}
            self.assertIn(destination / ".local/bin/wmp9-compat", targets)
            self.assertIn(destination / ".local/bin/wmp9-library-sync", targets)
            self.assertIn(
                destination / ".local/share/wmp9-compat/controller.py", targets
            )
            self.assertIn(
                destination / ".local/share/wmp9-compat/wmp9-library-apply.js",
                targets,
            )
            self.assertIn(
                destination / ".local/share/applications/wmp9-wine.desktop",
                targets,
            )

    def test_install_backs_up_existing_files_and_renders_desktop_exec(self):
        """Verify that install backs up existing files and renders desktop exec."""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "profile"
            old_launcher = destination / ".local/bin/wmp9-compat"
            old_desktop = destination / ".local/share/applications/wmp9-wine.desktop"
            old_launcher.parent.mkdir(parents=True)
            old_desktop.parent.mkdir(parents=True)
            old_launcher.write_text("old launcher\n", encoding="utf-8")
            old_desktop.write_text("old desktop\n", encoding="utf-8")

            try:
                result = installer.install_repository(
                    ROOT,
                    destination,
                    timestamp=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
                )
            except NotImplementedError:
                self.fail("installer still lacks its write and backup path")

            self.assertEqual(result["status"], "installed")
            self.assertEqual(
                old_launcher.read_bytes(), (ROOT / "wmp9-compat").read_bytes()
            )
            desktop_text = old_desktop.read_text(encoding="utf-8")
            self.assertIn(f"Exec={old_launcher} %f", desktop_text.splitlines())

            backup = Path(result["backup"])
            self.assertEqual(
                (backup / ".local/bin/wmp9-compat").read_text(encoding="utf-8"),
                "old launcher\n",
            )
            self.assertEqual(
                (backup / ".local/share/applications/wmp9-wine.desktop").read_text(
                    encoding="utf-8"
                ),
                "old desktop\n",
            )
            manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["destination"], str(destination.resolve()))
            self.assertIn(str(old_launcher), manifest["existing"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
