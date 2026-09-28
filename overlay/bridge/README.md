# WMP9 GUI remoting probe

`wmp_remote_probe.cpp` is a small 32-bit Windows console host for
`WMPlayer.OCX.7`. It implements the COM site interfaces that Windows Media
Player 9 expects for GUI remoting without depending on ATL. The normal polling
mode reads state from the existing WMP GUI and writes newline-delimited JSON to
standard output; COM setup and teardown diagnostics go to standard error.

The bridge does not set a media URL or alter WMP layout. Its optional startup
mode may pause, seek to zero, and resume only the exact proxy URL supplied by
the launcher. A per-run stop marker lets it stop that owned proxy before
releasing COM. This bounded mutation is part of the synchronized audio gate,
not a general remote-control interface.

## Build

Run the build script from any working directory:

```sh
./build-remote.sh
```

It defaults to `i686-w64-mingw32-g++`, builds a statically linked 32-bit PE
executable, and atomically replaces `wmp_remote_probe.exe` only after a
successful link. Select another MinGW compiler or output name with environment
variables:

```sh
MINGW_CXX=/opt/mingw/bin/i686-w64-mingw32-g++ \
WMP_PROBE_OUTPUT=wmp_remote_probe.exe \
./build-remote.sh
```

The selected compiler must provide the Win32/OLE headers and import libraries
for `ole32`, `oleaut32`, `uuid`, and `user32`. On Debian-derived systems these
are normally supplied by the i686 MinGW-w64 C++ toolchain. An extracted
user-local toolchain also works by setting `MINGW_CXX` to its compiler path; no
repository or home-directory layout is assumed.

## Run

Set the Wine prefix and desktop-session variables for the WMP installation,
then run the executable through Wine:

```sh
export WINEPREFIX="$HOME/.wine-wmp9"
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
export WINEDEBUG=-all
wine ./wmp_remote_probe.exe 0 150
```

Arguments are the sample count (`0` means continuous) and polling interval in
milliseconds (minimum 10). Defaults are 20 samples and 250 ms.

Additional modes:

```text
wmp_remote_probe.exe 1 150 --enum-only
wmp_remote_probe.exe 20 150 --activate
wmp_remote_probe.exe 0 30 --startup-gate GATE_MARKER EXPECTED_SOURCE_URL
```

- `--enum-only` emits one Win32 WMP window tree without creating the ActiveX
  control.
- `--activate` in-place activates the hidden host for diagnostics. The overlay
  does not need this option.
- `--startup-gate` pauses and seeks the matching remote media to zero, then
  resumes it only after `GATE_MARKER` exists. `EXPECTED_SOURCE_URL` must be the
  exact Wine/WMP URL owned by this overlay session.
- `WMP9_PROBE_STOP`, when set to a per-run marker path, requests graceful
  shutdown. In startup-gate mode the bridge stops media only if the active
  remote URL still equals `EXPECTED_SOURCE_URL`.

## JSON output

Each `sample` object contains:

- `isRemote`: whether the hosted control attached to the real WMP GUI engine.
- `remoteHRESULT`: HRESULT returned while reading `isRemote`.
- `playState` and `currentPosition`: WMP transport state and position in
  seconds.
- `sourceURL`: URL reported by `currentMedia`.
- `startupHeld`: true while startup-gate mode has WMP paused near zero and has
  not received the release marker.
- `tick`: Win32 `GetTickCount()` value.
- `windows`: all top-level HWNDs owned by the `WMPlayerApp` process plus their
  descendants. Entries include decimal `hwnd`, `parent`, Windows PID,
  visibility, class, title, outer `rect`, and `clientScreen` rectangle.

HWND values, class-name suffixes, process IDs, and rectangles are runtime data,
not constants. Skin changes can replace WMP's X11 and Win32 windows. Consumers
must identify visualization windows by class prefix and validate every rectangle
against the current Wine client geometry.

Failed or unavailable automation properties are emitted as JSON `null`, not as
fabricated zero values. Consumers must require `isRemote == true`; exit status 5
means the final sample was not attached to the remote GUI engine.

## Validation

Capture standard output to a completed snapshot and validate it offline:

```sh
wine ./wmp_remote_probe.exe 20 150 > probe.jsonl 2> probe.stderr
python3 verify_probe.py probe.jsonl
```

For transport verification, operate play and pause through the visible WMP GUI,
then validate the closed capture:

```sh
python3 verify_probe.py probe.jsonl --require-pause
```

The pause check expects playing samples plus at least two paused samples with a
stable position. The normal probe observes these actions; it does not generate
them. `verify_probe.py` currently validates full-mode `WMPlayerApp` geometry, so
skin-only captures may require a purpose-built geometry check.

## COM and synchronization details

WMP calls `IServiceProvider::QueryService` with service GUID
`{DF333473-2CF7-4BE2-907F-9AAD5661364F}` and requests interface IID
`{CBB92747-741F-44FE-AB5B-F1A48F3B2A59}`. The implementation must match the
requested interface IID rather than require the service GUID to equal the
interface IID. The wrong comparison creates an isolated engine with
`isRemote=false`.

The probe runs an OLE single-threaded apartment and pumps Windows messages
between samples. It reacquires nested `controls` and `currentMedia` dispatch
interfaces on every iteration because media and skin transitions can invalidate
cached pointers. On shutdown it releases nested interfaces, closes the OLE
object, clears the client site, releases the site, destroys the hidden window,
and finally calls `OleUninitialize`. The overlay service should request this
path through the stop marker before using process termination as a last resort.

Primary interface references:

- Microsoft Learn: *Remoting the Windows Media Player Control*
- Microsoft Learn: `IWMPRemoteMediaServices::GetServiceType`
- Microsoft Learn: `IWMPPlayer4::get_isRemote`
