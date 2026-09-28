# Limitations and risk notes

## Experimental status

This repository packages an experimental staging system, not a supported replacement for a known-good launcher. Production deployment and all hardware/software combinations are unverified. Test with copies, keep a rollback path, and do not replace a working setup until your own media, skins, resize behavior, close behavior, and repeated launches have passed.

Resize tracking and compact-skin handling were improved in staging. Media handoff, GUI close, COM release, and orphan-audio cleanup also needed iterative fixes. Those areas remain regression-sensitive.

## Native WMP video remains black

In the tested Wine setup, WMP9 could advance time and identify some files as video while its renderer remained black. Codec changes, renderer choices, virtual desktop use, and related experiments did not establish a reliable native WMP picture path.

The project therefore uses `mpv` for pixels. It does not claim to repair Wine's WMP9 DirectShow renderer.

## Startup timing is deliberately conservative

The private audio gate waits for a rendered mpv frame and exact overlay alignment before moving WMP's audio stream to the real sink. In acceptance runs, picture preceded sound by about 0.8 seconds. This avoids sound-before-picture but is not a guarantee of frame-accurate startup or lip synchronization on every machine.

A broken or unsupported PulseAudio/PipeWire-Pulse module setup can prevent the gate from opening or releasing. The implementation requires JSON output from `pactl` and `module-null-sink` support.

## Runtime assumptions

The package is portable across user profiles, but several runtime conventions remain:

- The launcher, overlay services, and library workflow use `WMP9_PREFIX`, with `~/.wine-wmp9-real` as the default.
- Runtime commands use the literal executable name `wine-stable`.
- The optional library workflow defaults its Windows user-directory name from `$USER` or the home-directory name; prefixes whose Windows user differs must set `WMP9_WINE_USER` explicitly.
- The source desktop entry is a portable template; `install_overlay.py` renders its installed `Exec` path for the selected profile.
- The bridge build script expects an `i686-w64-mingw32-g++` compiler by default; set `MINGW_CXX` for a differently named toolchain.
- The integrated bridge is optional in a source checkout and is installed only when `overlay/bridge/wmp_remote_probe.exe` has been built.

The library synchronizer accepts both `WMP9_PREFIX` and `WMP9_WINE_USER`. The main launcher also reads `WMP9_PREFIX`; setting only Wine's conventional `WINEPREFIX` is not equivalent.

## Desktop and window-manager constraints

The integrated overlay depends on X11 concepts and tools: X11 window IDs, `xwininfo`, `xdotool`, `wmctrl`, Xlib, active-window state, mapping/unmapping, and absolute screen geometry.

Consequences include:

- Native Wayland is not supported; behavior through XWayland is unverified.
- Window managers other than the tested Xfwm-style environment may stack, focus, decorate, or move the borderless mpv window differently.
- Multiple monitors, mixed scaling, fractional scaling, unusual panels, compositors, and virtual desktops may produce incorrect geometry.
- WMP skins can destroy and recreate top-level clients. Bounded rediscovery handles observed transitions but cannot cover every skin implementation.
- Fullscreen behavior is not presented as production-ready.

No proprietary WMP skins are included, and only user-supplied skins can be tested.

## Playback coverage

- Integrated overlay requires both a real video stream and an audio stream. Video without audio uses standalone mpv.
- Attached album art is ignored as video, but unusual stream layouts may still select an unintended first audio/video stream.
- DRM-protected, encrypted, corrupt, incomplete, or network-only media may fail.
- Subtitles and mpv's normal input UI are not exposed through the integrated overlay because mpv keyboard bindings and OSC are disabled there.
- WMP is the transport master. The synchronizer corrects drift at a threshold rather than continuously resampling or phase-locking clocks.
- Very large audio files can approach RIFF/WAV limits; the launcher may reduce sample rate or channels, or reject files it still cannot fit.
- First playback can be delayed by FFmpeg conversion. The compact video proxy still requires its complete audio transcode before WMP starts.
- The cache can consume several GiB. Automatic pruning applies to generated WAV/AVI media, not every diagnostic or library file.

## WMP process ownership

The integrated path rejects an already-running unmanaged WMP GUI rather than risking two audible players. Opening a second integrated video uses a cooperative handoff that visibly closes/reopens the owned WMP GUI; it is not seamless in-process media switching.

Wine may forward a new `wmplayer.exe` invocation to an older GUI process. Ownership checks reduce the risk of terminating the wrong process, but process/window behavior can differ across Wine versions. Do not replace those checks with broad `killall`, `pkill wine`, or global Wine-server shutdowns.

## Optional media-library risk

The media-library workflow is not required for file playback. When enabled, it:

- scans and converts a music tree;
- creates a persistent mirror;
- redirects a symlink inside the Wine prefix;
- updates WMP's Media Library database through COM;
- stores a first backup at a fixed state path.

The current default source directory is `~/Musik`, not `~/Music`. The Wine-side username defaults from the Linux environment and may not match the prefix. Review `WMP9_SOURCE_ROOT`, `WMP9_PREFIX`, and `WMP9_WINE_USER` before applying.

Mass removal through WMP COM has crashed WMP during earlier experiments. The default `wmp9-library-apply.js` therefore adds and updates without bulk deletion. The older `wmp9-library-sync.js` includes removal logic and should be treated as diagnostic/legacy code.

Back up `.wmdb` files and the original Wine `Music` symlink before use. A successful mirror conversion does not prove that WMP's database, metadata, and GUI library view are correct.

## Security and maintenance

WMP9 is obsolete software. Running it, its browser/codec surfaces, or untrusted media can expose old vulnerabilities. Use an isolated prefix, avoid untrusted files and network content, and do not treat Wine as a security boundary.

The 32-bit COM bridge is manually implemented against legacy WMP interfaces. It must report `isRemote=true` before its state is trusted. A successful process exit or JSON stream is not sufficient proof that it attached to the real GUI.

Static MinGW linking reduces deployment dependencies but does not make the executable trustworthy by itself. Build it from the included source and verify the output architecture.

## Fixtures and assets

No proprietary WMP skin assets, media collections, test songs/videos, screenshots from private media, or live acceptance fixtures are included. Several acceptance scripts refer to staging-generated fixtures or host-specific paths and will not run unchanged in a clean checkout.

Generate synthetic audio/video fixtures when possible and keep private media and captured output outside version control.
