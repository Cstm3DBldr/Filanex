"""Slicer target registry.

Adding a new slicer to the installer is just appending an entry here.
The picker UI and (future) install dispatch both read from this list.

Each entry:
    name:       internal id (lowercase, ascii). Used as the key in the
                tracking file and on the CLI (--target ...).
    display:    human-readable name for the picker UI.
    enabled:    True  -> a working install adapter exists in this folder
                         (e.g. slicers/<name>.py).
                False -> shown grayed out as "(coming soon)" so the user
                         can see it on the roadmap but can't pick it.
    notes:      free-form description of the install path and format.
                Useful when writing the next adapter -- documents what
                an implementation needs to do.

When you implement an adapter:
    1. Drop a module at slicers/<name>.py exposing the adapter API
       (see slicers/base.py once it's added).
    2. Flip enabled=True here.
    3. The picker stops showing "(coming soon)" automatically.
    4. install.py routes to the new adapter when the user picks it.

Order in this list controls display order in the picker.
"""
from __future__ import annotations

SLICERS: list[dict] = [
    {
        "name": "bambu_studio",
        "display": "Bambu Studio",
        "enabled": True,
        "notes": (
            "Patches BBL.json + BBL/filament/ in the user-data system/ "
            "directory. Existing implementation lives in install.py; "
            "needs to be moved into slicers/bambu_studio.py when the "
            "registry-driven dispatch lands."
        ),
    },
    {
        "name": "orca_slicer",
        "display": "Orca Slicer",
        "enabled": False,
        "notes": (
            "Fork of Bambu Studio. Same vendor manifest format and same "
            "BBL.json + BBL/filament/ layout. Adapter should be a thin "
            "wrapper around the Bambu Studio one with a different "
            "system/ path: %APPDATA%/OrcaSlicer/system/ on Windows."
        ),
    },
    {
        "name": "prusa_slicer",
        "display": "PrusaSlicer",
        "enabled": False,
        "notes": (
            "INI-based vendor.ini bundles, not JSON. Adapter has to "
            "translate our canonical chemistry into PrusaSlicer's "
            ".ini key/value pairs and place them under the user's "
            "PrusaSlicer/vendor/ directory."
        ),
    },
    {
        "name": "super_slicer",
        "display": "SuperSlicer",
        "enabled": False,
        "notes": "PrusaSlicer fork; same INI format with extra keys.",
    },
    {
        "name": "cura",
        "display": "Ultimaker Cura",
        "enabled": False,
        "notes": (
            "Per-material .xml.fdm_material files in Cura's resources/ "
            "directory. Big translation effort -- different printer "
            "model identifiers, different temperature / flow conventions."
        ),
    },
    {
        "name": "creality_print",
        "display": "Creality Print",
        "enabled": False,
        "notes": (
            "Bambu Studio fork. Same JSON format; system/ path is "
            "user-data CrealityPrint/system/."
        ),
    },
    {
        "name": "ideamaker",
        "display": "IdeaMaker (Raise3D)",
        "enabled": False,
        "notes": "Proprietary XML profile format. Largest translation lift.",
    },
    {
        "name": "kisslicer",
        "display": "KISSlicer",
        "enabled": False,
        "notes": "Per-material text profiles; modest translation effort.",
    },
    {
        "name": "slic3r",
        "display": "Slic3r",
        "enabled": False,
        "notes": "Same INI format as PrusaSlicer (PrusaSlicer is a fork).",
    },
    {
        "name": "simplify3d",
        "display": "Simplify3D",
        "enabled": False,
        "notes": ".fff XML profiles. Per-printer, per-material.",
    },
]


def get(name: str) -> dict | None:
    for s in SLICERS:
        if s["name"] == name:
            return s
    return None


def enabled_names() -> list[str]:
    return [s["name"] for s in SLICERS if s["enabled"]]
