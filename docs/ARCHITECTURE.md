# Architecture

## Design goal

The project preserves WMP9 as the user-facing player while replacing only the video pixels that Wine fails to display. WMP9 remains authoritative for audio, playback state, seeking, and window layout. `mpv` is a muted renderer synchronized to that state.

The tested native WMP9 video path under Wine decoded or advanced media but left the renderer black. The overlay therefore does not try to repair DirectShow or replace WMP DLLs.

## High-level data flow

```text
                         +--------------------+
source file ------------>| wmp9-compat        |
                         | ffprobe + dispatch |
                         +---------+----------+
                                   |
            +----------------------+-----------------------+
            |                      |                       |
         audio only          video + audio          video without audio,
            |                      |                 forced/failed overlay
            v                      v                       |
    cached PCM WAV       cached tiny AVI proxy             v
            |             (black video + audio)      standalone mpv
            v                      |
          WMP9 <-------------------+
    GUI + audio + transport
            |
            | remote read-only state, URL, HWND geometry
            v
  32-bit wmp_remote_probe.exe
            |
            v
      top_service.py --------------------> muted borderless mpv
       transport + geometry                    original video
            |
            +---- private null sink holds WMP audio
                  until mpv frame exists and is aligned
```

## Dispatch and cache

`wmp9-compat` uses FFprobe to ignore attached cover art and determine whether a source has real video and/or audio.

- Compatible mono/stereo PCM WAV at 8–48 kHz is opened directly.
- Other audio is converted to signed 16-bit PCM WAV. Sample rate and channels are reduced when necessary to stay below practical RIFF size limits.
- The legacy full-video preparation path can create an `msmpeg4v2`/MP3 AVI capped at 1280×720 or 720×1280 and 30 fps.
- Integrated video creates a much smaller proxy: a 32×32, 1 fps black `msmpeg4v2` stream plus MP3 audio. WMP9 supplies the audio clock while `mpv` reads the original source.
- Video without audio cannot use the WMP-owned audio clock and goes directly to standalone `mpv`.

Cache keys include source path, size, modification time, format version, and output parameters. Conversion takes an exclusive per-item lock and atomically renames completed files. WAV/AVI cache use is pruned from an 8 GiB high-water mark toward 6 GiB, oldest first.

## Integrated video startup

1. The launcher refuses integrated startup if an unmanaged WMP GUI is already open.
2. FFmpeg creates or reuses the compact AVI audio proxy.
3. `startup_gate.AudioGate` creates a private PulseAudio/PipeWire-Pulse null sink and exports it as `PULSE_SINK` only for the new WMP process.
4. WMP9 opens the proxy in Now Playing mode.
5. `top_service.py` starts the 32-bit COM bridge and verifies that it is remotely attached to the real WMP GUI and observing the exact proxy URL.
6. The service identifies the live `WMP Visualization Window...` rectangle instead of relying on fixed full-mode offsets.
7. A borderless, muted, always-on-top `mpv` window opens over that rectangle with the original source paused at time zero.
8. `frame_ready.lua` records mpv's first playback-restart event after video output is configured.
9. The COM bridge holds WMP paused at zero for the startup gate. The service requires the held WMP state, mpv time zero, a rendered frame marker, a visible window, and exact geometry alignment.
10. Only then are WMP's owned sink inputs moved from the private null sink to the previous physical/default sink. mpv remains muted.

This avoids global muting and prevents the first WMP audio buffer from reaching the speakers before a rendered video frame is visible. In the recorded acceptance runs, picture led sound by about 0.8 seconds; the mechanism does not promise exact lip-sync startup on other systems.

## Runtime synchronization

The bridge emits newline-delimited JSON samples containing:

- remote-attachment status and HRESULT;
- WMP play state;
- current position;
- current media source URL;
- Win32 window tree, visibility, class names, and screen rectangles.

`top_service.py` continuously drains the bridge and keeps only the latest sample so slow geometry processing cannot replay stale state. WMP is the master clock:

- play resumes muted mpv;
- pause pauses mpv and corrects meaningful drift;
- seek moves mpv to WMP's position;
- stopped and transitional states hold mpv paused;
- drift below the controller threshold is left alone to avoid seek loops.

The overlay follows the actual WMP visualization rectangle, hides when WMP is minimized or another application is active, and re-aligns periodically in case the window manager shifts the borderless window. Compact-skin transitions can replace the WMP X11 client, so the service performs bounded rediscovery and restores focus only when Wine left no active client.

## Ownership, handoff, and shutdown

Only one integrated launch owns the overlay session lock. Opening another video creates a cooperative handoff request. The current launcher verifies the WMP GUI's Linux PID, prefix environment, and mapped `wmplayer.exe` before asking that owned GUI to close. It does not kill arbitrary Wine processes.

The COM bridge receives a stop marker and releases its WMP interfaces gracefully before process termination. This matters because abrupt COM-host shutdown caused subsequent warm starts to lose remote attachment during staging. Bounded terminate/kill remains a fallback for the owned probe process only.

If the user closes WMP during startup, the launcher treats that as cancellation and does not open a surprise standalone player. If integrated setup fails for another reason before ownership is established, the launcher can fall back to standalone `mpv`.

## Optional media-library mirror

`wmp9-library-sync` scans a Linux music tree and creates a persistent mirror with `.wav` names:

- MP3 audio is remuxed without re-encoding into a WAV container.
- Other supported formats are transcoded to stereo 192 kbit/s MP3 at 44.1 kHz in a WAV container.
- Windows-invalid path characters are replaced.
- A manifest tracks source size and modification time.
- A UTF-16 tab-separated plan carries Windows paths and metadata.

