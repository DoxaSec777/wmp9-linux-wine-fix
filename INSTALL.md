# Installation

This package is not a WMP9 installer. It expects Windows Media Player 9 to be installed and working for basic PCM audio in a dedicated 32-bit Wine prefix. Keep your current launcher and a prefix backup until this compatibility layer passes your own playback tests.

## 1. Dependencies

### Generic dependency list

Runtime:

- Python 3
- Wine with 32-bit support and an executable named `wine-stable`
- An existing WMP9 prefix at `~/.wine-wmp9-real`
- FFmpeg and FFprobe
- mpv
- X11, `xdotool`, `wmctrl`, and `xwininfo`
- PulseAudio or PipeWire-Pulse and `pactl`, including `module-null-sink`
- The X11 client library (`libX11`)
- `xdg-mime`; `desktop-file-validate` and `update-desktop-database` are recommended
- `notify-send` is optional

Build and test:

- A 32-bit MinGW-w64 POSIX C++ compiler
- `file` for checking the generated PE executable
- Python Pillow only for pixel-based live acceptance scripts
- A real graphical/audio session with suitable `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and D-Bus environment values

### Debian/Ubuntu example

Package names vary by release. This is an example, not a universal command:

```sh
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install \
  python3 ffmpeg mpv xdotool wmctrl x11-utils pulseaudio-utils \
  libx11-6 libnotify-bin desktop-file-utils xdg-utils file \
  g++-mingw-w64-i686-posix wine wine32:i386
```

If your Wine installation provides `wine` but not `wine-stable`, either install a WineHQ stable package or create a user-local wrapper after confirming that `wine` is the command that owns the WMP9 prefix:

```sh
mkdir -p "$HOME/.local/bin"
printf '%s\n' '#!/bin/sh' 'exec wine "$@"' > "$HOME/.local/bin/wine-stable"
chmod 0755 "$HOME/.local/bin/wine-stable"
```

Make sure `~/.local/bin` is on `PATH` before launching the desktop entry.

## 2. Prepare and back up the Wine prefix

WMP9, proprietary codecs, skins, and media are not included. Use a legally obtained WMP9 installer and a dedicated 32-bit prefix. The main launcher currently assumes this exact path:

```sh
export WINEPREFIX="$HOME/.wine-wmp9-real"
test -f "$WINEPREFIX/drive_c/Program Files/Windows Media Player/wmplayer.exe"
```

Before installation:

1. Start WMP9 in the real desktop/audio session.
2. Confirm that a small PCM WAV plays, advances, pauses, and seeks.
3. Close WMP9 cleanly.
4. Copy or archive the entire prefix if it contains a valuable library or configuration.

The repository installer does not modify the Wine prefix. The optional media-library synchronizer does, so retain the prefix backup if you plan to enable that feature.

The launcher, overlay services, and library synchronizer accept `WMP9_PREFIX`. Export it in the graphical session before launching a non-default prefix. `WINEPREFIX` is also honored by the overlay services, but `WMP9_PREFIX` is the package-wide setting.

## 3. Build the 32-bit remote COM bridge

The integrated WMP/mpv overlay requires a 32-bit Windows helper. Build it into the location recognized by the installer:

```sh
i686-w64-mingw32-g++-posix \
  -std=c++17 -O2 -Wall -Wextra \
  -static -static-libgcc -static-libstdc++ \
  overlay/bridge/wmp_remote_probe.cpp \
  -o overlay/bridge/wmp_remote_probe.exe \
  -lole32 -loleaut32 -luuid -luser32
file overlay/bridge/wmp_remote_probe.exe
```

The result must be a 32-bit Windows PE executable. If it is absent, the installer still installs the launchers, overlay source, and library helpers, but integrated video remains unavailable and video falls back to standalone mpv.

Alternatively run `overlay/bridge/build-remote.sh`. Set `MINGW_CXX` when the compiler has a non-default name and `WMP_PROBE_OUTPUT` when a different output path is required.

## 4. Preflight and dry run

From the repository root, check runtime commands and the prefix:

```sh
for command in python3 wine-stable ffmpeg ffprobe mpv xdotool wmctrl xwininfo pactl; do
  command -v "$command" >/dev/null || { printf 'Missing: %s\n' "$command" >&2; exit 1; }
done

