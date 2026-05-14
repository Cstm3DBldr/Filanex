#!/usr/bin/env python3
"""Filanex filament profile installer for Bambu Studio.

Run from the unzipped bundle directory (alongside additions.json and
BBL/filament/).

Strictly additive design with first-class upgrade and uninstall support.
A tracking file at `system/.polymaker-install.json` records exactly which
files and BBL.json entries this installer owns, along with SHA-256 hashes
of each installed file. That ownership record is what makes upgrade
(replace ours, leave the rest) and uninstall (remove ours, leave the
rest) possible without ever touching anything that wasn't ours to touch.

Subcommands:
    install     Fresh install if not previously installed; upgrade if a
                tracking file is present. Default if no subcommand given.
    upgrade     Same as install but errors if no previous install detected.
    uninstall   Remove every file and BBL.json entry recorded as ours.
                Files modified since install (hash mismatch) are kept by
                default; pass --force to delete anyway.
    status      Print current install state and exit. Read-only.

What every run does, regardless of subcommand:
    1. Locates your Bambu Studio user-data system/ folder (per OS).
    2. Detects whether Bambu Studio is running; asks you to close it.
    3. Sanity-checks the target looks like a real Bambu Studio install.
    4. Backs up the current BBL.json + BBL/filament/ to a timestamped
       folder under system/ (skip with --no-backup).

Requires Python 3.9+. No external packages.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# When run as a PyInstaller .exe, __file__ points at PyInstaller's
# temporary extraction dir (sys._MEIPASS), not where the user put the
# .exe. We need the latter -- the bundle's additions.json + BBL/filament/
# live next to the .exe, not next to the bootloader's unpack dir.
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent

TRACKING_FILENAME = ".polymaker-install.json"
TRACKING_TOOL = "polymaker-installer/1"

# Installer-binary version. Bumped when the installer .exe itself
# changes (new wizard pages, new install logic, bug fixes). Independent
# of the database version (which lives in VERSION at the repo root and
# bumps when chemistry changes). The wizard fetches additions.json from
# DISTRIBUTION_BASE_URL on startup and compares its `installer_version`
# field to this constant; if remote is newer, it offers a self-update.
# bundle_bbl_inject.py parses this constant out of install.py and stamps
# it into the additions.json it ships, so they always match per release.
INSTALLER_VERSION = "1.3.3"

# Stable URL for the `update` subcommand. Points at the BBL-injection
# bundle on the project's default branch via GitHub raw. Override with
# the POLYMAKER_DISTRIBUTION_URL env var (useful for local testing or
# a different hosting plan -- see question 4 in QUESTIONS_FOR_MIKE.md).
DISTRIBUTION_BASE_URL = os.environ.get(
    "POLYMAKER_DISTRIBUTION_URL",
    "https://raw.githubusercontent.com/Cstm3DBldr/Filanex"
    "/main/install",
)

SYSTEM_DIR_DEFAULTS: dict[str, Path | None] = {
    "Windows": (Path(os.environ["APPDATA"]) / "BambuStudio" / "system")
        if os.environ.get("APPDATA") else None,
    "Darwin":  Path.home() / "Library" / "Application Support" / "BambuStudio" / "system",
    "Linux":   Path.home() / ".config" / "BambuStudio" / "system",
}

# Where the installer remembers the user's last picker selection so a
# repeat run starts pre-checked the same way (instead of forcing the
# user to uncheck the same lines every single time). Lives OUTSIDE
# Bambu Studio's system/ folder on purpose: uninstall + reinstall
# should preserve the user's last picks.
PREFS_DIR_DEFAULTS: dict[str, Path | None] = {
    "Windows": (Path(os.environ["APPDATA"]) / "PolymakerInstaller")
        if os.environ.get("APPDATA") else None,
    "Darwin":  Path.home() / "Library" / "Application Support" / "PolymakerInstaller",
    "Linux":   Path.home() / ".config" / "polymaker-installer",
}
PREFS_FILENAME = "picker-prefs.json"

PROCESS_NAMES = {
    "Windows": ["bambu-studio.exe", "BambuStudio.exe", "Bambu Studio.exe"],
    "Darwin":  ["BambuStudio", "Bambu Studio"],
    "Linux":   ["bambu-studio", "BambuStudio"],
}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def banner(msg: str) -> None:
    line = "=" * max(len(msg), 60)
    print(f"\n{line}\n{msg}\n{line}\n")


def section(msg: str) -> None:
    print(f"\n--- {msg} ---")


# ---------------------------------------------------------------------------
# Hashing + JSON IO
# ---------------------------------------------------------------------------

def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, data: object) -> None:
    """Write JSON via temp file + os.replace so a crash mid-write can't
    leave the target half-written."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def detect_system_dir(override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    osname = platform.system()
    p = SYSTEM_DIR_DEFAULTS.get(osname)
    if p is None:
        sys.exit(f"Unsupported OS: {osname}. Pass --system-dir to override.")
    return p


def find_bambu_process() -> tuple[bool, Path | None]:
    osname = platform.system()
    names = PROCESS_NAMES.get(osname, [])

    # On Windows, every subprocess.run from a --windowed (no-console)
    # PyInstaller .exe pops a brief cmd window. With this code being
    # called repeatedly from the wizard's preflight step (3+ subprocesses
    # per check, often called twice in a row), the screen flashes hard
    # enough to be a real photosensitive-epilepsy hazard. CREATE_NO_WINDOW
    # (0x08000000) suppresses the console window for the spawned
    # subprocess.
    nw_flags = 0x08000000 if osname == "Windows" else 0

    if osname == "Windows":
        for name in names:
            base = name.removesuffix(".exe")
            ps = (
                f"(Get-Process -Name '{base}' -ErrorAction SilentlyContinue | "
                f"Select-Object -First 1).Path"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True,
                creationflags=nw_flags,
            )
            path = r.stdout.strip()
            if path:
                return True, Path(path)
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True, text=True,
                creationflags=nw_flags,
            )
            if name.lower() in r.stdout.lower():
                return True, None
        return False, None

    if osname == "Darwin":
        for name in names:
            r = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, text=True,
            )
            if r.returncode == 0:
                return True, None
        return False, None

    if osname == "Linux":
        for name in names:
            r = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, text=True,
            )
            if r.returncode == 0:
                pid = r.stdout.strip().splitlines()[0]
                exe = Path(f"/proc/{pid}/exe")
                if exe.exists():
                    try:
                        return True, exe.resolve()
                    except OSError:
                        return True, None
                return True, None
        return False, None

    return False, None