When applied, the synchronizer backs up selected WMP database files, redirects the Wine user's `Music` symlink to the mirror, and runs `wmp9-library-apply.js` through Wine's `cscript.exe`. The helper adds missing media and updates metadata through `WMPlayer.OCX.7`.

This path is intentionally separate from single-file playback because WMP's Media Library can reopen original compressed files without passing through the Linux desktop launcher.

## Persistent locations

| Location | Purpose |
| --- | --- |
| `~/.cache/wmp9-compat/` | Prepared WAV/AVI media, locks, logs, and handoff marker |
- `~/.cache/hal-wmp-overlay/` | Overlay sockets and diagnostic logs |
| `~/.local/bin/wmp9-compat` | Installed launcher |
| `~/.local/share/wmp9-compat/` | Installed overlay modules, COM bridge, and library helper |
| `~/.local/share/wmp9-compat-library/` | Optional persistent music mirror |
| `~/.local/state/wmp9-compat/` | Optional library manifest, plan, log, and first backup |
| `~/.local/share/wmp9-compat-backups/` | Installer/manual deployment backups |
| `~/.wine-wmp9-real/` | Expected WMP9 Wine prefix; supplied by the user |

## Source groups

### Launch and deployment

- `wmp9-compat` — probes media, manages caches, launches WMP/mpv, owns the private audio gate, and coordinates video handoff.
- `wmp9-wine.desktop` — desktop/MIME template; its `Exec` path is host-specific and must be rewritten at installation.
- `install_overlay.py` — repository installer for a selected user profile. It supports dry runs, renders the desktop `Exec` path, backs up existing targets, writes atomically, verifies installed hashes, and rolls back the transaction on failure.

### Overlay runtime

- `overlay/controller.py` — pure calculations and command construction for media matching, geometry, visibility, and transport updates.
- `overlay/top_service.py` — current top-level borderless overlay, window/skin tracking, transport synchronization, startup release, and graceful bridge shutdown.
- `overlay/service.py` — earlier X11-child embedding implementation plus shared bridge/window/IPC utilities used by the top-level service.
- `overlay/startup_gate.py` — private null-sink creation, owned-stream migration, and sink teardown.
- `overlay/frame_ready.lua` — mpv event hook that records a configured/rendered playback restart.

### Remote COM bridge

- `overlay/bridge/wmp_remote_probe.cpp` — 32-bit read-only WMP remote host and JSON sampler; startup-gate support temporarily holds the exact proxy media at zero.
- `overlay/bridge/build-remote.sh` — original absolute-path staging build script.
- `overlay/bridge/verify_probe.py` — fail-closed validation for remote attachment and real GUI geometry.
- `overlay/bridge/README.md` — protocol, build history, observed proof, and COM implementation notes.

### Media-library integration and WSH probes

- `wmp9-library-sync` — Linux-side mirror, metadata plan, backup, symlink redirect, and apply orchestration.
- `wmp9-library-apply.js` — add/update-only WMP Media Library apply helper used by the Python synchronizer.
- `wmp9-library-sync.js` — earlier synchronization helper that also removes stale/duplicate managed entries; retained for investigation rather than the safer default apply path.
- `wmp_library_add_probe.js` — add/play/seek/remove compatibility probe for one item.
- `wmp_library_compat_probe.js` — playback/seek check for an existing compatibility-library WAV item.
- `wmp_library_inventory.js` — exports WMP library metadata to a UTF-16 TSV file.
- `wmp_library_probe.js` — checks whether an original compressed library item plays and advances.
- `wmp_set_metadata_probe.js` — tests metadata writes through WMP COM.
- `wmp_video_metadata_probe.js` — inspects WMP's classification and dimensions for a video source.

### Automated tests

- `test_wmp9_compat.py` — launcher/cache/ownership/handoff tests and a generated FFmpeg proxy test.
- `test_wmp9_library_sync.py` — mirror format/path tests plus installer dry-run, desktop rendering, backup, and installation verification.
- `overlay/test_controller.py` — transport, geometry, media matching, and mpv command tests.
- `overlay/test_top_service.py` — window selection, visibility, focus, re-alignment, and heartbeat tests.
- `overlay/test_startup_gate.py` — private sink ownership and move/read-back behavior.
- `overlay/test_live_regressions.py` — bounded regressions for COM release, skin replacement, close latency, stale samples, startup conditions, and hidden-renderer transport.

### Staging acceptance and diagnostic probes

- `overlay/gui_acceptance.py` — WMP controls, minimize/restore, resize, overlay geometry, and close behavior.
- `overlay/integration_probe.py` — actual WMP GUI play/pause/seek versus mpv IPC state.
- `overlay/pixel_audio_acceptance.py` — screenshot pixel detection plus physical-output monitor timing, resize, and skin checks.
- `overlay/repair_acceptance.py` — physical audio, rendered pixels, handoff, close, and reopen sequence.
- `overlay/media_switch_acceptance.py` — second-media handoff and old-overlay detachment.
- `overlay/close_audio_probe.py` — orphan-audio regression after GUI close.
- `overlay/startup_timing.py` — coarse GUI/audio/picture startup timing.
- `overlay/stop_start_probe.py` — stop at first sound and resume after visible video.
- `overlay/three_faults_probe.py` — bounded reproduction of known startup/cleanup fault classes.
- `overlay/xembed_probe.py` — throwaway XEmbed-style experiment; not the production overlay.

### Spikes

- `spikes/001-wmp-activex-video/` — HTA/ActiveX experiment asking whether a hosted WMP control can display native Wine video. Its recorded verdict remains pending and it is not part of the launcher path.
