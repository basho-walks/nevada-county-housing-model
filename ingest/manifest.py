"""Record lineage for raw snapshots in a manifest.json kept in the snapshot's directory.

Publishers replace files in place (docs/sources.md), so the hash, not the URL, identifies a vintage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "manifest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(snapshot: Path, url: str, license_note: str, retrieved_at: str | None = None) -> Path:
    """Add or replace the entry for `snapshot` in its directory's manifest.json and return that path."""
    snapshot = Path(snapshot)
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    path = snapshot.parent / MANIFEST
    entries = json.loads(path.read_text()) if path.exists() else {}
    entries[snapshot.name] = {
        "url": url,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256(snapshot),
        "size": snapshot.stat().st_size,
        "license_note": license_note,
    }
    path.write_text(json.dumps(dict(sorted(entries.items())), indent=2) + "\n")
    return path


def verify_manifest(directory: Path) -> list[str]:
    """Return names of files whose size or hash no longer match the manifest, or that are missing."""
    directory = Path(directory)
    entries = json.loads((directory / MANIFEST).read_text())
    bad = []
    for name, meta in entries.items():
        f = directory / name
        if not f.is_file() or f.stat().st_size != meta["size"] or sha256(f) != meta["sha256"]:
            bad.append(name)
    return bad
