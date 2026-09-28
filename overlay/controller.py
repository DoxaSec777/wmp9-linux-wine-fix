"""Pure policy and geometry helpers for the WMP9/mpv video overlay.

WMP remains the transport and audio authority.  The overlay process renders the
original video with muted mpv and mirrors only WMP's play, pause, and seek
state.  Geometry helpers deliberately distinguish Win32 screen coordinates,
X11 client coordinates, and mpv top-level coordinates; mixing those spaces
causes the replacement frame to cover WMP's controls or drift with decorations.

This module performs no process or GUI operations, which keeps the matching,
coordinate conversion, and synchronization policy independently testable.
"""

import re
from pathlib import Path


def expected_media_url(audio: Path) -> str:
    """Return Wine's ``Z:`` URL for the absolute WMP-compatible media copy."""
    if not audio.is_absolute():
        raise ValueError('cached WMP media path must be absolute')
    return 'Z:' + str(audio).replace('/', '\\')


def matching_media(sample: dict, audio: Path) -> bool:
    """Accept samples only from the remote GUI playing the exact owned copy."""
    source = sample.get('sourceURL')
    return (sample.get('isRemote') is True and isinstance(source, str)
            and source.casefold() == expected_media_url(audio).casefold())


def mpv_command(source: Path, wid: int, ipc_socket: Path) -> list[str]:
    """Build the legacy X11-child mpv command without audio or input handling."""
    return ['mpv', '--no-config', '--vo=x11', '--no-audio', '--pause=yes',
            '--input-vo-keyboard=no', '--no-osc', '--no-input-default-bindings',
            '--keep-open=always', f'--wid={wid}',
            f'--input-ipc-server={ipc_socket}', '--', str(source)]


def top_mpv_command(source: Path, ipc_socket: Path,
                    rect: tuple[int, int, int, int]) -> list[str]:
    """Build the independent borderless overlay used when Wine repaints children."""
    x, y, width, height = rect
    return ['mpv', '--no-config', '--no-audio', '--pause=yes',
            '--force-window=yes', '--no-border', '--ontop', '--no-focus-on-open',
            '--input-cursor-passthrough=yes', '--no-window-dragging',
            '--input-vo-keyboard=no', '--no-osc', '--no-input-default-bindings',
            '--keep-open=always', '--title=HAL-WMP-Overlay',
            f'--geometry={width}x{height}+{x}+{y}',
            f'--input-ipc-server={ipc_socket}', '--', str(source)]


def overlay_visible(wmp_mapped: bool, active_xid: int, wmp_xid: int,
                    mpv_xid: int | None = None) -> bool:
    """Show video only while WMP (or its own overlay) is the active application."""
    return wmp_mapped and (active_xid == wmp_xid or active_xid == mpv_xid)


def screen_video_rect(sample: dict, x_origin: int, y_origin: int,
                      width: int, height: int) -> tuple[int, int, int, int] | None:
    """Convert the validated WMP client-relative rectangle back to screen space."""
    rect = remote_video_rect(sample, x_origin, y_origin, width, height)
    if rect is None:
        return None
    x, y, w, h = rect
    return x + x_origin, y + y_origin, w, h


def remote_video_rect(sample: dict, x_origin: int, y_origin: int,
                      parent_width: int, parent_height: int) -> tuple[int, int, int, int] | None:
    """Map a Win32 renderer HWND into Wine's X11 top-level client coordinates.

    The bridge reports Win32 rectangles in screen coordinates, while an X11
    child passed to ``mpv --wid`` must be placed relative to the Wine client.
    Bounds checks reject stale HWND samples from skin transitions and prevent a
    cached rectangle from being mapped outside the current WMP client.
    """
    if sample.get('isRemote') is not True:
        return None
    for win in sample.get('windows', []):
        if not win.get('class', '').startswith('WMP Visualization Window') or not win.get('visible'):
            continue
        x, y, width, height = win['rect']
        rel_x, rel_y = x - x_origin, y - y_origin
        if (width > 64 and height > 64 and rel_x >= 0 and rel_y >= 0
                and rel_x + width <= parent_width + 2
                and rel_y + height <= parent_height + 2):
            return rel_x, rel_y, width, height
    return None


def parse_xwininfo(output: str) -> tuple[int, int, bool]:
    """Return X11 client size and mapped state, excluding the Xfwm frame."""
    values = {}
    for key in ("Width", "Height", "Map State"):
        match = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", output, re.MULTILINE)
        if not match:
            raise ValueError(f"missing xwininfo field: {key}")
        values[key] = match.group(1).strip()
    return int(values["Width"]), int(values["Height"]), values["Map State"] == "IsViewable"


def video_rect(parent_width: int, parent_height: int) -> tuple[int, int, int, int]:
    """Return the historical full-mode panel fallback in X11 client coordinates.

    Live services prefer the bridge-reported visualization HWND.  This fixed
    inset remains useful for diagnostics and tests, but is not valid for skins.
    """
    left, top, right, bottom = 95, 53, 201, 101
    return left, top, max(1, parent_width - left - right), max(1, parent_height - top - bottom)


def mpv_updates(wmp_state: int, wmp_position: float, mpv_position: float,
                mpv_paused: bool, threshold: float = 0.6) -> list[list]:
    """Return one-way mpv updates that follow WMP without chasing clock jitter.

    WMP is always the master clock.  The drift threshold avoids repeated seeks
    caused by sampling latency, while explicit paused-state seeks keep a user
    timeline seek visible even though neither player clock is advancing.
    """
    if wmp_state != 3:
        if wmp_state not in (1, 2, 8, 9, 10):
            return []
        commands = [] if mpv_paused else [["set_property", "pause", True]]
        if wmp_state == 2 and abs(wmp_position - mpv_position) >= threshold:
            commands.append(["set_property", "time-pos", wmp_position])
        return commands
    commands = []
    if abs(wmp_position - mpv_position) >= threshold:
        commands.append(["set_property", "time-pos", wmp_position])
    if mpv_paused:
        commands.append(["set_property", "pause", False])
    return commands
