<p align="center">
  <img src="branding/01-lockups/filanex-lockup-horizontal-light.png#gh-light-mode-only" alt="Filanex" width="420">
  <img src="branding/01-lockups/filanex-lockup-horizontal-dark.png#gh-dark-mode-only" alt="Filanex" width="420">
</p>

# Filanex

A multi-vendor, conflict-resolved filament profile distribution for Bambu Studio.

Filanex curates filament chemistry across 27 vendors (Polymaker, eSUN, Creality, Elegoo, Anycubic, Ultimaker, Fillamentum, ColorFabb, Spectrum, Hatchbox, Ultrafuse, Snapmaker, Flashforge, Prusa Polymers, Fiberlogy, Filatech, Extrudr, Eryone, FlyingBear, addnorth, Eolas Prints, InfiMech, Numakers, Orca Arena, Peopoly, Overture, SUNLU) and ships them as drop-in profiles for Bambu Studio. Cross-source disagreements are resolved against published vendor TDS data using a calibration anchored to Bambu Lab's own published-data-vs-slicer behavior — keeping you off the ragged edge of every aspirational max-speed claim.

## Install (Windows)

**One file, no zip extraction.** Download `install.exe` and run it.

1. **Download** just the `.exe` — [`install/install.exe`](install/install.exe) (~11 MB). Save it to a normal folder (Desktop, Downloads, anywhere). Don't double-click into a `.zip` preview — Windows extracts only the .exe to a temp folder and you'll get an error.
2. **Close Bambu Studio** if it's open.
3. **Double-click** `install.exe`. The wizard will:
   - Locate your Bambu Studio user-data folder.
   - Download the latest filament-profile bundle from this repo into a temp folder (one-time, automatic — no manual zip step).
   - Show a picker — tick the vendors / lines / materials you want.
   - Back up your current `BBL.json` + `BBL/filament/` to a timestamped folder.
   - Install the picked profiles.
   - Auto-enable them in Bambu Studio's filament dropdown so they show up immediately.
   - Clean up the temp folder when you click Finish.
4. **Re-launch Bambu Studio**. New vendors appear in the filament selector.

The installer is self-updating: when a new `install.exe` version ships it offers an in-place update on next launch. Profile-data updates happen automatically every run — the bundle is re-fetched from this repo each time, so the installer always has the latest chemistry.

`install.exe` is a self-contained PyInstaller binary — Python + GUI + branding baked in. Nothing else needs to be installed.

> **Cosmetic warning you may occasionally see:** *"Failed to remove temporary directory: C:\Users\…\AppData\Local\Temp\_MEIxxxx"*. This is the Python bundling system's own bootloader. It happens when Windows hasn't released a file handle by the time the bootloader tries to clean up its own working folder. Safe to ignore — the next `install.exe` launch cleans up the orphaned `_MEI*` folders automatically.

**Non-Windows / Python users:** the same logic ships as [`install/install.py`](install/install.py); run with Python 3.9+ in any environment with Tk. The `.py` flow expects the bundle (`additions.json` + `BBL/filament/`) next to it OR will fetch it from this repo at runtime — same behavior as the `.exe`.

## What you're installing

Each profile is a Bambu Studio filament JSON in the OEM file structure (`<line> @base.json` + `<line> @BBL <printer> <nozzle> nozzle.json`), structurally indistinguishable from Bambu's own profiles — same key set, same key order, same array shapes, same `inherits` chain, same `compatible_printers` shape. Only the chemistry values and `filament_vendor` differ.

The installer never overwrites Bambu OEM profiles. When a Filanex profile name would collide with an OEM one, ours is renamed to `<line> @<vendor> base` so both coexist. On uninstall, only Filanex-tracked entries are removed.

## Uninstall

Run `install.exe` again, choose "Uninstall." Files modified since install (hash mismatch — the user touched them in Bambu Studio) are preserved by default; pass `--force` from the CLI to delete anyway.

## Issues, requests, contributions

This is a curated distribution — the source pipeline (canonicalization, multi-source conflict resolution, vendor scrape automation) lives in a private development repository. To suggest a chemistry correction, request a vendor or product line, or report a print issue, please **open a GitHub issue** with:

- Vendor + product line + nozzle size
- The specific value you think is wrong + your suggested replacement
- Source for the suggested value (vendor TDS PDF URL, empirical print test, etc.)

Issues will be reviewed and validated against vendor sources before merging into the next bundle release.

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details. Numerical chemistry values are treated as defensible facts re-derived from publicly-available vendor data into Filanex's canonical schema; original vendor source files are not redistributed.
