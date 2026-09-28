#!/usr/bin/env python3
"""Install the WMP9 compatibility launchers from a repository checkout.

The installer writes only to a selected user profile.  It does not modify the
Wine prefix itself.  Existing targets are backed up before replacement, and a
failed installation is rolled back from that backup.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable


@dataclass(frozen=True)
class PayloadFile:
    """Describe one repository file and its target within a user profile."""

    source: Path
    target: Path
    mode: int
    render_desktop: bool = False


# Runtime overlay files used by the public launcher.  Diagnostic and acceptance
# probes remain in the checkout and are not copied into a user's profile.
OVERLAY_FILES = (
    "controller.py",
    "service.py",
    "top_service.py",
    "startup_gate.py",
    "frame_ready.lua",
)

# The JavaScript helpers are small Windows Script Host programs.  Installing all
# root helpers keeps the library synchronizer and its manual probes together.
JAVASCRIPT_FILES = (
    "wmp9-library-apply.js",
    "wmp9-library-sync.js",
    "wmp_library_add_probe.js",
    "wmp_library_compat_probe.js",
    "wmp_library_inventory.js",
    "wmp_library_probe.js",
    "wmp_set_metadata_probe.js",
    "wmp_video_metadata_probe.js",
)


def digest(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""

    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def build_payload(repository: Path, destination: Path) -> tuple[PayloadFile, ...]:
    """Build the fixed installation plan for a checkout and target profile."""

    local = destination / ".local"
    shared = local / "share" / "wmp9-compat"
    payload = [
        PayloadFile(repository / "wmp9-compat", local / "bin/wmp9-compat", 0o755),
        PayloadFile(
            repository / "wmp9-library-sync",
            local / "bin/wmp9-library-sync",
            0o755,
        ),
    ]
    payload.extend(
        PayloadFile(repository / "overlay" / name, shared / name, 0o644)
        for name in OVERLAY_FILES
    )
    payload.extend(
        PayloadFile(repository / name, shared / name, 0o644)
        for name in JAVASCRIPT_FILES
    )
    payload.append(
        PayloadFile(
            repository / "wmp9-wine.desktop",
            local / "share/applications/wmp9-wine.desktop",
            0o644,
            render_desktop=True,
        )
    )

    # A locally compiled remote probe is optional in source checkouts.  When it
    # exists, include it so the integrated WMP/mpv overlay becomes available.
    remote_probe = repository / "overlay/bridge/wmp_remote_probe.exe"
    if remote_probe.is_file():
        payload.append(PayloadFile(remote_probe, shared / remote_probe.name, 0o755))
    return tuple(payload)


def validate_payload(payload: Iterable[PayloadFile]) -> None:
    """Fail before writing anything if a required repository file is missing."""

    missing = [str(item.source) for item in payload if not item.source.is_file()]
    if missing:
        raise FileNotFoundError("Repository payload is incomplete: " + ", ".join(missing))


def rendered_bytes(item: PayloadFile, launcher: Path) -> bytes:
    """Read a payload source, rendering the installed desktop command if needed."""

    if not item.render_desktop:
        return item.source.read_bytes()
    lines = item.source.read_text(encoding="utf-8").splitlines()
    rendered = []
    replaced = False
    for line in lines:
        if line.startswith("Exec="):
            rendered.append(f"Exec={launcher} %f")
            replaced = True
        else:
            rendered.append(line)
    if not replaced:
        raise RuntimeError(f"Desktop template has no Exec entry: {item.source}")
    return ("\n".join(rendered) + "\n").encode("utf-8")


def bytes_digest(content: bytes) -> str:
    """Return a SHA-256 digest for rendered installation content."""

    return hashlib.sha256(content).hexdigest()


def atomic_write(target: Path, content: bytes, mode: int) -> None:
    """Replace one target atomically after writing it in the same directory."""

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", dir=target.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def backup_existing(
    payload: Iterable[PayloadFile], destination: Path, backup: Path
) -> dict[str, dict[str, str]]:
    """Copy existing targets into a timestamped tree and describe each copy."""

    existing: dict[str, dict[str, str]] = {}
    for item in payload:
        if not item.target.exists():
            continue
        relative = item.target.relative_to(destination)
        saved = backup / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.target, saved)
        existing[str(item.target)] = {
            "sha256": digest(item.target),
            "backup": str(saved),
        }
    return existing


def rollback_installation(
    payload: Iterable[PayloadFile], existing: dict[str, dict[str, str]]
) -> None:
    """Restore every original target, removing files newly created by this run."""

    for item in reversed(tuple(payload)):
        saved = existing.get(str(item.target), {}).get("backup")
        if saved:
            backup_file = Path(saved)
            atomic_write(
                item.target,
                backup_file.read_bytes(),
                backup_file.stat().st_mode & 0o777,
            )
        else:
            item.target.unlink(missing_ok=True)


def install_repository(
    repository: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    timestamp: datetime | None = None,
) -> dict:
    """Install one checkout into ``destination``, or return its dry-run plan."""

    repository = repository.expanduser().resolve()
    destination = destination.expanduser().resolve()
    payload = build_payload(repository, destination)
    validate_payload(payload)
    result = {
        "status": "dry-run" if dry_run else "pending",
        "repository": str(repository),
        "destination": str(destination),
        "targets": [str(item.target) for item in payload],
    }
    if dry_run:
        return result

    instant = timestamp or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    stamp = instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ")
    backup = destination / ".local/share/wmp9-compat-backups" / stamp
    if backup.exists():
        raise FileExistsError(f"Backup directory already exists: {backup}")
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)

    existing = backup_existing(payload, destination, backup)
    launcher = destination / ".local/bin/wmp9-compat"
    expected = {
        str(item.target): bytes_digest(rendered_bytes(item, launcher)) for item in payload
    }
    manifest = {
        "repository": str(repository),
        "destination": str(destination),
        "created_utc": instant.astimezone(timezone.utc).isoformat(),
        "existing": existing,
        "targets": expected,
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    try:
        for item in payload:
            atomic_write(item.target, rendered_bytes(item, launcher), item.mode)
            if digest(item.target) != expected[str(item.target)]:
                raise RuntimeError(f"Installed file did not verify: {item.target}")
    except Exception:
        rollback_installation(payload, existing)
        raise

    result.update(
        status="installed",
        backup=str(backup),
        sha256={str(item.target): digest(item.target) for item in payload},
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options while retaining the old ``--install`` switch."""

    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--install",
        action="store_true",
        help="write files (the default remains a safe preflight for compatibility)",
    )
    action.add_argument("--dry-run", action="store_true", help="show the installation plan")
    parser.add_argument(
        "--destination",
        "--prefix",
        dest="destination",
        type=Path,
        default=Path.home(),
        help="target user profile directory (default: current home directory)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the repository installer and print a machine-readable summary."""

    args = parse_args(argv)
    result = install_repository(
        Path(__file__).resolve().parent,
        args.destination,
        dry_run=args.dry_run or not args.install,
    )
    label = "DRY_RUN" if result["status"] == "dry-run" else "INSTALLED"
    print(label, json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
