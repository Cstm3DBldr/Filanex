# Install Polymaker profiles via BBL injection

## What this does

Adds Polymaker filament profiles into Bambu Studio's BBL vendor -- the same
way Bambu's OEM install ships PolyLite/PolyTerra/Panchroma profiles. They
appear in the filament library alongside Bambu's own (Bambu PLA Basic, etc.)
filtered by `filament_vendor: "Polymaker"`.

**Strictly additive, with first-class upgrade and uninstall.** The
installer keeps a tracking file at `system/.polymaker-install.json`
listing exactly which entries and filenames it owns, plus a SHA-256 hash
of each file at install time. That ownership record is what makes:

- **Install** never touch your custom profiles or BBL OEM data.
- **Upgrade** replace just the files we previously installed (and only
  if they haven't been user-modified -- detected via hash comparison).
  Files we used to ship but no longer do get cleanly removed.
- **Uninstall** remove just the files and entries we added, leaving
  everything else in place. User-modified files are preserved by default
  (warned about, kept on disk, removed only with `--force`).

If you have your own custom profiles or vendor additions, they stay
exactly as you left them through every operation.

## Recommended: automated installer

The bundle ships `install.py` (and `install.bat` wrapper for Windows
double-clickers). Every run of the installer:

1. Auto-detects your Bambu Studio user-data `system/` folder per OS.
2. Detects whether Bambu Studio is running and asks you to save + close it.
3. Sanity-checks the target really is a Bambu Studio install before
   touching anything.
4. Backs up `BBL.json` + `BBL/filament/` to a timestamped
   `_backup-YYYYMMDD-HHMMSS/` folder under `system/` (skip with
   `--no-backup`).
5. Performs the requested operation (install / upgrade / uninstall).
6. Offers to re-launch Bambu Studio if it was running at start.

### Subcommands

```
install.py             # default: install if fresh, upgrade if previously installed
install.py install     # explicit; same as above
install.py upgrade     # explicit upgrade; errors if not previously installed
install.py uninstall   # remove every file + entry this installer added
install.py status      # show install state, change nothing
```

`install.py` (no subcommand) is the right answer for both first-time and
ongoing installs -- it auto-detects which path to take from the tracking
file at `system/.polymaker-install.json`.

### How to run it

1. Download `Filanex-install-v1.1.5.zip` (this folder, on GitHub).
2. Extract anywhere (Desktop is fine).
3. Run the installer:
   - **Windows (no Python required):** double-click `install.exe` for
     install/upgrade, or `install.bat uninstall` from a terminal for
     uninstall. The `.exe` is a self-contained ~8 MB Windows binary --
     no Python install needed.
   - **Windows fallback:** if `install.exe` was somehow not shipped, the
     `install.bat` wrapper falls back to a system Python install. Get
     Python from <https://www.python.org/downloads/> if you need it.
   - **macOS / Linux:**
     ```
     cd <unzipped-folder>
     python3 install.py            # install or upgrade
     python3 install.py uninstall  # remove
     python3 install.py status     # check
     ```
     Requires Python 3.9+, which macOS 12.3+ ships by default and most
     Linux distros include.

#### Flags (apply to all subcommands)

- `--system-dir <path>` -- override auto-detection.
- `--no-backup` -- skip the safety backup.
- `--yes` -- non-interactive: skip confirmation prompts and re-launch
  prompt. Pair with `install` for unattended re-installs after Bambu
  Studio updates.
- `--force` -- on upgrade or uninstall, overwrite/delete files even if
  they've been user-modified since install (detected via SHA-256). By
  default user-modified files are preserved with a warning.

### Upgrade behavior

When the installer detects a tracking file from a previous install, it
upgrades instead of doing a fresh install:

- Files this installer previously wrote AND that are unchanged on disk
  -> replaced with the new version.
- Files this installer previously wrote AND that have been user-modified
  -> preserved by default, removed/replaced with `--force`.
- Files we used to ship but no longer do -> removed (same modification
  rules apply).
- Files we never previously wrote -> added (skip if a same-named file
  is already there from somewhere else).

The tracking file is rewritten to reflect the post-upgrade state.

### Uninstall behavior

Removes every file and BBL.json entry recorded in the tracking file.
User-modified files are kept by default; `--force` removes them too.
The tracking file itself is deleted on a clean uninstall (or trimmed
to the kept files if any were user-modified).

## Manual install (fallback)

The auto-installer is the supported path. If you must do it manually:

1. **Back up first.** Copy `BBL.json` and the `BBL/filament/` folder to
   somewhere safe before you touch anything.
2. Quit Bambu Studio.
3. Open Bambu Studio's `system/` directory:

   | OS       | Path |
   |----------|------|
   | Windows  | `%APPDATA%\BambuStudio\system\` |
   | macOS    | `~/Library/Application Support/BambuStudio/system/` |
   | Linux    | `~/.config/BambuStudio/system/` |

4. Copy every `.json` file from the zip's `BBL/filament/` folder into
   your `system/BBL/filament/`. **Don't overwrite** any existing files.
5. Manually merge the entries listed in `additions.json` (the zip
   contains it) into your `BBL.json`'s `filament_list` array. Skip any
   entry whose `name` is already in your `filament_list`.
6. Launch Bambu Studio. Reselect your printer if needed; open Filament
   Settings to tick the Polymaker lines.

The zipped `BBL.json` is also included for reference -- it's the OEM
manifest with our additions already merged. You can use it as a
diff target, but **don't** copy it over your live BBL.json wholesale
(that would defeat the additive design and wipe your own customizations).

## Updating

Bambu Studio updates restore the OEM `BBL.json` and `BBL/filament/`,
which wipes our additions. The tracking file at
`system/.polymaker-install.json` survives, so the next install run sees
a previous install and upgrades cleanly. After each Bambu Studio update:

```
install.py --yes
```

Will detect the wiped state, re-add our files and entries, and update
the tracking file.

## Troubleshooting

### "All filaments show as Unsupported -- even Bambu's own"

When BBL.json changes, Bambu Studio may deselect or scramble the active
printer. Bambu Studio decides Supported vs Unsupported by exact-string
match on the selected printer + nozzle, so when nothing's selected,
everything reads as unsupported. Fix: open the printer dropdown
(top-left), pick your printer model, confirm the nozzle size, then
re-open Filament Settings -- the Supported tab populates correctly.

### "Polymaker vendor box is checked but no profiles appear in the dropdown"

Same root cause as above -- check the printer selection first.

### "Failed loading configuration file ..." dialog at launch

A profile's `inherits` couldn't resolve. Restore from the auto-generated
`_backup-...` folder (copy `BBL.json` and `BBL/filament/` back) and
report the filename in the error.

## Caveats

- **Bambu Studio updates clobber the install.** Re-run the installer
  after each Bambu Studio update.
- **OEM PolyLite/PolyTerra @bases keep their original chemistry.**
  Where our line @base would have collided with one Bambu ships (e.g.
  `PolyLite ASA @base`), we ship ours under `PolyLite ASA @Polymaker base`
  instead. Our leaves inherit from our @base; Bambu's existing leaves
  still inherit from theirs. Both coexist.
- **4 leaf-level collisions are skipped.** Bambu ships `PolyLite ASA @BBL
  H2D / H2DP / X1C / X1E 0.2 nozzle` directly -- we don't override them.
- **VERSION 1.1.5** -- see `_meta/conflict_log.md` for chemistry
  resolution details.
