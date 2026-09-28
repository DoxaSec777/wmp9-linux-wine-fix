# Development

## Scope

The repository is a source snapshot of an experimental WMP9/Wine/mpv compatibility project. Runtime Python uses the standard library. There is no Python package installation step and no supported public API.

Keep changes conservative around process ownership, audio routing, COM shutdown, and GUI close detection. A launcher that appears correct after startup can still leave orphan audio, close the wrong Wine process, replay stale geometry, or break the next warm start.

## Local setup

Install the runtime and MinGW dependencies described in [../INSTALL.md](../INSTALL.md). Work from the repository root. Do not use valuable media or a production Wine prefix for first tests.

The cleanest development split is:

1. Pure/unit tests without a live WMP session.
2. Bridge compilation and offline JSON validation.
3. Staging tests in a copied Wine prefix.
4. Live GUI/audio acceptance only in the intended desktop session.

## Unit tests

Run root tests:

```sh
python3 -m unittest -v test_wmp9_compat.py test_wmp9_library_sync.py
```

`test_wmp9_compat.py` includes a generated two-second FFmpeg media test, so FFmpeg codec support is required even though no fixture is stored in the repository.

Run overlay tests with the overlay directory on Python's import path:

```sh
PYTHONPATH=overlay python3 -m unittest discover -s overlay -p 'test_*.py' -v
```

Compile-check all Python sources and extensionless Python entry points:

```sh
python3 -m py_compile \
  wmp9-compat wmp9-library-sync install_overlay.py \
  test_wmp9_compat.py test_wmp9_library_sync.py overlay/*.py
```

## Build the bridge

Use a 32-bit MinGW-w64 POSIX compiler:

```sh
i686-w64-mingw32-g++-posix \
  -std=c++17 -O2 -Wall -Wextra \
  -static -static-libgcc -static-libstdc++ \
  overlay/bridge/wmp_remote_probe.cpp \
  -o overlay/bridge/wmp_remote_probe.exe \
  -lole32 -loleaut32 -luuid -luser32
file overlay/bridge/wmp_remote_probe.exe
```

Building at this path lets `install_overlay.py` include the optional bridge in its installation payload.

`overlay/bridge/build-remote.sh` records the original staging toolchain arrangement and uses a host-specific absolute directory. Prefer the explicit build above in a normal checkout.

The bridge protocol and COM details are documented in `overlay/bridge/README.md`. Important invariants:

- Require `isRemote=true` before consuming WMP state.
- Treat failed properties as missing/null, not fabricated zero values.
- Match the exact proxy source URL before controlling an overlay.
- Enumerate actual window descendants each sample; HWNDs and class suffixes are runtime values.
- Release COM interfaces gracefully through the stop-marker path.

## Generated fixtures

Do not commit proprietary or personal media. Generate synthetic files with FFmpeg for tests. Useful fixtures have:

- a visually unambiguous solid color or timestamp pattern;
- a continuous, known-frequency tone;
- distinct colors/tones for old and new media during handoff tests;
- short duration for bounded cleanup.

Acceptance needs to distinguish these events independently:

1. WMP GUI exists.
2. The private WMP stream exists.
3. A real video frame is visible on the desktop.
4. Physical-output audio becomes non-silent.
5. Pause and seek are reflected in both WMP and mpv.
6. Closing WMP removes audible output before emergency cleanup.

A mapped mpv window, a WMP sink input, an advancing WMP position, or a successful exit code is not sufficient evidence for visible/audible playback.

## Live acceptance scripts

The files below are staging tools, not a single portable test suite:

- `overlay/gui_acceptance.py`
- `overlay/integration_probe.py`
- `overlay/pixel_audio_acceptance.py`
- `overlay/repair_acceptance.py`
- `overlay/media_switch_acceptance.py`
- `overlay/close_audio_probe.py`
- `overlay/startup_timing.py`
- `overlay/stop_start_probe.py`
- `overlay/three_faults_probe.py`
- `overlay/xembed_probe.py`