def wait_for_close() -> None:
    while True:
        input("Press Enter once Bambu Studio is fully closed... ")
        time.sleep(1)
        running, _ = find_bambu_process()
        if not running:
            print("Confirmed closed.")
            return
        print("Bambu Studio still appears to be running. Close it and try again.")


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def sanity_check_target(system_dir: Path) -> None:
    if not system_dir.exists():
        sys.exit(
            f"\nERROR: System folder doesn't exist:\n  {system_dir}\n"
            f"\nHas Bambu Studio been launched at least once on this account?"
        )
    if not system_dir.is_dir():
        sys.exit(f"\nERROR: Not a directory: {system_dir}")
    bbl_json = system_dir / "BBL.json"
    bbl_dir = system_dir / "BBL"
    if not bbl_json.exists() or not bbl_dir.is_dir():
        sys.exit(
            f"\nERROR: {system_dir} doesn't look like a Bambu Studio system\n"
            f"folder. Expected to find BBL.json and a BBL/ folder inside."
        )


def sanity_check_source(need_bundle: bool) -> tuple[Path | None, Path | None]:
    """Returns (additions_path, filament_dir). Both None if uninstall mode
    and bundle isn't needed."""
    if not need_bundle:
        return None, None
    src_additions = HERE / "additions.json"
    src_filament_dir = HERE / "BBL" / "filament"
    if not src_additions.exists():
        sys.exit(
            f"ERROR: Bundle is missing additions.json. Expected at:\n"
            f"  {src_additions}\n"
            f"Run this from inside the unzipped bundle directory."
        )
    if not src_filament_dir.is_dir():
        sys.exit(
            f"ERROR: Bundle is missing BBL/filament/. Expected at:\n"
            f"  {src_filament_dir}"
        )
    return src_additions, src_filament_dir


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

# Number of timestamped _backup-* folders kept in system/. Older ones
# get pruned automatically after each new backup so they don't pile up.
BACKUP_RETENTION = 5