test -f "$HOME/.wine-wmp9-real/drive_c/Program Files/Windows Media Player/wmplayer.exe"
```

Run source checks:

```sh
python3 -m py_compile \
  wmp9-compat wmp9-library-sync install_overlay.py \
  test_wmp9_compat.py test_wmp9_library_sync.py overlay/*.py
python3 -m unittest -v test_wmp9_compat.py test_wmp9_library_sync.py
PYTHONPATH=overlay python3 -m unittest discover -s overlay -p 'test_*.py' -v

desktop-file-validate wmp9-wine.desktop
```

Some root tests generate a short temporary media file and require FFmpeg support for AAC, MPEG-4, MP3, and AVI.

The installer defaults to a non-writing dry run. Both commands below print a `DRY_RUN` JSON plan:

```sh
python3 install_overlay.py
python3 install_overlay.py --dry-run
```

To exercise the full write/backup/render path without touching your home directory, install into a temporary profile and inspect it:

```sh
TEST_PROFILE=$(mktemp -d)
python3 install_overlay.py --install --destination "$TEST_PROFILE"
find "$TEST_PROFILE/.local" -type f -print
rm -rf "$TEST_PROFILE"
```

You can also test media preparation without starting WMP:

```sh
python3 ./wmp9-compat --prepare-only /absolute/path/to/test-audio-or-video
```

Use media you are permitted to process. The repository intentionally includes no fixtures.

## 5. Install into the user profile

Run from the repository root:

```sh
python3 install_overlay.py --install
```

The installer:

- installs both launchers under `~/.local/bin/`;
- installs overlay modules and Windows Script Host helpers under `~/.local/share/wmp9-compat/`;
- includes `overlay/bridge/wmp_remote_probe.exe` when it was built in step 3;
- renders the desktop `Exec` line as an absolute path for the selected profile;
- saves existing target files under a timestamped `~/.local/share/wmp9-compat-backups/` directory;
- writes targets atomically and verifies their SHA-256 digests;
- restores the previous files automatically if installation fails.

The successful `INSTALLED` JSON output contains the exact backup path. Save it:

```sh
BACKUP="$HOME/.local/share/wmp9-compat-backups/PASTE-TIMESTAMP-HERE"
```

A different profile root can be selected with `--destination PATH` or its alias `--prefix PATH`. This option selects a Unix user-profile directory; it is not the Wine prefix.

Refresh and validate the desktop entry:

```sh
desktop-file-validate "$HOME/.local/share/applications/wmp9-wine.desktop"
command -v update-desktop-database >/dev/null && \
  update-desktop-database "$HOME/.local/share/applications"
```

Verify installed playback before changing MIME defaults:

```sh
"$HOME/.local/bin/wmp9-compat" --prepare-only /absolute/path/to/test-media
WMP9_FORCE_MPV=1 "$HOME/.local/bin/wmp9-compat" /absolute/path/to/test-video
```

The second command verifies the standalone-mpv fallback. Run an integrated overlay test only after WMP9 is closed and the live desktop/audio environment is correct.

## 6. Optional WMP Media Library mirror

The installer includes `wmp9-library-sync` and its WSH helpers, but the feature is not applied automatically during installation. The synchronizer converts or remuxes supported audio into an MP3-in-WAV mirror, redirects the selected Wine user's `Music` symlink, and adds/updates metadata through WMP COM.

Set the Windows user-directory name explicitly when it differs from the Linux account name:

```sh
export WMP9_WINE_USER="your-wine-user-directory"
test -L "$HOME/.wine-wmp9-real/drive_c/users/$WMP9_WINE_USER/Music"
```

Back up the prefix before proceeding. Then prepare the mirror without changing WMP's database or the Wine `Music` link:

```sh
WMP9_SOURCE_ROOT="$HOME/Music" \
WMP9_LIBRARY_ROOT="$HOME/.local/share/wmp9-compat-library" \
WMP9_WINE_USER="$WMP9_WINE_USER" \
"$HOME/.local/bin/wmp9-library-sync" --prepare-only
```

Inspect the mirror, manifest, and generated plan. To apply it, close WMP9 and run the synchronizer without `--prepare-only`. A bare `wmp9-compat` launch also attempts synchronization before opening WMP's Media Library.

The synchronizer refuses to redirect `Music` unless it is already a symlink to the configured source tree or the compatibility mirror.

## 7. MIME associations

The desktop file advertises supported audio and video types, but installation does not make it the default. Associate only formats you have tested.

Back up current defaults after setting `BACKUP` to the installer's backup directory:

```sh
MIME_BACKUP="$BACKUP/mime-defaults.tsv"
for mime in audio/mpeg audio/wav audio/flac audio/ogg video/mp4 video/x-matroska video/webm video/x-msvideo; do
  printf '%s\t%s\n' "$mime" "$(xdg-mime query default "$mime")"
done > "$MIME_BACKUP"
```

Set selected defaults:

```sh
for mime in audio/mpeg audio/wav audio/flac audio/ogg video/mp4 video/x-matroska video/webm video/x-msvideo; do
  xdg-mime default wmp9-wine.desktop "$mime"
done
```

Confirm each result with `xdg-mime query default MIME_TYPE`. The full declared list is in `wmp9-wine.desktop`.

## 8. Environment overrides

### Main launcher and overlay

| Variable | Effect |
| --- | --- |
| `WMP9_PREFIX` | Wine prefix used by the launcher, overlay services, and library workflow; default `~/.wine-wmp9-real` |
| `WMP9_CACHE` | Prepared-media cache, log, locks, and overlay handoff state; default `~/.cache/wmp9-compat` |
| `WMP9_LIBRARY_SYNC` | Path to the optional library-sync executable |
| `WMP9_OVERLAY_DIR` | Directory containing `controller.py`, `service.py`, `top_service.py`, `startup_gate.py`, and `frame_ready.lua` |
| `WMP_OVERLAY_PROBE` | Path to `wmp_remote_probe.exe` |
| `WMP9_FORCE_MPV=1` | Disable integrated video and use standalone mpv |
| `WMP9_TRACE` | Write diagnostic state when valid overlay geometry cannot be found |
| `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS` | Select the live graphical/audio user session |

`WMP9_GATE_FILE`, `WMP9_GATE_SINK`, `WMP9_GATE_TARGET`, `WMP9_FRAME_READY`, `PULSE_SINK`, and `WMP9_PROBE_STOP` are internal coordination variables. Do not set them manually for normal launches.

### Library synchronizer

| Variable | Default |
| --- | --- |
| `WMP9_SOURCE_ROOT` | `~/Musik` |
| `WMP9_LIBRARY_ROOT` | `~/.local/share/wmp9-compat-library` |
| `WMP9_STATE_ROOT` | `~/.local/state/wmp9-compat` |
| `WMP9_PREFIX` | `~/.wine-wmp9-real` |
| `WMP9_WINE_USER` | `$USER`, then the home-directory name |
| `WMP9_LIBRARY_JS` | `~/.local/share/wmp9-compat/wmp9-library-apply.js` |

`WMP9_WINE_USER` must be one Windows user-directory name, not a path.

### Acceptance probes

Some staging scripts also recognize `WMP9_TEST_LAUNCHER`, `WMP9_TEST_OVERLAY_DIR`, and `WMP9_TEST_PROBE`. Read each probe before use; these are development overrides, not normal launcher settings.

## 9. Rollback

The installer automatically rolls back if its own transaction fails. To manually restore a completed installation, use the `manifest.json` in the backup directory printed by the installer:

```sh
python3 - "$BACKUP/manifest.json" <<'PY'
from pathlib import Path
import json
import shutil
import sys

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
existing = manifest["existing"]
for raw_target in reversed(list(manifest["targets"])):
    target = Path(raw_target)
    saved = existing.get(raw_target, {}).get("backup")
    if saved:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(saved, target)
    else:
        target.unlink(missing_ok=True)
PY
command -v update-desktop-database >/dev/null && \
  update-desktop-database "$HOME/.local/share/applications"
```

Restore saved MIME defaults:

```sh
while IFS="$(printf '\t')" read -r mime desktop; do
  [ -n "$desktop" ] && xdg-mime default "$desktop" "$mime"
done < "$BACKUP/mime-defaults.tsv"
```

If a previous default was empty, remove this package's desktop entry or explicitly choose another installed player; the XDG tools have no portable universal "unset default" operation.

### Restore after library synchronization

Close WMP9 before restoring its database or `Music` link. The synchronizer's first backup is stored under:

```text
~/.local/state/wmp9-compat/backups/pre-library-sync/
```

That directory may contain `CurrentDatabase_59R.wmdb`, `wmpfolders.wmdb`, and `wine-music-link-target.txt`. With the same `WMP9_PREFIX`, `WMP9_STATE_ROOT`, and `WMP9_WINE_USER` used during synchronization:

1. Restore the two database files to `drive_c/users/$WMP9_WINE_USER/AppData/Local/Microsoft/Media Player/` inside the prefix.
2. Replace `drive_c/users/$WMP9_WINE_USER/Music` with a symlink to the exact target recorded in `wine-music-link-target.txt`.
3. Do not guess the previous link target or restore a database while WMP is running.

## 10. Uninstall

Restore any wanted backup and MIME defaults first. Then remove the installed payload:

```sh
rm -f \
  "$HOME/.local/bin/wmp9-compat" \
  "$HOME/.local/bin/wmp9-library-sync" \
  "$HOME/.local/share/wmp9-compat/controller.py" \
  "$HOME/.local/share/wmp9-compat/service.py" \
  "$HOME/.local/share/wmp9-compat/top_service.py" \
  "$HOME/.local/share/wmp9-compat/startup_gate.py" \
  "$HOME/.local/share/wmp9-compat/frame_ready.lua" \
  "$HOME/.local/share/wmp9-compat/wmp_remote_probe.exe" \
  "$HOME/.local/share/wmp9-compat/wmp9-library-apply.js" \
  "$HOME/.local/share/wmp9-compat/wmp9-library-sync.js" \
  "$HOME/.local/share/wmp9-compat/wmp_library_add_probe.js" \
  "$HOME/.local/share/wmp9-compat/wmp_library_compat_probe.js" \
  "$HOME/.local/share/wmp9-compat/wmp_library_inventory.js" \
  "$HOME/.local/share/wmp9-compat/wmp_library_probe.js" \
  "$HOME/.local/share/wmp9-compat/wmp_set_metadata_probe.js" \
  "$HOME/.local/share/wmp9-compat/wmp_video_metadata_probe.js" \
  "$HOME/.local/share/applications/wmp9-wine.desktop"
```

Optionally remove generated data only after confirming that it is no longer needed:

- `~/.cache/wmp9-compat`
- `~/.cache/hal-wmp-overlay`
- `~/.local/share/wmp9-compat-library`
- `~/.local/state/wmp9-compat`

The Wine prefix is not removed by uninstall. Keep or delete it according to your own WMP9/Wine backup policy.
