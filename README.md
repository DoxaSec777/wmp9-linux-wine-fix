# WMP9 Wine Media Compatibility

Experimental compatibility tooling for running Windows Media Player 9 under Wine while delegating video rendering to `mpv`.

WMP9 remains the visible player and owns the audio, transport controls, timeline, and media state. For video with audio, the launcher gives WMP9 a cached AVI audio proxy, starts a muted borderless `mpv` window over WMP's video area, and follows WMP's play, pause, seek, resize, minimize, and skin state through a read-only remote COM bridge. A private PulseAudio/PipeWire-Pulse null sink holds WMP audio until `mpv` has rendered and aligned its first frame.

> [!WARNING]
> This project is experimental and was mostly vibe-coded with AI. In other words, the code is probably pure trash, since I don’t really have the time to write something properly. 😅. Native WMP video under Wine remained black in the tested setup. The overlay, resize, skin, media-handoff, and close lifecycle went through several staging fixes, but production deployment and all Wine, desktop, audio, GPU, and hardware combinations are not guaranteed. In acceptance runs, picture preceded sound by about 0.8 seconds. Test this package alongside your current launcher before replacing anything that already works.

## What it does

- Keeps the WMP9 GUI, audio path, keyboard/transport controls, and timeline.
- Converts compressed audio to cached PCM WAV files that WMP9 can play reliably.
- Creates a compact AVI audio-clock proxy for video while `mpv` renders the original source.
- Uses a 32-bit Windows COM helper to read the live WMP instance's state and video-window geometry.
- Gates only the new WMP process's audio through a private null sink until the overlay is ready.
- Falls back to standalone `mpv` when the integrated overlay is unavailable, explicitly disabled, or unsuitable.
- Includes an optional mirrored WMP media-library workflow and staging acceptance probes.

## What it does not include

This repository contains no Windows Media Player binaries, codecs, proprietary WMP skin assets, media files, acceptance fixtures, or test recordings. You must supply a legally installed WMP9 Wine prefix and your own test media.

## Requirements at a glance

- Linux with an X11 session; XWayland and non-X11 window managers are not guaranteed.
- A working 32-bit Wine prefix with WMP9 already installed; the default path is `~/.wine-wmp9-real` and `WMP9_PREFIX` selects another prefix.
- A `wine-stable` command compatible with that prefix.
- Python 3, FFmpeg/FFprobe, `mpv`, `xdotool`, `wmctrl`, `xwininfo`, and `pactl`.
- PulseAudio or PipeWire's PulseAudio-compatible service with null-sink support.
- A 32-bit MinGW-w64 C++ compiler to build the remote bridge.

See [INSTALL.md](INSTALL.md) for dependency examples, backup, preflight, installation, MIME association, rollback, and removal.

## Playback paths

| Input | Primary path |
| --- | --- |
| Compatible PCM WAV | Opened directly in WMP9 |
| Other audio | FFmpeg creates a cached PCM WAV; WMP9 plays it |
| Video with audio and working overlay prerequisites | WMP9 plays a cached AVI audio proxy; muted `mpv` overlays the original picture |
| Video without audio | Standalone `mpv` |
| Overlay unavailable, forced off, or failed before handoff | Standalone `mpv` |
| No file argument | Optional library synchronization, then WMP9 Media Library |

The cache defaults to `~/.cache/wmp9-compat`. Generated WAV and AVI files are pruned when the cache grows above 8 GiB, down toward 6 GiB.

## Safety model

The overlay bridge is read-only during normal synchronization: it observes WMP state, source URL, and window geometry. The launcher avoids global mute changes and routes only the newly launched WMP stream through its private sink. It also refuses to start integrated playback beside an unmanaged WMP instance.

The optional media-library synchronizer is more invasive: it creates a compatibility mirror, redirects the Wine user's `Music` symlink, and updates WMP's database through `WMPlayer.OCX.7`. Back up the prefix and read the limitations before enabling it.

## Repository map

- `wmp9-compat` — main media dispatcher, cache manager, WMP launcher, overlay owner, and fallback path.
- `overlay/` — geometry/transport synchronization, audio startup gate, tests, and live acceptance probes.
- `overlay/bridge/` — source and build support for the 32-bit remote COM probe.
- `wmp9-library-sync` and WSH helpers — optional compatibility mirror and WMP Media Library integration.
- `wmp_library_*`, `wmp_*_probe.js` — targeted WMP/COM diagnostics.
- `spikes/` — isolated experiments that are not production paths.
- `install_overlay.py` — the user-profile installer with dry-run planning, atomic writes, backup, verification, and failure rollback.

A file-by-file component description is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Development and test procedures are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Current status

The staging implementation improved overlay resizing, compact-skin transitions, focus restoration, audio gating, and close/media-handoff cleanup. Unit coverage exercises cache selection, transport following, ownership checks, startup gating, geometry alignment, and COM shutdown. Live acceptance scripts additionally inspect real pixels and the physical output monitor.

These results are evidence for the tested staging system, not a promise of compatibility elsewhere. Review [docs/LIMITATIONS.md](docs/LIMITATIONS.md) before deployment.

## License and trademarks

The source in this repository is licensed under the [MIT License](LICENSE). Windows Media Player and Windows are Microsoft products. Wine and mpv are independent projects. This package is not affiliated with or endorsed by Microsoft, WineHQ, or the mpv project.