Many assume a staged launcher, generated fixtures, Xfwm-style behavior, a PulseAudio monitor source, and the original prefix path. Read each script before running it. Set the documented `WMP9_TEST_*`, overlay, and probe variables where supported rather than editing production targets.

Run live tests only when:

- the physical output monitor is initially silent;
- no unrelated WMP session is active;
- `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and D-Bus values identify the real user's session;
- the test can manipulate focus, minimize/restore, resize, and skin mode without disrupting another user;
- raw timestamps, PCM samples, screenshots, and logs can be retained for diagnosis.

Do not count cleanup-induced silence as a successful close test. Measure close-to-last-audio before terminating owned fallback processes.

## Architecture rules

### WMP remains authoritative

WMP owns audio and transport. mpv is always muted in the integrated path. Follow WMP state; do not add a second independent control surface that can diverge.

### Never globally mute or stop the desktop audio server

The startup gate must create a private sink before the fresh WMP process and move only sink inputs owned by that sink. Read back the routing after release.

### Never kill arbitrary Wine processes

A forwarded `wmplayer.exe` launch may exit while another Wine process owns the GUI. Verify the Linux GUI owner against both its `WINEPREFIX` environment and mapped `wmplayer.exe`. Close only clients belonging to that verified owner.

### Handle GUI closure concurrently

A remote COM read can block while WMP starts or exits. Continue observing X11 GUI presence and cancel promptly if the user closes WMP. Do not wait for a long synchronous probe timeout before stopping owned audio.

### Consume the newest COM sample

Bridge output must be drained continuously into a bounded latest-sample queue. Slow rendering must not replay obsolete window rectangles.

### Treat skins as client replacement

Compact/full-mode changes may replace X11 clients and briefly expose no client. Rediscovery must be bounded: long waits create audible close tails, while no wait misclassifies skin changes as closure.

### Keep transport independent of visible geometry

A skin can temporarily hide the visualization child. Pause and seek synchronization must continue even when there is no valid rectangle; never remap an old cached rectangle during a geometry gap.

### Fail closed on ownership and source mismatch

Do not overlay if the bridge is not remote, the source URL differs from the exact proxy, or the WMP GUI owner cannot be verified. An unmanaged existing player must be rejected rather than joined by a second audible player.

## Library development

The optional library path is host-specific and modifies prefix state. Use a prefix copy.

- Preserve source media; all conversions go to the mirror.
- Sanitize Windows-invalid path characters.
- Keep manifest updates atomic.
- Back up WMP databases and the original `Music` symlink before redirecting.
- Prefer add/update behavior. Repeated mass removal through WMP COM can crash the player.
- Verify playback from WMP's actual Media Library, not only by launching a file path.

The Windows user-directory name comes from `WMP9_WINE_USER`, then `$USER`, then the home-directory name. Tests involving library paths should set it explicitly so results do not depend on the developer's account name.

## Change verification checklist

Before proposing a runtime change:

- [ ] Root unit tests pass.
- [ ] Overlay unit tests pass.
- [ ] The 32-bit bridge builds without new warnings.
- [ ] No proprietary media, skins, prefix files, screenshots, or generated PE files are staged.
- [ ] Audio-only playback still prepares and seeks.
- [ ] Standalone mpv fallback still receives the original source.
- [ ] Integrated startup proves real pixels before physical sound.
- [ ] Pause, seek, minimize/restore, resize, and skin transitions are verified.
- [ ] Closing WMP stops audible output promptly without global cleanup.
- [ ] A second media launch cleans up the old overlay/audio before starting the new one.
- [ ] A warm reopen still obtains `isRemote=true`.
- [ ] Rollback restores the previous launcher and desktop association.

Document the exact Wine version, desktop/window manager, audio server, GPU path, media properties, and whether a result came from unit, staging, or physical acceptance testing.