def back_up(system_dir: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = system_dir / f"_backup-{ts}"
    backup_dir.mkdir()
    bbl_json = system_dir / "BBL.json"
    if bbl_json.exists():
        shutil.copy2(bbl_json, backup_dir / "BBL.json")
    filament_dir = system_dir / "BBL" / "filament"
    if filament_dir.exists():
        shutil.copytree(filament_dir, backup_dir / "BBL" / "filament")
    tracking = system_dir / TRACKING_FILENAME
    if tracking.exists():
        shutil.copy2(tracking, backup_dir / TRACKING_FILENAME)
    _prune_old_backups(system_dir)
    return backup_dir


def _prune_old_backups(system_dir: Path, keep: int = BACKUP_RETENTION) -> None:
    """Keep the newest `keep` _backup-* folders; delete older ones.
    The folder name's YYYYMMDD-HHMMSS timestamp sorts lexicographically
    the same as chronologically, so a string sort is enough."""
    backups = sorted(
        (p for p in system_dir.iterdir()
         if p.is_dir() and p.name.startswith("_backup-")),
        key=lambda p: p.name,
        reverse=True,
    )
    pruned = 0
    for old in backups[keep:]:
        try:
            shutil.rmtree(old)
            pruned += 1
        except OSError as e:
            print(f"  WARN: couldn't remove old backup {old.name}: {e}")
    if pruned:
        print(f"  Pruned {pruned} old backup(s); keeping the newest "
              f"{min(len(backups), keep)}.")


# ---------------------------------------------------------------------------
# Tracking file
# ---------------------------------------------------------------------------

def tracking_path(system_dir: Path) -> Path:
    return system_dir / TRACKING_FILENAME


def load_tracking(system_dir: Path) -> dict | None:
    p = tracking_path(system_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"WARNING: Tracking file at {p} is not valid JSON. Treating as no install.")
        return None


def save_tracking(system_dir: Path, tracking: dict) -> None:
    atomic_write_json(tracking_path(system_dir), tracking)


def build_tracking(
    installed_files: list[dict],
    installed_entries: list[dict],
    database_version: str | None = None,
) -> dict:
    return {
        "tool": TRACKING_TOOL,
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bundle_database_version": database_version,
        "filament_files": installed_files,   # [{filename, sha256}]
        "filament_list_entries": installed_entries,  # [{name, sub_path}]
    }


# ---------------------------------------------------------------------------
# Picker preferences (remembered selection across runs)
# ---------------------------------------------------------------------------

def prefs_path() -> Path | None:
    """Per-user JSON file remembering the picker's last selection.
    Returns None on platforms we don't have a default for."""
    base = PREFS_DIR_DEFAULTS.get(platform.system())
    if base is None:
        return None
    return base / PREFS_FILENAME


def load_picker_prefs() -> dict | None:
    """Load remembered selection. Returns None if not present / invalid
    / on any error -- caller should fall back to its default state."""
    p = prefs_path()
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_picker_prefs(prefs: dict) -> None:
    """Persist the picker's current selection. Best-effort: silently
    swallows errors so a permission glitch on the prefs dir can never
    break the install. Creates the parent dir on demand."""
    p = prefs_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(p, prefs)
    except Exception as e:
        print(f"  (prefs save skipped: {e})")


# ---------------------------------------------------------------------------
# Bundle introspection
# ---------------------------------------------------------------------------

def load_bundle(src_additions: Path, src_filament_dir: Path) -> dict:
    """Returns {entries, files, database_version}."""
    doc = json.loads(src_additions.read_text(encoding="utf-8"))
    entries = doc["filament_list_additions"]
    database_version = doc.get("database_version")
    files: dict[str, str] = {}
    for p in src_filament_dir.iterdir():
        if p.is_file():
            files[p.name] = file_sha256(p)
    return {
        "entries": entries,
        "files": files,
        "database_version": database_version,
    }


# ---------------------------------------------------------------------------
# Manifest manipulation
# ---------------------------------------------------------------------------

def manifest_add_entries(
    user_bbl: dict, entries_to_add: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Append entries whose name isn't already in filament_list. Returns
    (added, skipped) -- the actual entry dicts, so callers can record
    exactly what they appended (don't track entries we didn't add)."""
    existing = {e["name"] for e in user_bbl.get("filament_list", [])}
    added: list[dict] = []
    skipped: list[dict] = []
    for entry in entries_to_add:
        if entry["name"] in existing:
            skipped.append(entry)
            continue
        user_bbl.setdefault("filament_list", []).append(entry)
        existing.add(entry["name"])
        added.append(entry)
    return added, skipped


def manifest_remove_entries(user_bbl: dict, names_to_remove: set[str]) -> int:
    """Remove all filament_list entries whose name is in names_to_remove.
    Returns count removed."""
    fl = user_bbl.get("filament_list", [])
    new_fl = [e for e in fl if e["name"] not in names_to_remove]
    removed = len(fl) - len(new_fl)
    user_bbl["filament_list"] = new_fl
    return removed


def enable_filaments_in_user_conf(
    system_dir: Path, names_to_enable: list[str], names_to_disable: list[str] | None = None,
) -> tuple[int, int] | None:
    """Add `names_to_enable` to BambuStudio.conf's top-level
    `filaments` list (the per-user enabled-filaments registry that
    drives the in-slicer dropdown). Without this, profile JSONs ship
    into BBL.json but stay invisible until the user manually clicks
    them through "Add/Remove filaments".

    Returns (n_added, n_already_enabled) or None if the conf can't be
    located / parsed (best-effort -- never fail the install).

    Bambu Studio is closed during install (we enforce that earlier),
    so this is a safe time to rewrite. Atomic write + .bak retained.
    """
    import hashlib

    # system_dir is .../BambuStudio/system/; conf is one level up
    conf_path = system_dir.parent / "BambuStudio.conf"
    if not conf_path.is_file():
        return None
    try:
        text = conf_path.read_text(encoding="utf-8")
        # File is one JSON object plus a trailing "# MD5 checksum <hex>"
        # comment Bambu Studio uses to detect tampering. Decode just
        # the JSON, modify, re-encode, recompute MD5.
        decoder = json.JSONDecoder()
        conf, json_end = decoder.raw_decode(text, 0)
    except Exception:
        return None

    if not isinstance(conf, dict):
        return None
    current = conf.get("filaments")
    if not isinstance(current, list):
        # Some user states have no filaments key yet -- create it.
        current = []

    enabled_set = set(current)
    n_already = 0
    n_added = 0
    for name in names_to_enable:
        if name in enabled_set:
            n_already += 1
            continue
        current.append(name)
        enabled_set.add(name)
        n_added += 1

    if names_to_disable:
        disable_set = set(names_to_disable)
        current = [n for n in current if n not in disable_set]

    conf["filaments"] = current

    # Write back: JSON re-encoded, MD5 over the JSON bytes only.
    new_json = json.dumps(conf, indent=4, ensure_ascii=False)
    md5 = hashlib.md5(new_json.encode("utf-8")).hexdigest().upper()
    new_text = f"{new_json}\n# MD5 checksum {md5}\n"

    # Backup the existing conf next to itself.
    bak_path = conf_path.with_suffix(conf_path.suffix + ".filanex-bak")
    try:
        bak_path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # best-effort

    atomic_write_text(conf_path, new_text)
    return n_added, n_already


def atomic_write_text(path: Path, text: str) -> None:
    """Same atomicity guarantee as atomic_write_json: write next-door
    then os.replace. Used for non-JSON files we still need to write
    safely (BambuStudio.conf -- JSON+MD5-comment hybrid)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Install / Upgrade
# ---------------------------------------------------------------------------

def filter_bundle_by_selection(bundle: dict, selection) -> dict:
    """Return a new bundle dict keeping only entries / files whose
    (vendor, line, material) is in selection.profile_keys.

    Bucketing must match picker.build_tree() exactly or selections
    silently drop. Newer bundles (>= db v1.1.3) ship explicit
    vendor/line/material/is_base fields on every addition; use those
    directly. Older bundles only have `name`, so fall back to the
    legacy regex-based parse_entry_name() with whatever vendor field
    happens to be there.

    @base entries are always kept when ANY material in their (vendor,
    line) is picked -- they're chemistry roots and the picker doesn't
    show them as user-pickable items."""
    from picker import parse_entry_name

    # Pre-compute the set of (vendor, line) pairs the user picked any
    # material for. Used to keep @base entries even though they aren't
    # in selection.profile_keys.
    picked_vendor_lines = {(v, l) for (v, l, _m) in selection.profile_keys}

    keep_names: set[str] = set()
    for entry in bundle["entries"]:
        if entry.get("is_base"):
            v = entry.get("vendor")
            l = entry.get("line")
            if v is not None and l is not None and (v, l) in picked_vendor_lines:
                keep_names.add(entry["name"])
            continue
        # Prefer explicit picker fields over name-regex (the regex
        # silently mis-buckets vendor-prefixed names like
        # "Anycubic PLA @BBL X1C 0.4 nozzle" as vendor="Other").
        if "vendor" in entry and "line" in entry and "material" in entry:
            tup = (entry["vendor"], entry["line"], entry["material"])
        else:
            parsed = parse_entry_name(entry["name"], entry.get("vendor"))
            if parsed is None:
                keep_names.add(entry["name"])
                continue
            tup = parsed
        if tup in selection.profile_keys:
            keep_names.add(entry["name"])
    filtered_entries = [e for e in bundle["entries"] if e["name"] in keep_names]
    keep_filenames = {e["sub_path"].split("/")[-1] for e in filtered_entries}
    filtered_files = {
        fn: sha for fn, sha in bundle["files"].items() if fn in keep_filenames
    }
    return {
        "entries": filtered_entries,
        "files": filtered_files,
        "database_version": bundle.get("database_version"),
    }


def cmd_install(
    system_dir: Path, src_additions: Path, src_filament_dir: Path,
    force: bool, selection=None, auto_fetch: bool = True,
) -> int:
    """Fresh install if no tracking file; otherwise upgrade.
    If `selection` is provided, the bundle is filtered to just the
    user's picks before applying.

    When auto_fetch is True (the default for CLI runs), the latest data
    is fetched from DISTRIBUTION_BASE_URL first -- if a newer version is
    available online, the install uses that data instead of whatever
    shipped with the .exe. Falls back to the bundled data when offline /
    fetch fails / versions match. The wizard sets auto_fetch=False
    because it does the fetch earlier (so new vendors/lines/materials
    can appear in the picker)."""
    if auto_fetch:
        fetched_dir = _try_auto_fetch_bundle(src_additions)
        if fetched_dir is not None:
            src_additions = fetched_dir / "additions.json"
            src_filament_dir = fetched_dir / "BBL" / "filament"

    tracking = load_tracking(system_dir)
    is_upgrade = tracking is not None
    bundle = load_bundle(src_additions, src_filament_dir)

    if selection is not None:
        before_entries = len(bundle["entries"])
        before_files = len(bundle["files"])
        bundle = filter_bundle_by_selection(bundle, selection)
        print(
            f"  Filtered bundle by user selection: "
            f"{len(bundle['entries'])}/{before_entries} entries, "
            f"{len(bundle['files'])}/{before_files} files."
        )

    if is_upgrade:
        return _do_upgrade(system_dir, tracking, bundle, src_filament_dir, force)
    return _do_fresh_install(system_dir, bundle, src_filament_dir)


def _do_fresh_install(
    system_dir: Path, bundle: dict, src_filament_dir: Path
) -> int:
    section("Fresh install")
    target_filament = system_dir / "BBL" / "filament"
    target_filament.mkdir(parents=True, exist_ok=True)

    # Files: copy ones that don't exist; INHERIT ones that already exist
    # with our exact content (record them in tracking so future
    # upgrade/uninstall handles them); SKIP ones with different content
    # (someone else's file -- not ours to touch).
    installed_files: list[dict] = []
    files_added = files_inherited = files_skipped = 0
    for filename, sha in bundle["files"].items():
        src = src_filament_dir / filename
        dst = target_filament / filename
        if dst.exists():
            try:
                live_sha = file_sha256(dst)
            except OSError:
                files_skipped += 1
                continue
            if live_sha == sha:
                # Same content -- claim as ours.
                installed_files.append({"filename": filename, "sha256": sha})
                files_inherited += 1
            else:
                files_skipped += 1
            continue
        shutil.copy2(src, dst)
        installed_files.append({"filename": filename, "sha256": sha})
        files_added += 1

    # Manifest entries: claim names already in the user's filament_list
    # too (inheritance) -- if BBL.json already has "PolyLite ASA @BBL ..."
    # listed and the file at that sub_path matches our hash, we own it
    # for tracking purposes.
    user_bbl_path = system_dir / "BBL.json"
    user_bbl = json.loads(user_bbl_path.read_text(encoding="utf-8"))
    existing_by_name = {e["name"]: e for e in user_bbl.get("filament_list", [])}
    added_entries: list[dict] = []
    inherited_entries: list[dict] = []
    for entry in bundle["entries"]:
        if entry["name"] in existing_by_name:
            inherited_entries.append(existing_by_name[entry["name"]])
        else:
            user_bbl.setdefault("filament_list", []).append(entry)
            existing_by_name[entry["name"]] = entry
            added_entries.append(entry)
    if added_entries:
        atomic_write_json(user_bbl_path, user_bbl)

    save_tracking(system_dir, build_tracking(
        installed_files, added_entries + inherited_entries,
        database_version=bundle.get("database_version"),
    ))

    # Auto-enable in user's filament dropdown (best-effort). On a
    # FRESH install we enable everything we just placed -- there's no
    # prior user state to preserve. Inherited entries (matched by hash
    # but already on disk) get enabled too since the user has no
    # historical disable choice for them.
    enable_names = [e["name"] for e in added_entries + inherited_entries]
    enable_result = enable_filaments_in_user_conf(system_dir, enable_names)

    print(f"  Files added:         {files_added}")
    print(f"  Files inherited:     {files_inherited}  "
          f"(existing files with matching content -- now tracked)")
    print(f"  Files skipped:       {files_skipped}  "
          f"(existing files with different content -- not ours)")
    print(f"  Entries appended:    {len(added_entries)}")
    print(f"  Entries inherited:   {len(inherited_entries)}")
    if enable_result is None:
        print(f"  Auto-enable in slicer:  SKIPPED (conf not found/parseable)")
    else:
        n_added, n_already = enable_result
        print(f"  Auto-enabled in slicer: {n_added} newly enabled, "
              f"{n_already} already enabled")
    print(f"  Tracking written:    {tracking_path(system_dir)}")
    print()
    print("=" * 60)
    print(f"  INSTALL COMPLETE -- {files_added + files_inherited} "
          f"files now tracked, {len(added_entries) + len(inherited_entries)} "
          f"entries.")
    print("=" * 60)
    if not installed_files and not added_entries and not inherited_entries:
        print("\nNothing changed -- target had no overlap with the bundle.")
    return 0


def _do_upgrade(
    system_dir: Path, tracking: dict, bundle: dict,
    src_filament_dir: Path, force: bool,
) -> int:
    section(f"Upgrade (previous install: {tracking.get('installed_at', '?')})")
    target_filament = system_dir / "BBL" / "filament"

    prev_files = {f["filename"]: f["sha256"] for f in tracking["filament_files"]}
    prev_entry_names = {e["name"] for e in tracking["filament_list_entries"]}
    new_files = bundle["files"]
    new_entry_names = {e["name"] for e in bundle["entries"]}

    # File reconciliation. Invariant: post-upgrade tracking includes ONLY
    # files we previously owned (still in bundle) plus files we wrote in
    # this run. Files the user added independently -- even if they happen
    # to match our bundle by name + content -- are NOT claimed.
    files_replaced = files_unchanged = files_kept_modified = 0
    files_added = files_removed = files_remove_kept = files_unowned = 0
    new_owned_files: list[dict] = []

    for filename, new_sha in new_files.items():
        src = src_filament_dir / filename
        dst = target_filament / filename
        prev_sha = prev_files.get(filename)
        we_previously_owned = prev_sha is not None

        if dst.exists():
            live_sha = file_sha256(dst)
            if not we_previously_owned:
                # User added a file matching one of our names. Don't touch
                # the file (skip-existing) AND don't claim ownership.
                files_unowned += 1
                continue
            if live_sha == new_sha:
                # We owned it; it's already the new version. Keep ownership.
                files_unchanged += 1
                new_owned_files.append({"filename": filename, "sha256": new_sha})
                continue
            if live_sha != prev_sha and not force:
                # We owned it, user modified it. Preserve, keep ORIGINAL
                # install-time hash so the modification stays flagged.
                print(f"  WARN: keeping user-modified {filename}")
                files_kept_modified += 1
                new_owned_files.append({"filename": filename, "sha256": prev_sha})
                continue
            # We owned it, untouched (or --force). Replace with new version.
            shutil.copy2(src, dst)
            files_replaced += 1
            new_owned_files.append({"filename": filename, "sha256": new_sha})
        else:
            # File absent. Write it and claim it.
            shutil.copy2(src, dst)
            files_added += 1
            new_owned_files.append({"filename": filename, "sha256": new_sha})

    # Files that were in the previous install but no longer in the bundle.
    obsolete_filenames = set(prev_files) - set(new_files)
    for filename in obsolete_filenames:
        dst = target_filament / filename
        if not dst.exists():
            continue
        live_sha = file_sha256(dst)
        prev_sha = prev_files[filename]
        if live_sha != prev_sha and not force:
            print(f"  WARN: keeping user-modified obsolete {filename}")
            files_remove_kept += 1
            continue
        dst.unlink()
        files_removed += 1

    # Manifest reconciliation. Same invariant: post-upgrade tracking
    # includes only entries we previously owned (still in bundle) plus
    # entries we appended in this run.
    user_bbl_path = system_dir / "BBL.json"
    user_bbl = json.loads(user_bbl_path.read_text(encoding="utf-8"))

    # 1. Drop entries we previously owned but are no longer in bundle.
    obsolete_names = prev_entry_names - new_entry_names
    entries_removed = manifest_remove_entries(user_bbl, obsolete_names)

    # 2. Update sub_path on previously-owned entries if our bundle
    # changed it. This DOES touch user_bbl, but only on entries we
    # already owned -- safe.
    new_entry_by_name = {e["name"]: e for e in bundle["entries"]}
    sub_path_updates = 0
    for e in user_bbl.get("filament_list", []):
        n = e["name"]
        if n in prev_entry_names and n in new_entry_by_name:
            new_sub = new_entry_by_name[n]["sub_path"]
            if e["sub_path"] != new_sub:
                e["sub_path"] = new_sub
                sub_path_updates += 1

    # 3. Append new entries (skip-existing). We track only what we
    # actually appended in this run.
    added_entries, skipped_entries = manifest_add_entries(user_bbl, bundle["entries"])

    atomic_write_json(user_bbl_path, user_bbl)

    # Post-upgrade ownership = previously owned (still in bundle) + newly
    # appended in this run. Anything the user owns independently stays
    # outside our tracking.
    still_owned_names = prev_entry_names & new_entry_names
    newly_added_names = {e["name"] for e in added_entries}
    new_owned_entries = [
        e for e in bundle["entries"]
        if e["name"] in still_owned_names or e["name"] in newly_added_names
    ]
    save_tracking(system_dir, build_tracking(
        new_owned_files, new_owned_entries,
        database_version=bundle.get("database_version"),
    ))

    # Auto-enable ONLY the entries we added this run (not previously-
    # owned ones that the user may have intentionally disabled since).
    # Re-enabling everything every install would clobber the user's
    # disable choices -- a regression they reported. Disable any
    # obsolete entries (we used to own them, no longer in bundle) so
    # the dropdown doesn't accumulate dead references.
    enable_names = [e["name"] for e in added_entries]
    disable_names = list(obsolete_names)
    enable_result = enable_filaments_in_user_conf(
        system_dir, enable_names, disable_names,
    )

    print(f"  Files added:                       {files_added}")
    print(f"  Files replaced:                    {files_replaced}")
    print(f"  Files unchanged:                   {files_unchanged}")
    print(f"  Files removed:                     {files_removed}")
    print(f"  User-modified files preserved:     {files_kept_modified + files_remove_kept}")
    print(f"  Files matching name but not ours:  {files_unowned}")
    print(f"  Entries added:                     {len(added_entries)}")
    print(f"  Entries skipped (not ours):        {len(skipped_entries)}")
    print(f"  Entries removed:                   {entries_removed}")
    print(f"  sub_path updates:                  {sub_path_updates}")
    if enable_result is None:
        print(f"  Auto-enable in slicer:             SKIPPED (conf not "
              f"found/parseable -- enable manually via Bambu Studio's "
              f"\"Add/Remove filaments\" dialog)")
    else:
        n_added, n_already = enable_result
        if not enable_names and not disable_names:
            print(f"  Auto-enable in slicer:             nothing to do "
                  f"(no new filaments this run -- your existing enable/"
                  f"disable choices preserved)")
        else:
            print(f"  Auto-enabled in slicer dropdown:   "
                  f"{n_added} newly enabled, {n_already} were already enabled")
    return 0


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def cmd_uninstall(system_dir: Path, force: bool) -> int:
    tracking = load_tracking(system_dir)
    if tracking is None:
        print("=" * 60)
        print("  NOTHING TO UNINSTALL")
        print("=" * 60)
        print()
        print("No tracking file found at:")
        print(f"  {tracking_path(system_dir)}")
        print()
        print("This means the installer didn't track any prior install.")
        print("If you have older Filanex files on disk from a previous")
        print("manual install, run Install first -- it will INHERIT any")
        print("existing files whose content matches the bundle, then a")
        print("future Uninstall will remove them.")
        return 0

    section(f"Uninstall (installed at: {tracking.get('installed_at', '?')})")
    target_filament = system_dir / "BBL" / "filament"

    files_removed = files_kept_modified = 0
    for entry in tracking["filament_files"]:
        filename = entry["filename"]
        recorded_sha = entry["sha256"]
        dst = target_filament / filename
        if not dst.exists():
            continue
        if not force:
            live_sha = file_sha256(dst)
            if live_sha != recorded_sha:
                print(f"  WARN: keeping user-modified {filename}")
                files_kept_modified += 1
                continue
        dst.unlink()
        files_removed += 1

    user_bbl_path = system_dir / "BBL.json"
    user_bbl = json.loads(user_bbl_path.read_text(encoding="utf-8"))
    names_to_remove = {e["name"] for e in tracking["filament_list_entries"]}
    entries_removed = manifest_remove_entries(user_bbl, names_to_remove)
    if entries_removed:
        atomic_write_json(user_bbl_path, user_bbl)

    # Also remove from user's enabled-filament dropdown so leftover
    # references don't appear as broken entries.
    enable_filaments_in_user_conf(
        system_dir, names_to_enable=[], names_to_disable=list(names_to_remove),
    )

    if files_kept_modified == 0:
        tracking_path(system_dir).unlink()
        print(f"  Tracking file removed.")
    else:
        # Keep tracking around so the user can retry uninstall after
        # reviewing the modified files. Remove only the cleanly-removed
        # entries from tracking.
        kept = [
            e for e in tracking["filament_files"]
            if (target_filament / e["filename"]).exists()
        ]
        tracking["filament_files"] = kept
        tracking["filament_list_entries"] = []
        save_tracking(system_dir, tracking)
        print(f"  Tracking updated; {files_kept_modified} files left for review.")
        print(f"  Re-run with --force to remove user-modified files anyway.")

    print(f"  Files removed:         {files_removed}")
    print(f"  User-modified kept:    {files_kept_modified}")
    print(f"  Manifest entries removed: {entries_removed}")
    print()
    print("=" * 60)
    if files_removed == 0 and entries_removed == 0:
        print("  UNINSTALL COMPLETE -- nothing to remove.")
    else:
        print(f"  UNINSTALL COMPLETE -- removed {files_removed} files "
              f"and {entries_removed} entries.")
    print("=" * 60)
    return 0


# ---------------------------------------------------------------------------
# Update -- fetch latest bundle from the stable URL, run install on it
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: int = 30) -> bytes:
    """Fetch a URL and return its body. Stdlib only -- no external deps."""
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": TRACKING_TOOL},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_version(v: str) -> tuple[int, ...]:
    """Best-effort semver tuple. Non-numeric chunks become 0 so a weird
    string can never raise -- worst case it sorts oddly."""
    out: list[int] = []
    for chunk in v.split("."):
        try:
            out.append(int(chunk))
        except ValueError:
            out.append(0)
    return tuple(out)


def check_for_installer_update(timeout: int = 10) -> str | None:
    """Probe DISTRIBUTION_BASE_URL/additions.json for an `installer_version`
    newer than this build's INSTALLER_VERSION. Returns the remote version
    string if newer, else None. Returns None on any failure -- never
    raises so the wizard can call this on startup without a try/except."""
    try:
        text = _http_get(
            DISTRIBUTION_BASE_URL + "/additions.json", timeout=timeout
        ).decode("utf-8")
        doc = json.loads(text)
        remote = doc.get("installer_version")
        if not remote:
            return None
        if _parse_version(remote) > _parse_version(INSTALLER_VERSION):
            return remote
        return None
    except Exception:
        return None


def check_remote_versions(timeout: int = 10) -> dict | None:
    """One-shot probe that returns both remote installer_version AND
    remote database_version (the bundle's profile data version).
    Returns {'installer_version': str, 'database_version': str,
    'addition_count': int} or None on any failure. Used by the wizard
    so the user can see when only the data has changed but the .exe
    is current -- the common case after a chemistry/conflict-resolution
    bug fix where install.exe is unchanged but the bundle differs."""
    try:
        text = _http_get(
            DISTRIBUTION_BASE_URL + "/additions.json", timeout=timeout
        ).decode("utf-8")
        doc = json.loads(text)
        return {
            "installer_version": doc.get("installer_version") or "?",
            "database_version":  doc.get("database_version") or "?",
            "addition_count":    len(doc.get("filament_list_additions", [])),
        }
    except Exception:
        return None


def perform_self_update(remote_version: str) -> None:
    """Download install.exe from DISTRIBUTION_BASE_URL, atomically swap
    it for the running .exe, spawn the new one with --post-update, and
    return. The CALLER is responsible for exiting immediately so the new
    process has the .exe to itself.

    Windows can't delete a running .exe but it can rename one (the live
    process keeps its file handle by NTFS reference, not by name), so:
        current install.exe -> install.old.exe
        downloaded copy     -> install.exe
        spawn install.exe --post-update
        current process exits

    The post-update boot also tries to delete install.old.exe -- best
    effort, since the prior process may not have fully released its
    handle yet. A second startup picks up anything left behind.

    Raises on download failure / OS error so the caller can surface it.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Self-update is only supported in the .exe build. Re-clone "
            "the repo / pull from git for the .py path."
        )
    if platform.system() != "Windows":
        raise RuntimeError(
            "Self-update of install.exe is Windows-only -- non-Windows "
            "users run install.py from the repo."
        )

    current_exe = Path(sys.executable).resolve()
    new_exe = current_exe.with_name(current_exe.stem + ".new" + current_exe.suffix)
    old_exe = current_exe.with_name(current_exe.stem + ".old" + current_exe.suffix)

    print(f"Downloading installer v{remote_version}...")
    data = _http_get(DISTRIBUTION_BASE_URL + "/install.exe", timeout=180)
    print(f"  Got {len(data) // 1024} KB.")
    new_exe.write_bytes(data)

    # If a stale .old.exe is still here from a prior update, try to
    # remove it. May still be locked if anything else opened it; we'll
    # try again next launch.
    if old_exe.exists():
        try:
            old_exe.unlink()
        except OSError:
            pass

    os.rename(current_exe, old_exe)
    os.rename(new_exe, current_exe)

    # Detach the new process so it survives our exit. DETACHED_PROCESS
    # = no console attached (none in --windowed builds anyway),
    # CREATE_NEW_PROCESS_GROUP so the parent process group's signals
    # don't reach the child.
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [str(current_exe), "--post-update"],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def cleanup_old_installer() -> None:
    """If a .old.exe sibling is on disk (left over by a recent
    self-update), try to delete it. Best-effort -- silent on failure
    because the prior process may still hold the handle for a moment."""
    if not getattr(sys, "frozen", False):
        return
    current_exe = Path(sys.executable).resolve()
    old_exe = current_exe.with_name(current_exe.stem + ".old" + current_exe.suffix)
    if old_exe.exists():
        try:
            old_exe.unlink()
        except OSError:
            pass


def _try_auto_fetch_bundle(local_additions: Path) -> Path | None:
    """Probe DISTRIBUTION_BASE_URL for a newer database_version than the
    bundled one. If newer, fetch the version-stamped install zip,
    extract additions.json + BBL/filament/ to a tempdir, and return
    that dir. Return None when versions match, when offline, or on
    any error -- the caller falls back to the bundled data, so this
    must never raise.
    """
    section("Checking for updated profile data")
    print(f"  Source: {DISTRIBUTION_BASE_URL}")
    try:
        if not local_additions.exists():
            print("  No local additions.json to compare against; using online copy if reachable.")
            local_version = None
        else:
            local_doc = json.loads(local_additions.read_text(encoding="utf-8"))
            local_version = local_doc.get("database_version")

        remote_text = _http_get(
            DISTRIBUTION_BASE_URL + "/additions.json", timeout=10
        ).decode("utf-8")
        remote_doc = json.loads(remote_text)
        remote_version = remote_doc.get("database_version")

        print(f"  Local version:  {local_version or '(unknown)'}")
        print(f"  Remote version: {remote_version or '(unknown)'}")

        if remote_version is not None and remote_version == local_version:
            print("  Up to date -- using bundled data.")
            return None

        print(f"  Newer data available; downloading...")
        import io
        import tempfile
        import zipfile
        # Zip filename is version-stamped (Filanex-install-v<X>.zip)
        # so each release is uniquely-named on the public repo. We
        # learned the remote version from additions.json above.
        zip_url = (
            DISTRIBUTION_BASE_URL + f"/Filanex-install-v{remote_version}.zip"
        )
        zip_bytes = _http_get(zip_url, timeout=60)
        print(f"  Got {len(zip_bytes) // 1024} KB.")

        workdir = Path(tempfile.mkdtemp(prefix="polymaker-autofetch-"))
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.filename == "additions.json":
                    zf.extract(info, workdir)
                elif info.filename.startswith("BBL/filament/"):
                    zf.extract(info, workdir)

        fetched_additions = workdir / "additions.json"
        fetched_filament = workdir / "BBL" / "filament"
        if not fetched_additions.exists() or not fetched_filament.is_dir():
            print("  Online zip missing expected files; using bundled data.")
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass
            return None

        print(f"  Using fetched data from {workdir}")
        return workdir
    except Exception as e:
        print(f"  Auto-fetch skipped ({type(e).__name__}: {e}); using bundled data.")
        return None


def cmd_update(
    system_dir: Path, force: bool, selection,
    no_backup: bool, was_running: bool, exe_path: Path | None,
    yes: bool,
) -> int:
    """Check the stable URL for a newer bundle. If newer, fetch it,
    extract additions.json + BBL/filament/, run install/upgrade against
    the freshly-fetched files."""
    section("Checking for updates")
    print(f"  Source: {DISTRIBUTION_BASE_URL}")

    # Fetch the slim additions.json first to compare versions cheaply.
    additions_url = DISTRIBUTION_BASE_URL + "/additions.json"
    try:
        additions_text = _http_get(additions_url).decode("utf-8")
    except Exception as e:
        print(f"  ERROR: couldn't fetch {additions_url}\n  {e}")
        return 1
    try:
        additions_doc = json.loads(additions_text)
    except json.JSONDecodeError as e:
        print(f"  ERROR: server returned non-JSON: {e}")
        return 1

    remote_version = additions_doc.get("database_version") or "(unknown)"
    tracking = load_tracking(system_dir)
    local_version = (
        tracking.get("bundle_database_version") if tracking else None
    ) or "(not installed)"
    print(f"  Local version:  {local_version}")
    print(f"  Remote version: {remote_version}")

    if local_version == remote_version:
        print()
        print("=" * 60)
        print(f"  ALREADY UP TO DATE -- both at version {remote_version}.")
        print(f"  Nothing fetched. No changes.")
        print("=" * 60)
        return 0

    print(f"\nUpdate available: {local_version} -> {remote_version}")
    if not yes:
        ok = input("Proceed with update? [Y/n] ").strip().lower()
        if ok not in ("", "y", "yes"):
            print("Aborted.")
            return 1

    # Fetch the bundle zip and extract the bits we need into a temp dir.
    # Filename is version-stamped (Filanex-install-v<X>.zip); we already
    # know the remote version from the version-check probe above.
    section("Downloading update")
    import io
    import tempfile
    import zipfile
    zip_url = (
        DISTRIBUTION_BASE_URL + f"/Filanex-install-v{remote_version}.zip"
    )
    print(f"  Fetching {zip_url} ...")
    try:
        zip_bytes = _http_get(zip_url, timeout=60)
    except Exception as e:
        print(f"  ERROR: couldn't fetch update zip\n  {e}")
        return 1
    print(f"  Got {len(zip_bytes) // 1024} KB.")

    workdir = Path(tempfile.mkdtemp(prefix="polymaker-update-"))
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.filename == "additions.json":
                    zf.extract(info, workdir)
                elif info.filename.startswith("BBL/filament/"):
                    zf.extract(info, workdir)
        fetched_additions = workdir / "additions.json"
        fetched_filament = workdir / "BBL" / "filament"
        if not fetched_additions.exists() or not fetched_filament.is_dir():
            print(f"  ERROR: zip didn't contain expected files; got "
                  f"{[p.name for p in workdir.rglob('*') if p.is_file()][:5]}")
            return 1

        # Run install/upgrade against the fetched bundle. The existing
        # cmd_install handles the "fresh install vs upgrade" branch
        # based on tracking presence; we just point it at the new bundle.
        if no_backup:
            print("\nSkipping backup (--no-backup).")
        else:
            section("Backup")
            backup = back_up(system_dir)
            print(f"  Backup at: {backup}")

        return cmd_install(
            system_dir, fetched_additions, fetched_filament, force,
            selection=selection,
        )
    finally:
        import shutil as _shutil
        _shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(system_dir: Path) -> int:
    tracking = load_tracking(system_dir)
    if tracking is None:
        print("Filanex filament profiles: NOT INSTALLED")
        print(f"  (no tracking file at {tracking_path(system_dir)})")
        return 0

    print("Filanex filament profiles: INSTALLED")
    print(f"  Installed at:        {tracking.get('installed_at', '?')}")
    print(f"  Tracking tool:       {tracking.get('tool', '?')}")
    print(f"  Files tracked:       {len(tracking['filament_files'])}")
    print(f"  Manifest entries:    {len(tracking['filament_list_entries'])}")

    target_filament = system_dir / "BBL" / "filament"
    missing = modified = 0
    for entry in tracking["filament_files"]:
        dst = target_filament / entry["filename"]
        if not dst.exists():
            missing += 1
        elif file_sha256(dst) != entry["sha256"]:
            modified += 1
    print(f"  Files missing on disk: {missing}")
    print(f"  Files user-modified:   {modified}")
    return 0


# ---------------------------------------------------------------------------
# Re-launch
# ---------------------------------------------------------------------------

def relaunch(exe_path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", "BambuStudio"])
        else:
            kwargs: dict = {}
            if platform.system() == "Windows":
                kwargs["creationflags"] = 0x00000200 | 0x00000008
            subprocess.Popen([str(exe_path)], close_fds=True, **kwargs)
        print("Launched.")
    except Exception as e:
        print(f"Could not auto-launch: {e}")
        print(f"Launch manually from: {exe_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    # Shared flags -- attached to both the top-level parser and every
    # subparser so they're accepted on either side of the subcommand name.
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--system-dir", type=Path, default=None,
                       help="Override the auto-detected Bambu Studio system folder.")
    flags.add_argument("--no-backup", action="store_true",
                       help="Skip the backup of BBL.json + BBL/filament/.")
    flags.add_argument("--yes", action="store_true",
                       help="Don't prompt for confirmations.")
    flags.add_argument("--force", action="store_true",
                       help="Overwrite/delete user-modified files during "
                            "upgrade or uninstall (default: preserve).")
    flags.add_argument("--all", action="store_true",
                       help="Install everything to every enabled slicer; "
                            "skip the GUI picker.")
    flags.add_argument("--no-gui", action="store_true",
                       help="Headless mode -- don't open the picker. "
                            "Implies --all unless --selection is given.")
    flags.add_argument("--post-update", action="store_true",
                       help="Internal: set by the installer when it "
                            "relaunches itself after a self-update so "
                            "the welcome screen can confirm the update "
                            "succeeded.")
    flags.add_argument("--skip-update-check", action="store_true",
                       help="Skip the on-startup check for a newer "
                            "install.exe. The bundled data fetch on the "
                            "picker step still runs.")

    parser = argparse.ArgumentParser(
        parents=[flags],
        description="Install / upgrade / uninstall Filanex filament profiles in Bambu Studio.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("install", parents=[flags],
                   help="Install or upgrade (default).")
    sub.add_parser("upgrade", parents=[flags],
                   help="Upgrade only; error if not previously installed.")
    sub.add_parser("uninstall", parents=[flags],
                   help="Remove what this installer added.")
    sub.add_parser("update", parents=[flags],
                   help="Check the stable URL for a newer bundle and "
                        "apply it. Replaces the bundled data with the "
                        "online copy.")
    sub.add_parser("status", parents=[flags],
                   help="Show install state and exit.")
    return parser


def main() -> int:
    args = make_parser().parse_args()

    # If the prior run swapped in a new .exe and relaunched us, sweep
    # the leftover install.old.exe sibling now (file handle should be
    # released by now). Best-effort.
    cleanup_old_installer()

    # No subcommand AND not headless -> open the GUI wizard. CLI
    # subcommands (install / upgrade / uninstall / update / status)
    # remain available for power users + scripting.
    if args.command is None and not args.no_gui:
        try:
            import gui as _gui  # type: ignore
        except ImportError as e:
            print(f"GUI module unavailable ({e}); falling back to CLI install.")
        else:
            try:
                system_dir = detect_system_dir(args.system_dir)
                sanity_check_target(system_dir)
            except SystemExit as e:
                # In --windowed PyInstaller builds stdout / stderr go to
                # nowhere visible. Surface the failure via a messagebox.
                import tkinter as tk  # noqa: WPS433  (lazy on purpose)
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror(
                    "Filanex installer",
                    str(e) if e.code else "Setup check failed.",
                )
                root.destroy()
                return 1
            return _gui.run(__import__(__name__), system_dir, args)

    cmd = args.command or "install"

    banner("Filanex filament profile installer for Bambu Studio")

    system_dir = detect_system_dir(args.system_dir)
    print(f"Target system folder: {system_dir}")

    if cmd == "status":
        sanity_check_target(system_dir)
        return cmd_status(system_dir)

    sanity_check_target(system_dir)
    need_bundle = cmd in ("install", "upgrade")
    src_additions, src_filament_dir = sanity_check_source(need_bundle)

    if cmd == "upgrade" and load_tracking(system_dir) is None:
        sys.exit(
            "ERROR: no tracking file found, nothing to upgrade. "
            "Run `install.py install` (or just `install.py`) first."
        )
    if cmd == "update" and load_tracking(system_dir) is None:
        # First-time installs should use `install` (with the bundled
        # data) rather than `update` (which fetches the latest online
        # version and applies it). Both work, but `update` makes the
        # roundtrip slower and less obvious.
        if not args.yes:
            print(
                "WARNING: nothing currently installed (no tracking file)."
                "\n         `update` will fetch and install the latest "
                "online bundle."
            )
            ok = input("Continue? [Y/n] ").strip().lower()
            if ok not in ("", "y", "yes"):
                print("Aborted.")
                return 1

    skip_running_check = os.environ.get("POLYMAKER_INSTALLER_SKIP_RUNNING_CHECK") == "1"
    if cmd in ("install", "upgrade", "uninstall", "update") and not skip_running_check:
        running, exe_path = find_bambu_process()
        was_running = running
        if running:
            print(
                f"\nBambu Studio appears to be running"
                + (f" ({exe_path})" if exe_path else "") + "."
                + "\nSave your work and close Bambu Studio before continuing."
            )
            wait_for_close()
        else:
            print("\nBambu Studio is not running. OK to proceed.")
            exe_path = None
    else:
        was_running = False
        exe_path = None

    if not args.yes:
        ok = input(
            f"\nProceed with {cmd!r} into {system_dir}? [Y/n] "
        ).strip().lower()
        if ok not in ("", "y", "yes"):
            print("Aborted.")
            return 1

    if not args.no_backup:
        section("Backup")
        backup = back_up(system_dir)
        print(f"  Backup at: {backup}")
    else:
        print("\nSkipping backup (--no-backup).")

    if cmd == "update":
        # Get a selection (picker or --all) so the fetched bundle is
        # filtered the same way local installs are.
        selection = None
        # `update` reads additions from the FETCHED zip, so we don't
        # have a local additions to feed the picker beforehand. Use
        # the local bundle's additions (if present) for the picker
        # tree -- that's a close-enough preview of what will be
        # installed; the actual filter applies to the fetched bundle.
        if (HERE / "additions.json").exists():
            additions_local = json.loads(
                (HERE / "additions.json").read_text(encoding="utf-8")
            )["filament_list_additions"]
            if args.all or args.no_gui:
                from picker import select_all
                selection = select_all(additions_local)
            else:
                from picker import show_picker
                selection = show_picker(additions_local)
                if selection.cancelled:
                    print("Cancelled by user.")
                    return 1
        else:
            # No local bundle to preview. Default to install-everything.
            print("(No local bundle present; will install everything from the fetched copy.)")
        rc = cmd_update(
            system_dir, args.force, selection,
            no_backup=args.no_backup, was_running=was_running,
            exe_path=exe_path, yes=args.yes,
        )
    elif cmd in ("install", "upgrade"):
        # Decide what to install: GUI picker (default, interactive) or
        # the full bundle (--all / --no-gui).
        selection = None
        if cmd in ("install", "upgrade") and src_additions is not None:
            additions = json.loads(src_additions.read_text(encoding="utf-8"))[
                "filament_list_additions"
            ]
            if args.all or args.no_gui:
                from picker import select_all
                selection = select_all(additions)
                print(f"\nSelection: install everything ({len(selection.profile_keys)} "
                      f"groups across {len(selection.slicers)} slicer(s)).")
            else:
                from picker import show_picker
                print("\nOpening selection window...")
                selection = show_picker(additions)
                if selection.cancelled:
                    print("Cancelled by user.")
                    return 1
                print(f"Selection: {len(selection.profile_keys)} groups across "
                      f"{len(selection.slicers)} slicer(s).")
            # For now only bambu_studio is implemented. Warn for any
            # other slicer the user picked (none will be checked yet
            # since the picker disables the others, but it's defensive).
            unsupported = selection.slicers - {"bambu_studio"}
            if unsupported:
                print(f"  Skipping not-yet-implemented slicers: "
                      f"{', '.join(sorted(unsupported))}")
            if "bambu_studio" not in selection.slicers:
                print("  ERROR: no implemented slicer in selection. "
                      "Bambu Studio is the only one wired up so far.")
                return 1
        rc = cmd_install(
            system_dir, src_additions, src_filament_dir, args.force,
            selection=selection,
        )
    elif cmd == "uninstall":
        rc = cmd_uninstall(system_dir, args.force)
    else:
        sys.exit(f"Unknown command: {cmd}")

    banner(f"{cmd.capitalize()} complete.")
    if cmd in ("install", "upgrade"):
        print("FIRST-LAUNCH STEP:")
        print("  When BBL.json changes, Bambu Studio may deselect your active")
        print("  printer. After launch, pick your printer in the top-left")
        print("  dropdown, then open Filament Settings (gear icon) to tick")
        print("  the Filanex lines you want.\n")

    if was_running and exe_path is not None:
        ans = "y" if args.yes else input(
            "Re-launch Bambu Studio now? [Y/n] "
        ).strip().lower()
        if ans in ("", "y", "yes"):
            relaunch(exe_path)
    elif was_running:
        print(
            "Note: Bambu Studio was running at start but I couldn't capture\n"
            "its executable path; please launch it manually."
        )

    return rc


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        rc = 130
    # Keep the console open after a double-click on Windows. Skip if
    # we're not interactive (e.g. piped, tested, or run from a script).
    # In a --windowed PyInstaller build there is no console attached
    # to the process; sys.stdin is None and `.isatty()` would crash.
    if (sys.stdin is not None and sys.stdin.isatty()
            and platform.system() == "Windows"):
        try:
            input("\nPress Enter to exit... ")
        except EOFError:
            pass
    raise SystemExit(rc)
