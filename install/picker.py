"""GUI picker for the Filanex installer.

Two entry points:

    show_picker(additions)        -- standalone Tk window, used by the
                                     legacy --all / --no-gui CLI path
                                     and tests/picker_preview.py.

    PickerView(parent, additions) -- ttk.Frame embeddable inside the
                                     installer wizard's content area.

Layout: four side-by-side panes -- Slicer / Vendor / Product Line /
Material. Each pane is a CheckList: a scrollable list of rows where
each row has a real ttk.Checkbutton + name + count. Clicking a row's
name drills into it (drives the next column); clicking the checkbox
toggles selection. A "Select all" checkbutton at the top of each
pane toggles every row.
"""
from __future__ import annotations

import re
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk

from slicers.registry import SLICERS

# Legacy fallback only -- kept so picker can still parse bundles
# generated before the multi-vendor migration. Newer additions.json
# carries an explicit "vendor" field per entry; this map only fires
# when that field is missing.
LINE_TO_VENDOR = {
    "PolyLite": "Polymaker", "Polymaker": "Polymaker",
    "PolyTerra": "Polymaker", "Panchroma": "Polymaker",
    "Fiberon": "Polymaker", "PolyFlex": "Polymaker",
    "PolyMax": "Polymaker", "PolyCast": "Polymaker",
    "PolyDissolve": "Polymaker", "PolySonic": "Polymaker",
    "PolySmooth": "Polymaker", "PolyMide": "Polymaker",
}

NAME_WITH_MATERIAL = re.compile(r"^(\S+)\s+(.+?)\s+@.*$")
NAME_LINE_ONLY = re.compile(r"^(\S+)\s+@.*$")

PANE_DESCRIPTIONS = {
    "Slicer":       "Slicer apps to install into.",
    "Vendor":       "Filament brand owner.",
    "Product Line": "Brand's product family (PolyLite, PolyTerra, ...).",
    "Material":     "Filament chemistry within the line.",
}

# Per-row drill highlight (clicked-row indication). Light blue.
ACTIVE_ROW_BG = "#cfe7ff"
ACTIVE_ROW_FG = "#000000"
ROW_HOVER_BG = "#f0f0f0"


@dataclass
class Selection:
    cancelled: bool = False
    slicers: set[str] = field(default_factory=set)
    profile_keys: set[tuple[str, str, str]] = field(default_factory=set)

    def matches(self, vendor: str, line: str, material: str) -> bool:
        return (vendor, line, material) in self.profile_keys


def parse_entry_name(name: str, explicit_vendor: str | None = None) -> tuple[str, str, str] | None:
    m = NAME_WITH_MATERIAL.match(name)
    if m:
        line, material = m.group(1), m.group(2)
    else:
        m = NAME_LINE_ONLY.match(name)
        if not m:
            return None
        line = m.group(1)
        material = line
    # New bundles carry vendor explicitly; old bundles fall back to the
    # legacy line->vendor map.
    vendor = explicit_vendor or LINE_TO_VENDOR.get(line, "Other")
    return vendor, line, material


def build_tree(additions: list[dict]) -> dict:
    tree: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for entry in additions:
        # Newer bundles ship explicit vendor / line / material fields
        # per entry (and an is_base flag for chemistry roots). Older
        # bundles only have "name" -- fall back to regex parsing.
        if entry.get("is_base"):
            continue
        if "vendor" in entry and "line" in entry and "material" in entry:
            vendor = entry["vendor"]
            line = entry["line"]
            material = entry["material"]
        else:
            parsed = parse_entry_name(entry["name"], entry.get("vendor"))
            if parsed is None:
                continue
            vendor, line, material = parsed
            nm = entry["name"]
            if "@base" in nm or nm.endswith(" base"):
                continue
        tree[vendor][line][material] += 1
    return {
        v: {l: dict(mats) for l, mats in lines.items()}
        for v, lines in tree.items()
    }


# ---------------------------------------------------------------------------
# CheckList: scrollable list of (Checkbutton + name label + count label)
# rows. Real widgets per row, so the checkboxes are native ttk.Checkbutton
# instead of Treeview-cell glyphs.
# ---------------------------------------------------------------------------

