<p align="center">
  <img src="branding/01-lockups/filanex-lockup-horizontal-light.png#gh-light-mode-only" alt="Filanex" width="420">
  <img src="branding/01-lockups/filanex-lockup-horizontal-dark.png#gh-dark-mode-only" alt="Filanex" width="420">
</p>

# Filanex

A multi-vendor, conflict-resolved filament profile distribution for Bambu Studio.

Filanex curates filament chemistry across 27 vendors (Polymaker, eSUN, Creality, Elegoo, Anycubic, Ultimaker, Fillamentum, ColorFabb, Spectrum, Hatchbox, Ultrafuse, Snapmaker, Flashforge, Prusa Polymers, Fiberlogy, Filatech, Extrudr, Eryone, FlyingBear, addnorth, Eolas Prints, InfiMech, Numakers, Orca Arena, Peopoly, Overture, SUNLU) and ships them as drop-in profiles for Bambu Studio. Cross-source disagreements are resolved against published vendor TDS data using a calibration anchored to Bambu Lab's own published-data-vs-slicer behavior — keeping you off the ragged edge of every aspirational max-speed claim.

## Install

1. **Download** [`install/install.exe`](install/install.exe) (Windows, ~11 MB)
2. **Close Bambu Studio** if it's open
3. **Double-click** `install.exe`. The wizard will:
   - Locate your Bambu Studio user-data folder
   - Show a picker — tick the vendors / lines / materials you want
   - Back up your current `BBL.json` + `BBL/filament/` to a timestamped folder
   - Install the picked profiles
   - Auto-enable them in Bambu Studio's filament dropdown so they show up immediately
4. **Re-launch Bambu Studio**. New vendors appear in the filament selector.

The installer is self-updating — when a new version ships, it offers an in-place update on next launch.

The `install.exe` is a self-contained PyInstaller binary (Python + GUI baked in, no external dependencies). For non-Windows or if you'd rather see what's happening, the same logic ships as [`install.py`](install/install.py) + [`install.bat`](install/install.bat) inside the bundle dir; run with Python 3.9+.

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