class CheckList(ttk.Frame):
    """Vertical scrollable list of rows. Each row: real Checkbutton,
    name label, count label.

    Callbacks:
      on_check(key)  -- fired when the user toggles a row's checkbox
      on_drill(key)  -- fired when the user clicks a row's name

    Public methods:
      clear()                                      -- remove all rows
      add_row(key, name, count, disabled=False,
              checked=False)                       -- append a row
      set_checked(key, checked)                    -- programmatic toggle
      get_checked(key) -> bool
      set_active(key)                              -- highlight a row
                                                       (no event fired)
    """

    def __init__(self, parent, *, on_check=None, on_drill=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.on_check = on_check
        self.on_drill = on_drill

        # Outer border so each list reads as its own pane.
        border = tk.Frame(self, bg="#cccccc", bd=0)
        border.pack(fill="both", expand=True)
        inset = tk.Frame(border, bg="#ffffff", bd=0)
        inset.pack(fill="both", expand=True, padx=1, pady=1)

        # Scrollbar packed FIRST so it gets its rightmost slot before the
        # canvas's expand=True grabs the remaining width.
        self._vsb = ttk.Scrollbar(inset, orient="vertical")
        self._vsb.pack(side="right", fill="y")
        self._canvas = tk.Canvas(
            inset, highlightthickness=0, background="#ffffff", bd=0,
            # One scroll "unit" = one row's worth of pixels. Without
            # this the canvas defaults to 1px units and the wheel
            # handler's 1-unit-per-tick scrolls only a single pixel,
            # which feels broken even when it's technically working.
            yscrollincrement=24,
        )
        self._vsb.configure(command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner = tk.Frame(self._canvas, bg="#ffffff")
        self._inner_id = self._canvas.create_window(
            (0, 0), window=self._inner, anchor="nw",
        )
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._inner.bind("<Configure>", self._on_inner_configure)

        # Mouse-wheel scrolling. The earlier bind_all-on-Enter /
        # unbind-on-Leave pattern was flaky: crossing the cursor onto a
        # row (a child of the inner frame) fires <Leave> on the parent
        # canvas, which unbinds the wheel handler before the user can
        # scroll. We bind <MouseWheel> directly on the canvas + inner
        # frame here, and on every row widget at add_row time -- so
        # every interior widget routes wheel events to this canvas's
        # scroll handler, no Enter/Leave bookkeeping needed.
        for w in (self._canvas, self._inner):
            w.bind("<MouseWheel>", self._on_mousewheel)

        self._rows: list[dict] = []
        self._active_key: str | None = None
        self._enabled: bool = True   # global on/off; vendor/line/material
                                     # panes get this flipped to False
                                     # when no slicer is selected.

    # --- canvas plumbing --------------------------------------------------

    def _on_canvas_resize(self, ev):
        self._canvas.itemconfigure(self._inner_id, width=ev.width)
        # Canvas got taller -- if content now fits in the new viewport,
        # snap yview to top so we're not stuck showing whitespace.
        self._clamp_yview()

    def _on_inner_configure(self, _e):
        # Inner frame's content (its packed rows) changed size. Refresh
        # the canvas's scrollregion to match the new bbox so the
        # scrollbar reflects the right range, then clamp yview in case
        # the content shrank below the current view position.
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._clamp_yview()

    def _clamp_yview(self):
        """If the inner content fits entirely within the canvas
        viewport, force yview back to 0. Without this, the previous
        scroll position can leave the canvas showing whitespace above
        (or below) the content with no way to scroll back -- the bug
        Mike hit when wheeling a short list."""
        bbox = self._canvas.bbox("all")
        if bbox is None:
            return
        content_h = bbox[3] - bbox[1]
        canvas_h = self._canvas.winfo_height()
        if content_h <= canvas_h:
            self._canvas.yview_moveto(0)

    def _on_mousewheel(self, ev):
        # Suppress wheel scrolling when the content fits inside the
        # viewport -- otherwise tk's yview_scroll happily walks the
        # view past the content edge into empty space and won't snap
        # back, which is the "scrolled and now stuck" bug.
        bbox = self._canvas.bbox("all")
        if bbox is None:
            return
        content_h = bbox[3] - bbox[1]
        canvas_h = self._canvas.winfo_height()
        if content_h <= canvas_h:
            return
        # Windows wheel events have delta in ±120 units. Scroll three
        # rows per tick (yscrollincrement=24, so 3 units = 72px) to
        # match the typical Windows wheel-scroll feel.
        self._canvas.yview_scroll(int(-ev.delta / 120) * 3, "units")

    # --- row management ---------------------------------------------------

    def clear(self) -> None:
        for r in self._rows:
            r["frame"].destroy()
        self._rows.clear()
        self._active_key = None
        # Drilling into a different vendor / line shrinks the row set.
        # Reset to top so the new (possibly shorter) list isn't shown
        # scrolled off-screen carrying yview from the prior list.
        self._canvas.yview_moveto(0)

    def add_row(self, key: str, name: str, count: str = "",
                *, disabled: bool = False, checked: bool = False) -> None:
        var = tk.BooleanVar(value=checked)
        row = tk.Frame(self._inner, bg="#ffffff")
        # Plain tk.Checkbutton -- ttk.Checkbutton on Windows ignored
        # bg style overrides for its indicator area, leaving a gray
        # rectangle around the checkmark even when the row hovered or
        # was active. tk.Checkbutton honors bg directly.
        cb = tk.Checkbutton(
            row, variable=var,
            bg="#ffffff", activebackground="#ffffff",
            highlightthickness=0, bd=0,
            command=lambda k=key: self._fire_check(k),
        )
        if disabled:
            cb.configure(state="disabled", disabledforeground="#999")
        cb.pack(side="left", padx=(8, 6), pady=4)

        fg = "#999" if disabled else "#222"
        # Count packed FIRST on the right with a fixed width so the name
        # label's expand=True has a clear right-edge to butt up against.
        # Without this, long names + long counts collided and the count
        # got clipped at the row edge.
        count_lbl = tk.Label(
            row, text=count, fg=fg, bg="#ffffff", anchor="e",
            font=("Segoe UI", 10), width=6,
        )
        count_lbl.pack(side="right", padx=(4, 12))
        name_lbl = tk.Label(
            row, text=name, fg=fg, bg="#ffffff", anchor="w",
            font=("Segoe UI", 10),
        )
        name_lbl.pack(side="left", fill="x", expand=True)

        # Small bottom pady so the last row in a column isn't visually
        # crushed against the pane border.
        row.pack(fill="x", padx=0, pady=(0, 1))

        rec = {
            "key": key,
            "frame": row,
            "checkbutton": cb,
            "var": var,
            "name_label": name_lbl,
            "count_label": count_lbl,
            "disabled": disabled,
        }
        self._rows.append(rec)

        # Wheel routing: bind <MouseWheel> on every widget in the row
        # so hover-and-scroll over any child (the row frame, checkbox,
        # name, or count) routes through to THIS canvas's scroll
        # handler. Without this, hovering a row swallows the wheel
        # event because the row widget has no wheel binding of its own.
        for w in (row, cb, name_lbl, count_lbl):
            w.bind("<MouseWheel>", self._on_mousewheel)

        # Click anywhere on the row (other than the checkbox) drills.
        def on_click(_e, k=key):
            self._on_row_click(k)
        for w in (row, name_lbl, count_lbl):
            w.bind("<Button-1>", on_click)
        # Hover highlight (cosmetic). Skipped when the row is
        # permanent-disabled, currently active, or the whole list is
        # globally disabled (no slicer chosen yet).
        def on_enter(_e, r=rec):
            if (r["disabled"] or r["key"] == self._active_key
                    or not self._enabled):
                return
            self._set_row_bg(r, ROW_HOVER_BG)
        def on_leave(_e, r=rec):
            if (r["disabled"] or r["key"] == self._active_key
                    or not self._enabled):
                return
            self._set_row_bg(r, "#ffffff")
        for w in (row, name_lbl, count_lbl):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

    def _on_row_click(self, key: str) -> None:
        rec = self._find(key)
        if rec is None or rec["disabled"] or not self._enabled:
            return
        self.set_active(key)
        if self.on_drill:
            self.on_drill(key)

    def _fire_check(self, key: str) -> None:
        if not self._enabled:
            return
        if self.on_check:
            self.on_check(key)

    def _find(self, key: str) -> dict | None:
        for r in self._rows:
            if r["key"] == key:
                return r
        return None

    def _set_row_bg(self, rec: dict, bg: str, fg: str | None = None) -> None:
        rec["frame"].configure(bg=bg)
        rec["name_label"].configure(bg=bg, fg=fg or rec["name_label"].cget("fg"))
        rec["count_label"].configure(bg=bg, fg=fg or rec["count_label"].cget("fg"))
        # Checkbutton bg also follows the row -- tk.Checkbutton honors
        # the bg + activebackground options directly so this works.
        rec["checkbutton"].configure(bg=bg, activebackground=bg)

    # --- public API -------------------------------------------------------

    def set_checked(self, key: str, checked: bool) -> None:
        rec = self._find(key)
        if rec is not None:
            rec["var"].set(checked)

    def get_checked(self, key: str) -> bool:
        rec = self._find(key)
        return rec["var"].get() if rec is not None else False

    def set_active(self, key: str | None) -> None:
        self._active_key = key
        for r in self._rows:
            if r["disabled"]:
                continue
            if r["key"] == key:
                self._set_row_bg(r, ACTIVE_ROW_BG, ACTIVE_ROW_FG)
            else:
                self._set_row_bg(r, "#ffffff", "#222")

    def set_enabled(self, enabled: bool) -> None:
        """Globally enable / disable the whole list. When disabled,
        every row's checkbox goes to disabled state and the labels
        gray out -- the user can't interact until the list is
        re-enabled. Used to lock vendor/line/material panes until at
        least one slicer is chosen."""
        self._enabled = enabled
        for r in self._rows:
            if r["disabled"]:
                continue  # permanent-disabled stays as-is
            cb = r["checkbutton"]
            if enabled:
                cb.configure(state="normal", disabledforeground="#999")
                r["name_label"].configure(fg="#222")
                r["count_label"].configure(fg="#222")
            else:
                cb.configure(state="disabled", disabledforeground="#bbb")
                r["name_label"].configure(fg="#bbb")
                r["count_label"].configure(fg="#bbb")


# ---------------------------------------------------------------------------
# PickerView -- four CheckLists in a row + select-all + total label
# ---------------------------------------------------------------------------

class PickerView(ttk.Frame):
    def __init__(
        self, parent: tk.Widget, additions: list[dict],
        *, initial_prefs: dict | None = None, **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self.tree = build_tree(additions)
        # If we have remembered prefs from a previous run, use them to
        # decide what's pre-checked. Otherwise default everything to
        # selected (first-time-user behavior).
        #
        # Schema v2 (all_*_seen fields present): for each item now in
        #   the bundle, if it WAS seen at the prior save, respect the
        #   user's pick (checked iff in profile_keys / slicers); if it
        #   was NOT seen at the prior save, it's new since last run and
        #   defaults to CHECKED. This way a new vendor / line / material
        #   added to the bundle (or a brand-new slicer adapter) shows up
        #   pre-ticked instead of silently hidden behind the user's
        #   prior selection.
        #
        # Schema v1 (no all_*_seen): legacy prefs from the first
        #   release. Treat everything not in prefs as explicitly
        #   unchecked (preserves the old behavior on first launch
        #   after upgrade). On the next save, prefs upgrade to v2 and
        #   future launches get the new-item auto-check.
        prefs_keys: set[tuple[str, str, str]] | None = None
        prefs_slicers: set[str] | None = None
        seen_keys: set[tuple[str, str, str]] | None = None
        seen_slicers: set[str] | None = None
        if initial_prefs:
            try:
                prefs_keys = {
                    tuple(k) for k in initial_prefs.get("profile_keys", [])
                    if isinstance(k, (list, tuple)) and len(k) == 3
                }
                prefs_slicers = set(initial_prefs.get("slicers", []))
                if initial_prefs.get("schema_version", 1) >= 2:
                    seen_keys = {
                        tuple(k) for k in initial_prefs.get("all_keys_seen", [])
                        if isinstance(k, (list, tuple)) and len(k) == 3
                    }
                    seen_slicers = set(initial_prefs.get("all_slicers_seen", []))
            except (TypeError, ValueError):
                prefs_keys = prefs_slicers = None
                seen_keys = seen_slicers = None

        def _key_state(v, l, m):
            if prefs_keys is None:
                return True  # no prefs at all -- first run, default all
            if (v, l, m) in prefs_keys:
                return True  # explicitly selected before
            if seen_keys is not None and (v, l, m) not in seen_keys:
                return True  # new item; auto-check
            return False  # explicitly unchecked or v1 legacy
        self.selection_state = {
            v: {l: {m: _key_state(v, l, m) for m in mats}
                for l, mats in lines.items()}
            for v, lines in self.tree.items()
        }
        self.active_vendor = next(iter(sorted(self.tree)), None)
        self.active_line = (
            next(iter(sorted(self.tree[self.active_vendor])), None)
            if self.active_vendor else None
        )

        def _slicer_state(s):
            if not s["enabled"]:
                return False  # "coming soon" rows always off
            if prefs_slicers is None:
                return True  # first-run default-on
            if s["name"] in prefs_slicers:
                return True  # explicitly selected before
            if seen_slicers is not None and s["name"] not in seen_slicers:
                return True  # new slicer; auto-check
            return False
        self.slicer_vars: dict[str, tk.BooleanVar] = {
            s["name"]: tk.BooleanVar(value=_slicer_state(s)) for s in SLICERS
        }
        self.selection_var = tk.BooleanVar(value=True)
        self.total_var = tk.StringVar(value="...")

        # Select-all variables, one per pane.
        self.all_slicer_var = tk.BooleanVar()
        self.all_vendor_var = tk.BooleanVar()
        self.all_line_var = tk.BooleanVar()
        self.all_material_var = tk.BooleanVar()

        self._build()
        self._populate_slicers()
        self._populate_vendors()
        if self.active_vendor:
            self._populate_lines(self.active_vendor)
            self.vendor_list.set_active(self.active_vendor)
        if self.active_vendor and self.active_line:
            self._populate_materials(self.active_vendor, self.active_line)
            self.line_list.set_active(self.active_line)
        self._update_totals()
        self._sync_select_all_vars()
        self._refresh_pane_enabled_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_selection(self) -> Selection:
        slicers = {
            s["name"] for s in SLICERS
            if s["enabled"] and self.slicer_vars[s["name"]].get()
        }
        keys: set[tuple[str, str, str]] = set()
        for v, lines in self.selection_state.items():
            for l, mats in lines.items():
                for m, on in mats.items():
                    if on:
                        keys.add((v, l, m))
        return Selection(cancelled=False, slicers=slicers, profile_keys=keys)

    def is_valid_selection(self) -> bool:
        return self.selection_var.get()

    def to_prefs(self) -> dict:
        """Serialize the current selection for persistence -- the wizard
        feeds this to install.save_picker_prefs() after the user clicks
        Next on the picker page so the next run starts pre-ticked the
        same way.

        Emits schema v2: in addition to what the user *picked*, we
        record what was *available* to pick (all_keys_seen,
        all_slicers_seen). Future loads compare current bundle against
        all_*_seen to detect items added since the last save and
        default-check them, so a new vendor / line / material doesn't
        get silently hidden behind an old prefs file.
        """
        sel = self.get_selection()
        all_keys_seen = sorted(
            [v, l, m]
            for v, lines in self.tree.items()
            for l, mats in lines.items()
            for m in mats
        )
        all_slicers_seen = sorted(
            s["name"] for s in SLICERS if s["enabled"]
        )
        return {
            "schema_version": 2,
            "slicers": sorted(sel.slicers),
            "profile_keys": sorted(list(k) for k in sel.profile_keys),
            "all_slicers_seen": all_slicers_seen,
            "all_keys_seen": all_keys_seen,
        }

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build(self) -> None:
        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True)
        # 4 columns sized 2:1:2:2 of available width.
        # All four columns get equal weight. Vendor used to be weight=1
        # which made it ~165px on a 1200px window; "Polymaker" + count +
        # checkbox didn't fit and the name clipped to "Polyr". Even
        # weights give every column ~280px which is enough for the
        # widest vendor names.
        cols.columnconfigure(0, weight=1, uniform="picker_cols")
        cols.columnconfigure(1, weight=1, uniform="picker_cols")
        cols.columnconfigure(2, weight=1, uniform="picker_cols")
        cols.columnconfigure(3, weight=1, uniform="picker_cols")
        cols.rowconfigure(0, weight=1)

        slicer_pane, self.slicer_list = self._make_pane(
            cols, "Slicer", self.all_slicer_var, self._toggle_all_slicers,
            on_check=self._on_slicer_check, on_drill=None,
        )
        vendor_pane, self.vendor_list = self._make_pane(
            cols, "Vendor", self.all_vendor_var, self._toggle_all_vendors,
            on_check=self._on_vendor_check, on_drill=self._on_vendor_drill,
        )
        line_pane, self.line_list = self._make_pane(
            cols, "Product Line", self.all_line_var, self._toggle_all_lines,
            on_check=self._on_line_check, on_drill=self._on_line_drill,
        )
        material_pane, self.material_list = self._make_pane(
            cols, "Material", self.all_material_var, self._toggle_all_materials,
            on_check=self._on_material_check, on_drill=None,
        )
        # Track the panes that should disable when no slicer is chosen.
        # Slicer is the gating column -- the others depend on it.
        self._gated_panes = (
            (vendor_pane, self.vendor_list),
            (line_pane, self.line_list),
            (material_pane, self.material_list),
        )

        # Identical 14-px gap between every pair of panes.
        slicer_pane.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        vendor_pane.grid(row=0, column=1, sticky="nsew", padx=7)
        line_pane.grid(row=0, column=2, sticky="nsew", padx=7)
        material_pane.grid(row=0, column=3, sticky="nsew", padx=(7, 0))

        ttk.Label(
            self, textvariable=self.total_var, font=("Segoe UI", 10),
            padding=(2, 8, 0, 0),
        ).pack(anchor="w")

    def _make_pane(self, parent, title: str,
                    select_all_var, select_all_cmd,
                    on_check, on_drill) -> tuple[ttk.Frame, CheckList]:
        outer = ttk.Frame(parent, padding=(0, 8, 0, 0))
        ttk.Label(
            outer, text=title, font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 2), padx=(4, 0))
        ttk.Label(
            outer, text=PANE_DESCRIPTIONS.get(title, ""),
            foreground="#666",
        ).pack(anchor="w", pady=(0, 6), padx=(4, 0))
        # Stash the Select-all Checkbutton on the outer frame so
        # _refresh_pane_enabled_state can disable it together with the
        # CheckList beneath.
        sa = ttk.Checkbutton(
            outer, text="Select all",
            variable=select_all_var, command=select_all_cmd,
        )
        sa.pack(anchor="w", pady=(0, 6), padx=(4, 0))
        outer._select_all_btn = sa  # used by _refresh_pane_enabled_state

        cl = CheckList(outer, on_check=on_check, on_drill=on_drill)
        cl.pack(fill="both", expand=True)
        return outer, cl

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------
    def _grand_total(self) -> int:
        return sum(
            self.tree[v][l][m]
            for v, lines in self.tree.items()
            for l, mats in lines.items()
            for m in mats
        )

    def _populate_slicers(self) -> None:
        self.slicer_list.clear()
        total = self._grand_total()
        for s in SLICERS:
            label = (
                s["display"] if s["enabled"]
                else f"{s['display']} (coming soon)"
            )
            count_text = str(total) if s["enabled"] else "—"
            self.slicer_list.add_row(
                s["name"], label, count_text,
                disabled=not s["enabled"],
                checked=self.slicer_vars[s["name"]].get(),
            )

    def _populate_vendors(self) -> None:
        self.vendor_list.clear()
        for vendor in sorted(self.tree):
            count = sum(
                c for line in self.tree[vendor].values() for c in line.values()
            )
            self.vendor_list.add_row(
                vendor, vendor, str(count),
                checked=self._vendor_any_checked(vendor),
            )

    def _populate_lines(self, vendor: str) -> None:
        self.line_list.clear()
        for line in sorted(self.tree[vendor]):
            count = sum(self.tree[vendor][line].values())
            self.line_list.add_row(
                line, line, str(count),
                checked=self._line_any_checked(vendor, line),
            )

    # Vendor / Line checkbox state derives from "is ANY material under
    # me currently checked?" -- so checking one material auto-shows
    # the parent line / vendor as checked.
    def _vendor_any_checked(self, vendor: str) -> bool:
        return any(
            self.selection_state[vendor][l][m]
            for l, mats in self.tree[vendor].items() for m in mats
        )

    def _line_any_checked(self, vendor: str, line: str) -> bool:
        return any(self.selection_state[vendor][line].values())

    def _populate_materials(self, vendor: str, line: str) -> None:
        self.material_list.clear()
        for material in sorted(self.tree[vendor][line]):
            count = self.tree[vendor][line][material]
            self.material_list.add_row(
                material, material, str(count),
                checked=self.selection_state[vendor][line][material],
            )

    # ------------------------------------------------------------------
    # Per-row check handlers
    # ------------------------------------------------------------------
    def _on_slicer_check(self, key: str) -> None:
        self.slicer_vars[key].set(self.slicer_list.get_checked(key))
        self._maybe_cascade_clear_slicer()
        self._refresh_pane_enabled_state()
        self._sync_select_all_vars()
        self._update_totals()

    def _refresh_pane_enabled_state(self) -> None:
        """Vendor / Line / Material panes are gated on at least one
        slicer being selected. With no slicer the user has nothing to
        install into, so locking the data picker forces them to pick
        a target first."""
        any_slicer = any(v.get() for v in self.slicer_vars.values())
        for pane, checklist in self._gated_panes:
            checklist.set_enabled(any_slicer)
            sa = getattr(pane, "_select_all_btn", None)
            if sa is not None:
                if any_slicer:
                    sa.state(["!disabled"])
                else:
                    sa.state(["disabled"])

    def _maybe_cascade_clear_slicer(self) -> None:
        """If no slicers are currently selected, clear every material
        selection too -- 'no install target means no install set'.
        Vendor + Line displays bubble up automatically because they
        derive from material state."""
        if any(v.get() for v in self.slicer_vars.values()):
            return
        for v, lines in self.tree.items():
            for l, mats in lines.items():
                for m in mats:
                    self.selection_state[v][l][m] = False
        self._populate_vendors()
        if self.active_vendor:
            self._populate_lines(self.active_vendor)
            self.vendor_list.set_active(self.active_vendor)
            if self.active_line:
                self._populate_materials(self.active_vendor, self.active_line)
                self.line_list.set_active(self.active_line)

    def _on_vendor_check(self, key: str) -> None:
        # User wants to set vendor's any-checked state to whatever they
        # just clicked. Cascade DOWN: if going to True, check every
        # material under the vendor; if going to False, uncheck every
        # material under the vendor.
        new_state = self.vendor_list.get_checked(key)
        for l, mats in self.tree[key].items():
            for m in mats:
                self.selection_state[key][l][m] = new_state
        # Refresh dependent panes if active vendor was the one toggled.
        if key == self.active_vendor:
            self._populate_lines(key)
            self.vendor_list.set_active(key)
            if self.active_line:
                self._populate_materials(key, self.active_line)
                self.line_list.set_active(self.active_line)
        self._sync_select_all_vars()
        self._update_totals()

    def _on_vendor_drill(self, key: str) -> None:
        if key == self.active_vendor:
            return
        self.active_vendor = key
        self.active_line = next(iter(sorted(self.tree[key])), None)
        self._populate_lines(key)
        if self.active_line:
            self._populate_materials(key, self.active_line)
            self.line_list.set_active(self.active_line)
        else:
            self.material_list.clear()
        self._sync_select_all_vars()
        self._update_totals()

    def _on_line_check(self, key: str) -> None:
        if self.active_vendor is None:
            return
        # Cascade DOWN to all materials under this line.
        new_state = self.line_list.get_checked(key)
        for m in self.tree[self.active_vendor][key]:
            self.selection_state[self.active_vendor][key][m] = new_state
        if key == self.active_line:
            self._populate_materials(self.active_vendor, key)
            self.line_list.set_active(key)
        # Bubble UP to vendor.
        self.vendor_list.set_checked(
            self.active_vendor,
            self._vendor_any_checked(self.active_vendor),
        )
        self._sync_select_all_vars()
        self._update_totals()

    def _on_line_drill(self, key: str) -> None:
        if key == self.active_line:
            return
        self.active_line = key
        self._populate_materials(self.active_vendor, key)
        self._sync_select_all_vars()
        self._update_totals()

    def _on_material_check(self, key: str) -> None:
        if self.active_vendor is None or self.active_line is None:
            return
        on = self.material_list.get_checked(key)
        self.selection_state[self.active_vendor][self.active_line][key] = on
        # Bubble UP to line + vendor (any-child-checked semantic).
        self.line_list.set_checked(
            self.active_line,
            self._line_any_checked(self.active_vendor, self.active_line),
        )
        self.vendor_list.set_checked(
            self.active_vendor,
            self._vendor_any_checked(self.active_vendor),
        )
        self._sync_select_all_vars()
        self._update_totals()

    # ------------------------------------------------------------------
    # Select-all toggles
    # ------------------------------------------------------------------
    def _toggle_all_slicers(self) -> None:
        on = self.all_slicer_var.get()
        for s in SLICERS:
            if not s["enabled"]:
                continue
            self.slicer_vars[s["name"]].set(on)
            self.slicer_list.set_checked(s["name"], on)
        # Same cascade as a single slicer toggle: deselecting all slicers
        # clears the rest of the picker.
        self._maybe_cascade_clear_slicer()
        self._refresh_pane_enabled_state()
        self._sync_select_all_vars()
        self._update_totals()

    def _toggle_all_vendors(self) -> None:
        # Cascades through vendor click semantics: set every vendor's
        # any-checked state to the new "all" state, which propagates
        # to all materials under all vendors.
        on = self.all_vendor_var.get()
        for v, lines in self.tree.items():
            for l, mats in lines.items():
                for m in mats:
                    self.selection_state[v][l][m] = on
            self.vendor_list.set_checked(v, on)
        if self.active_vendor:
            self._populate_lines(self.active_vendor)
            self.vendor_list.set_active(self.active_vendor)
            if self.active_line:
                self._populate_materials(self.active_vendor, self.active_line)
                self.line_list.set_active(self.active_line)
        self._sync_select_all_vars()
        self._update_totals()

    def _toggle_all_lines(self) -> None:
        if self.active_vendor is None:
            return
        # Cascades to materials under each line in active vendor.
        on = self.all_line_var.get()
        for l, mats in self.tree[self.active_vendor].items():
            for m in mats:
                self.selection_state[self.active_vendor][l][m] = on
        self._populate_lines(self.active_vendor)
        if self.active_line:
            self.line_list.set_active(self.active_line)
            self._populate_materials(self.active_vendor, self.active_line)
        self.vendor_list.set_checked(
            self.active_vendor,
            self._vendor_any_checked(self.active_vendor),
        )
        self._sync_select_all_vars()
        self._update_totals()

    def _toggle_all_materials(self) -> None:
        if self.active_vendor is None or self.active_line is None:
            return
        on = self.all_material_var.get()
        for m in self.tree[self.active_vendor][self.active_line]:
            self.selection_state[self.active_vendor][self.active_line][m] = on
            self.material_list.set_checked(m, on)
        # Bubble up.
        self.line_list.set_checked(
            self.active_line,
            self._line_any_checked(self.active_vendor, self.active_line),
        )
        self.vendor_list.set_checked(
            self.active_vendor,
            self._vendor_any_checked(self.active_vendor),
        )
        self._sync_select_all_vars()
        self._update_totals()

    # ------------------------------------------------------------------
    # Totals + select-all sync
    # ------------------------------------------------------------------
    def _update_totals(self) -> None:
        selected = sum(
            self.tree[v][l][m]
            for v, lines in self.selection_state.items()
            for l, mats in lines.items()
            for m, on in mats.items() if on
        )
        total = self._grand_total()
        slicer_count = sum(1 for v in self.slicer_vars.values() if v.get())
        slicer_word = "slicer" if slicer_count == 1 else "slicers"
        self.total_var.set(
            f"Selected {selected} of {total} profiles "
            f"-> {slicer_count} {slicer_word} = "
            f"{selected * slicer_count} install operation(s)"
        )
        self.selection_var.set(bool(slicer_count) and bool(selected))

    def _sync_select_all_vars(self) -> None:
        # Each Select-all derives from ITS OWN column's row visual
        # state (vendor + line use the any-checked semantic).
        enabled_slicers = [s for s in SLICERS if s["enabled"]]
        self.all_slicer_var.set(
            bool(enabled_slicers) and all(
                self.slicer_vars[s["name"]].get() for s in enabled_slicers
            )
        )
        self.all_vendor_var.set(
            bool(self.tree) and
            all(self._vendor_any_checked(v) for v in self.tree)
        )
        if self.active_vendor:
            self.all_line_var.set(
                bool(self.tree[self.active_vendor]) and
                all(
                    self._line_any_checked(self.active_vendor, l)
                    for l in self.tree[self.active_vendor]
                )
            )
        if self.active_vendor and self.active_line:
            self.all_material_var.set(all(
                self.selection_state[self.active_vendor][self.active_line].values()
            ))


# ---------------------------------------------------------------------------
# Standalone window wrapper (legacy entrypoint)
# ---------------------------------------------------------------------------

def show_picker(additions: list[dict]) -> Selection:
    root = tk.Tk()
    root.title("Filanex installer -- pick what to install")
    w, h = 1180, 620
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
    root.minsize(900, 480)

    header = ttk.Frame(root, padding=(12, 8))
    header.pack(fill="x")
    ttk.Label(
        header,
        text="Pick the slicers / vendors / product lines / materials "
             "you want installed.",
        font=("Segoe UI", 11),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="Click a vendor or product-line row to drill down. Tick "
             "checkboxes to choose. Use Select all to toggle every row in "
             "a column.",
        foreground="#666",
    ).pack(anchor="w")

    view = PickerView(root, additions)
    view.pack(fill="both", expand=True, padx=12)

    footer = ttk.Frame(root, padding=(12, 8))
    footer.pack(fill="x")
    result: dict = {"sel": Selection(cancelled=True)}

    def on_install():
        sel = view.get_selection()
        if not sel.slicers:
            messagebox.showerror(
                "No slicer selected",
                "Pick at least one slicer to install to.", parent=root,
            )
            return
        if not sel.profile_keys:
            messagebox.showerror(
                "Nothing selected",
                "Pick at least one material to install.", parent=root,
            )
            return
        result["sel"] = sel
        root.destroy()

    ttk.Button(footer, text="Cancel",
               command=root.destroy).pack(side="right")
    ttk.Button(footer, text="Install",
               command=on_install).pack(side="right", padx=(0, 6))

    root.lift()
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()
    return result["sel"]


def select_all(additions: list[dict]) -> Selection:
    keys: set[tuple[str, str, str]] = set()
    for entry in additions:
        parsed = parse_entry_name(entry["name"])
        if parsed is None:
            continue
        vendor, line, material = parsed
        nm = entry["name"]
        if "@base" in nm or "@Polymaker base" in nm:
            continue
        keys.add((vendor, line, material))
    slicers = {s["name"] for s in SLICERS if s["enabled"]}
    return Selection(cancelled=False, slicers=slicers, profile_keys=keys)
