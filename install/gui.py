"""Step-through wizard GUI for the installer.

User flow when install.exe is double-clicked:

    1. Welcome page          -- intro, [Next >]
    2. Action page           -- radio buttons (Install/Update/Uninstall/
                                Status), [< Back] [Next >]
    3. Pre-flight page       -- "make sure Bambu Studio is closed",
                                live process check + Re-check button
                                [< Back] [Next >] (Next disabled while
                                Bambu is running)
    4. Picker page (skip for Uninstall / Status)
                             -- the embedded PickerView
                                [< Back] [Next >] (Next disabled until
                                a valid selection)
    5. Confirm page          -- "About to <verb> N profiles. Proceed?"
                                [< Back] [Install / Update / Uninstall]
    6. Progress page         -- live log streaming the existing
                                cmd_install / cmd_update / cmd_uninstall
                                output. Cancel disabled while running;
                                [Re-launch Bambu Studio] [Finish]
                                appear on completion.

For the Status action: from page 2 we jump straight to a status-text
page (one screen), [< Back] [Finish].

CLI subcommands (install/upgrade/uninstall/update/status with args)
still bypass the wizard and run as before; this module is only
invoked when no subcommand was passed.
"""
from __future__ import annotations

import io
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import messagebox, ttk

from picker import PickerView, parse_entry_name


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(install_module, system_dir: Path, args) -> int:
    """Open the wizard. Returns the exit code (0 on success / cancel)."""
    app = Wizard(install_module, system_dir, args)
    app.mainloop()
    return app.exit_code


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class Wizard(tk.Tk):
    def __init__(self, install_mod, system_dir: Path, args):
        super().__init__()
        self.install_mod = install_mod
        self.system_dir = system_dir
        self.args = args
        self.exit_code = 0

        # Wizard state
        self.action: str = "install"   # 'install' / 'update' / 'uninstall' / 'status'
        self.selection = None          # picker.Selection (set after picker step)
        self.exe_path: Path | None = None
        self.tracking = install_mod.load_tracking(system_dir)
        # Auto-fetched bundle dir (set on success of the picker-step
        # fetch attempt). When set, it overrides the bundled
        # additions.json + BBL/filament/ paths everywhere downstream so
        # newly-added vendors / lines / materials appear in the picker
        # without needing a fresh installer download.
        self.fetched_bundle_dir: Path | None = None
        self._fetch_attempted: bool = False
        # Cached result of the on-startup installer-update probe. Sentinel
        # of False means "not run yet"; None means "ran, no update";
        # str means "ran, this version is available".
        self._update_check_result: object = False

        # Step list -- dynamically rebuilt when the action changes
        self.steps: list[str] = []
        self.step_idx = 0

        self.title("Filanex — filament profile installer")
        self._apply_window_icon()
        # Wide enough that the picker fits without horizontal scroll on
        # default content; height accommodates picker + header + nav.
        w, h = 1200, 720
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
        self.minsize(900, 540)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build_chrome()
        self._goto_step("welcome")

        # Foreground briefly so users notice the new window.
        self.lift()
        self.attributes("-topmost", True)
        self.after(700, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _read_local_db_version(self) -> str | None:
        """Return the database_version of the bundle currently sitting
        next to install.exe, or None if it can't be read. Used to
        compare local vs remote profile-data versions on the update
        screen.

        For a frozen PyInstaller .exe, additions.json lives next to
        the .exe binary (NOT in sys._MEIPASS, which is only the temp
        extraction dir for bundled payload). Search the install_mod's
        HERE constant (which already does this lookup correctly) plus
        a few common bundle-zip layouts.
        """
        try:
            here = getattr(self.install_mod, "HERE", None)
            search_roots = []
            if here is not None:
                search_roots.append(Path(here))
            # Source-tree fallback (running install.py directly)
            search_roots.append(Path(__file__).resolve().parent)
            # Last-ditch: check what we tracked from the prior install
            tracked = self.tracking.get("bundle_database_version") if self.tracking else None
            for root in search_roots:
                for cand in (root / "additions.json",
                             root / "BBL-injection" / "additions.json",
                             root.parent / "additions.json"):
                    if cand.is_file():
                        return json.loads(
                            cand.read_text(encoding="utf-8")
                        ).get("database_version")
            # Nothing on disk -- fall back to what we previously installed
            return tracked
        except Exception:
            return None

    def _apply_window_icon(self) -> None:
        """Set the Tk window icon so the taskbar shows the Filanex mark
        (the Windows .exe itself uses the icon baked in by PyInstaller's
        --icon flag; this affects the live Tk window icon)."""
        try:
            # In PyInstaller's onefile mode, sys._MEIPASS points to the
            # extracted bundle dir; outside frozen mode, fall back to
            # the source tree.
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
            for cand in (base / "filanex-app-icon.ico",
                         base / "branding" / "04-app-icon" / "filanex-app-icon.ico"):
                if cand.exists():
                    self.iconbitmap(default=str(cand))
                    return
            # PNG fallback for environments where .ico isn't honored
            for cand in (base / "filanex-app-icon-256.png",
                         base / "branding" / "04-app-icon" / "filanex-app-icon-256.png"):
                if cand.exists():
                    img = tk.PhotoImage(file=str(cand))
                    self._icon_img = img  # keep a ref so Tk doesn't GC it
                    self.iconphoto(True, img)
                    return
        except Exception:
            # Icon is cosmetic; never let it block the wizard.
            pass

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------
    def _build_chrome(self) -> None:
        title_bar = ttk.Frame(self, padding=(24, 16, 24, 6))
        title_bar.pack(fill="x")
        self.title_var = tk.StringVar(value="Welcome")
        ttk.Label(
            title_bar, textvariable=self.title_var,
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        self.subtitle_var = tk.StringVar(value="")
        ttk.Label(
            title_bar, textvariable=self.subtitle_var, foreground="#666",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # Content frame -- each step builds into here.
        self.content = ttk.Frame(self, padding=(24, 16))
        self.content.pack(fill="both", expand=True)

        ttk.Separator(self, orient="horizontal").pack(fill="x")
        nav = ttk.Frame(self, padding=(24, 12))
        nav.pack(fill="x")

        self.back_btn = ttk.Button(nav, text="< Back", command=self._back, width=12)
        self.cancel_btn = ttk.Button(nav, text="Cancel", command=self._cancel, width=12)
        self.next_btn = ttk.Button(nav, text="Next >", command=self._next, width=12)

        # Layout: Back is bottom-LEFT; Cancel is bottom-RIGHT (rightmost);
        # Next sits just to Cancel's left.
        self.back_btn.pack(side="left")
        self.cancel_btn.pack(side="right")
        self.next_btn.pack(side="right", padx=(0, 6))

    def _set_buttons(self, *, back: bool, next_text: str, next_enabled: bool,
                     cancel_text: str = "Cancel"):
        # Restore the standard wiring on every step. _step_progress
        # overrides next_btn.command to self._finish for the duration of
        # the progress page; without resetting it here, going back to
        # any other step would leave Next still pointing at _finish, so
        # clicking it would just close the wizard (the bug Mike hit).
        self.cancel_btn.configure(
            text=cancel_text, command=self._cancel, state="normal",
        )
        self.next_btn.configure(
            text=next_text,
            state="normal" if next_enabled else "disabled",
            command=self._next,
        )
        self.back_btn.configure(command=self._back)
        if back:
            self.back_btn.state(["!disabled"])
            self.back_btn.pack(side="left")
        else:
            self.back_btn.pack_forget()

    def _clear_content(self) -> None:
        for w in self.content.winfo_children():
            w.destroy()

    # ------------------------------------------------------------------
    # Step pipeline
    # ------------------------------------------------------------------
    def _pipeline(self) -> list[str]:
        """Step list for the currently-selected action. Every action
        passes through `update_check` (right after welcome) so the
        wizard can offer a self-update of install.exe before the user
        commits to a path."""
        if self.action == "status":
            return ["welcome", "update_check", "action", "status"]
        if self.action == "uninstall":
            return ["welcome", "update_check", "action", "preflight",
                    "confirm", "progress"]
        # install / update
        return ["welcome", "update_check", "action", "preflight",
                "picker", "confirm", "progress"]

    def _goto_step(self, name: str) -> None:
        self.steps = self._pipeline()
        if name not in self.steps:
            self.steps.append(name)
        self.step_idx = self.steps.index(name)
        self.current_step = name
        self._clear_content()
        getattr(self, f"_step_{name}")()

    def _next(self) -> None:
        # Each step's own _next_<step> hook decides where to go.
        method = getattr(self, f"_next_from_{self.current_step}", None)
        if method:
            method()
        else:
            # default: advance one step
            self.step_idx = min(self.step_idx + 1, len(self.steps) - 1)
            self._goto_step(self.steps[self.step_idx])

    def _back(self) -> None:
        if self.step_idx > 0:
            self.step_idx -= 1
            self._goto_step(self.steps[self.step_idx])

    def _cancel(self) -> None:
        self.exit_code = 1
        self.destroy()

    # ==================================================================
    # Step: welcome
    # ==================================================================
    def _step_welcome(self) -> None:
        self.title_var.set("Welcome")
        installer_v = getattr(self.install_mod, "INSTALLER_VERSION", "?")
        if getattr(self.args, "post_update", False):
            self.subtitle_var.set(
                f"Updated to installer v{installer_v} -- click Next to continue."
            )
        else:
            self.subtitle_var.set(
                f"Filanex filament profiles for Bambu Studio "
                f"(installer v{installer_v})"
            )

        ttk.Label(
            self.content,
            text="This wizard installs Filanex filament profiles into\n"
                 "Bambu Studio so they appear in the filament library.",
            font=("Segoe UI", 11), justify="left",
        ).pack(anchor="w", pady=(8, 14))

        if self.tracking is None:
            status = "Filanex filament profiles are not currently installed."
        else:
            ver = self.tracking.get("bundle_database_version") or "?"
            when = (self.tracking.get("installed_at") or "?")[:10]
            n = len(self.tracking.get("filament_files", []))
            status = (
                f"Filanex filament profiles are installed.\n"
                f"   Version {ver}, {n} profiles, installed {when}."
            )
        ttk.Label(self.content, text=status, foreground="#444").pack(
            anchor="w", pady=(0, 14)
        )

        ttk.Label(
            self.content,
            text=f"Target system folder:\n   {self.system_dir}",
            foreground="#666", justify="left",
        ).pack(anchor="w", pady=(0, 14))

        ttk.Label(
            self.content,
            text="Click Next to continue.",
            font=("Segoe UI", 10, "italic"), foreground="#666",
        ).pack(anchor="w", pady=(20, 0))

        self._set_buttons(back=False, next_text="Next >", next_enabled=True)

    def _next_from_welcome(self) -> None:
        self._goto_step("update_check")

    # ==================================================================
    # Step: update_check -- self-update of install.exe
    # ==================================================================
    def _step_update_check(self) -> None:
        self.title_var.set("Check for updates")
        self.subtitle_var.set("Looking for a newer installer online...")

        # Skip the network check entirely if we just self-updated this
        # session (we already know we're current) or if the user passed
        # --skip-update-check on the CLI.
        if (getattr(self.args, "post_update", False)
                or getattr(self.args, "skip_update_check", False)):
            self._set_buttons(back=True, next_text="Next >", next_enabled=True)
            ttk.Label(
                self.content,
                text="Update check skipped.",
                font=("Segoe UI", 11),
            ).pack(anchor="w", pady=(40, 8))
            return

        # Not running as a frozen .exe? Self-update only works for the
        # PyInstaller .exe build (we can rename a running .exe; the
        # .py-from-source path can update itself with `git pull`).
        if not getattr(sys, "frozen", False):
            self._set_buttons(back=True, next_text="Next >", next_enabled=True)
            ttk.Label(
                self.content,
                text="Self-update is only available for the install.exe build.\n"
                     "Pull the latest from git for source installs.",
                foreground="#666", justify="left",
            ).pack(anchor="w", pady=(40, 8))
            return

        # If we already ran the check this session (back-navigation or
        # repeat visit), render the cached result immediately rather
        # than re-hitting the network.
        if self._update_check_result is not False:
            self._on_update_check_done(self._update_check_result)
            return

        # Async check: show spinner, kick off worker, render result on
        # completion via after().
        self._set_buttons(back=True, next_text="Next >", next_enabled=False)
        ttk.Label(
            self.content,
            text="Checking the project's GitHub for a newer install.exe...",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(40, 8))
        ttk.Label(
            self.content,
            text="This is a quick HTTP request -- a few hundred bytes.",
            foreground="#666",
        ).pack(anchor="w")
        pb = ttk.Progressbar(self.content, mode="indeterminate", length=320)
        pb.pack(anchor="w", pady=(16, 0))
        pb.start(10)
        threading.Thread(
            target=self._update_check_worker, daemon=True,
        ).start()

    def _update_check_worker(self) -> None:
        try:
            remote = self.install_mod.check_for_installer_update()
        except Exception:
            remote = None
        self._update_check_result = remote
        self.after(0, lambda: self._on_update_check_done(remote))

    @staticmethod
    def _fmt_version_diff(local: str | None, remote: str | None) -> str:
        """Return either 'v<X>' (current) or 'previous v<X> ==> new v<Y>'
        (update). Hides None values gracefully."""
        if local is None:
            return f"v{remote}" if remote else "(unknown)"
        if remote is None or remote == local:
            return f"v{local}"
        return f"previous v{local}  ==>  new v{remote}"

    @staticmethod
    def _fmt_count_diff(local: int | None, remote: int | None) -> str:
        if local is None:
            return f"{remote:,}" if remote is not None else "(unknown)"
        if remote is None or remote == local:
            return f"{local:,}"
        diff = remote - local
        sign = "+" if diff > 0 else ""
        return f"previous {local:,}  ==>  new {remote:,}  ({sign}{diff:,})"

    def _local_profile_count(self) -> int | None:
        """Profiles currently tracked as installed by us."""
        if not self.tracking:
            return None
        n = len(self.tracking.get("filament_list_entries", []))
        return n if n else None

    def _on_update_check_done(self, remote_version: str | None) -> None:
        self._clear_content()
        installer_v = getattr(self.install_mod, "INSTALLER_VERSION", "?")
        # Always probe the remote bundle's database_version + addition
        # count too so we can show profile-data drift even when the
        # .exe itself is current.
        remote_versions = self.install_mod.check_remote_versions() or {}
        remote_db = remote_versions.get("database_version")
        remote_count = remote_versions.get("addition_count")
        local_db = self._read_local_db_version()
        local_count = self._local_profile_count()

        installer_changed = remote_version is not None
        db_changed = (remote_db is not None and local_db is not None
                       and remote_db != local_db)
        count_changed = (remote_count is not None and local_count is not None
                          and remote_count != local_count)

        # Title + subtitle reflect the actual state.
        if installer_changed and db_changed:
            self.title_var.set("Updates available")
            self.subtitle_var.set(
                f"Newer installer AND newer profile data on github."
            )
        elif installer_changed:
            self.title_var.set("Update available")
            self.subtitle_var.set(
                f"A newer install.exe is available."
            )
        elif db_changed:
            self.title_var.set("Profile data update available")
            self.subtitle_var.set(
                f"Installer is current but newer filament profiles "
                f"are on github."
            )
        else:
            self.title_var.set("You're on the latest version")
            self.subtitle_var.set(
                f"Installer v{installer_v} is current. Click Next to continue."
            )

        # Body: three uniform "previous X ==> new Y" / "v<X>" lines.
        body_lines = [
            f"   Installer:      "
            f"{self._fmt_version_diff(installer_v, remote_version)}",
            f"   Profile data:   "
            f"{self._fmt_version_diff(local_db, remote_db)}",
            f"   Profiles:       "
            f"{self._fmt_count_diff(local_count, remote_count)}",
        ]
        ttk.Label(
            self.content,
            text="\n".join(body_lines),
            font=("Segoe UI", 11), justify="left",
        ).pack(anchor="w", pady=(20, 12))

        if not installer_changed:
            # Either fully current OR only profile data drift -- both
            # resolve via clicking Next + running Install/Update.
            if db_changed or count_changed:
                ttk.Label(
                    self.content,
                    text="The newer profile data will download "
                         "automatically when you click Install or Update on "
                         "the next screens.",
                    foreground="#444", justify="left", wraplength=540,
                ).pack(anchor="w", pady=(0, 12))
            self._set_buttons(back=True, next_text="Next >", next_enabled=True)
            return

        # Installer self-update available -- offer Update Now button.
        ttk.Label(
            self.content,
            text="If you update, the installer will download the new "
                 ".exe, swap it in, close this window, and re-open the "
                 "wizard with the new version. No further clicks needed "
                 "-- you'll land back at a Welcome screen confirming "
                 "the update so you can continue.",
            foreground="#444", justify="left", wraplength=540,
        ).pack(anchor="w", pady=(0, 12))

        btn_row = ttk.Frame(self.content)
        btn_row.pack(anchor="w", pady=(8, 0))
        ttk.Button(
            btn_row, text=f"Update Now to v{remote_version}",
            command=lambda: self._do_self_update(remote_version),
        ).pack(side="left")

        self._set_buttons(back=True, next_text="Skip update",
                          next_enabled=True)

    def _do_self_update(self, remote_version: str) -> None:
        # Replace the page content with a "downloading" spinner, lock
        # all the nav buttons, and run the download + swap in a worker.
        self._clear_content()
        self.title_var.set("Updating installer")
        self.subtitle_var.set(
            f"Downloading install.exe v{remote_version} ..."
        )
        ttk.Label(
            self.content,
            text="Please wait. The installer will close and re-launch\n"
                 "automatically when the new version is ready.",
            font=("Segoe UI", 11), justify="left",
        ).pack(anchor="w", pady=(40, 8))
        pb = ttk.Progressbar(self.content, mode="indeterminate", length=320)
        pb.pack(anchor="w", pady=(16, 0))
        pb.start(10)

        # Lock the nav -- "no user input for this step" per Mike.
        self.cancel_btn.state(["disabled"])
        self.back_btn.pack_forget()
        self.next_btn.configure(state="disabled")

        threading.Thread(
            target=self._self_update_worker,
            args=(remote_version,), daemon=True,
        ).start()

    def _self_update_worker(self, remote_version: str) -> None:
        err: str | None = None
        try:
            self.install_mod.perform_self_update(remote_version)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        self.after(0, lambda: self._on_self_update_done(err))

    def _on_self_update_done(self, err: str | None) -> None:
        if err is not None:
            messagebox.showerror(
                "Update failed",
                f"Couldn't self-update the installer.\n\n{err}\n\n"
                f"You can continue with the current version, or close "
                f"the wizard and re-download the bundle manually.",
                parent=self,
            )
            # Bounce back to the action page so the user can continue
            # with the current build.
            self.action = self.action  # no-op
            self._goto_step("action")
            return
        # Success -- the new .exe is already starting. Close ourselves
        # so the disk file is fully released.
        self.exit_code = 0
        self.destroy()

    def _next_from_update_check(self) -> None:
        # User clicked Next on the update_check page (either "you're
        # current" or "skip update"). Move on to the action picker.
        self._goto_step("action")

    # ==================================================================
    # Step: action picker (radio buttons)
    # ==================================================================
    def _step_action(self) -> None:
        self.title_var.set("Choose an action")
        self.subtitle_var.set("Step 1 -- what do you want to do?")

        self.action_var = tk.StringVar(value=self.action)

        options = [
            ("install",   "Install / Upgrade",
             "Install Filanex filament profiles, or update them if already installed."),
            ("update",    "Check for updates online",
             "Fetch the latest profiles from GitHub and apply them."),
            ("uninstall", "Uninstall",
             "Remove every profile this installer added. Custom user profiles "
             "are preserved."),
            ("status",    "Show current install status",
             "View what's currently tracked. No changes are made."),
        ]

        for value, label, hint in options:
            row = ttk.Frame(self.content)
            row.pack(anchor="w", fill="x", pady=4)
            disabled = (
                value in ("update", "uninstall") and self.tracking is None
            )
            rb = ttk.Radiobutton(
                row, text=label, value=value, variable=self.action_var,
            )
            if disabled:
                rb.state(["disabled"])
            rb.pack(anchor="w")
            ttk.Label(
                row, text=hint, foreground="#888", wraplength=900,
            ).pack(anchor="w", padx=(24, 0))

        self._set_buttons(back=True, next_text="Next >", next_enabled=True)

    def _next_from_action(self) -> None:
        self.action = self.action_var.get()
        # Pipeline depends on action; rebuild it then advance.
        self.steps = self._pipeline()
        # After "action" we either go to preflight or status
        next_step = self.steps[self.steps.index("action") + 1]
        self._goto_step(next_step)

    # ==================================================================
    # Step: pre-flight (Bambu running check)
    # ==================================================================
    def _step_preflight(self) -> None:
        self.title_var.set("Make sure Bambu Studio is closed")
        self.subtitle_var.set(
            "Step 2 -- the installer needs Bambu Studio's profile files free."
        )

        ttk.Label(
            self.content,
            text="Modifying profiles while Bambu Studio is open can\n"
                 "cause it to overwrite our changes when it next saves.\n"
                 "Save your work and close Bambu Studio before continuing.",
            font=("Segoe UI", 10), justify="left",
        ).pack(anchor="w", pady=(8, 16))

        self.preflight_status = tk.StringVar(value="Checking...")
        ttk.Label(
            self.content, textvariable=self.preflight_status,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        ttk.Button(
            self.content, text="Re-check now",
            command=self._refresh_preflight,
        ).pack(anchor="w")

        self._set_buttons(back=True, next_text="Next >", next_enabled=False)
        self._refresh_preflight()

    def _refresh_preflight(self) -> None:
        running, exe_path = self.install_mod.find_bambu_process()
        if running:
            self.preflight_status.set(
                "Bambu Studio is RUNNING.\n"
                f"   ({exe_path or 'process detected'})\n"
                "Close it, then click Re-check."
            )
            self.next_btn.configure(state="disabled")
            if exe_path and self.exe_path is None:
                self.exe_path = exe_path
        else:
            self.preflight_status.set(
                "Bambu Studio is NOT running. Ready to proceed."
            )
            self.next_btn.configure(state="normal")

    def _next_from_preflight(self) -> None:
        # Re-check at the moment of advance so we don't proceed if
        # Bambu was relaunched between checks.
        running, exe_path = self.install_mod.find_bambu_process()
        if running:
            self._refresh_preflight()
            return
        # Branch by action
        if self.action == "uninstall":
            self._goto_step("confirm")
        else:
            self._goto_step("picker")

    # ==================================================================
    # Bundle path resolution -- picks the auto-fetched dir if we got one,
    # otherwise the bundled-with-the-exe paths.
    # ==================================================================
    def _resolve_bundle_paths(self) -> tuple[Path, Path]:
        if self.fetched_bundle_dir is not None:
            return (
                self.fetched_bundle_dir / "additions.json",
                self.fetched_bundle_dir / "BBL" / "filament",
            )
        return (
            self.install_mod.HERE / "additions.json",
            self.install_mod.HERE / "BBL" / "filament",
        )

    # ==================================================================
    # Step: picker
    # ==================================================================
    def _step_picker(self) -> None:
        if self.action == "update":
            self.title_var.set("Pick what to update")
        else:
            self.title_var.set("Pick what to install")

        if not self._fetch_attempted:
            # First time hitting the picker this session -- try to grab
            # the latest bundle from DISTRIBUTION_BASE_URL so any new
            # vendors / lines / materials added since this .exe was
            # built show up in the picker.
            self.subtitle_var.set("Checking for updated profile data online...")
            self._show_picker_loading()
            self._set_buttons(back=True, next_text="Next >", next_enabled=False)
            threading.Thread(
                target=self._fetch_bundle_worker, daemon=True
            ).start()
            return

        self.subtitle_var.set("Step 3 -- choose slicers, vendors, product lines, materials")
        self._build_picker_ui()

    def _show_picker_loading(self) -> None:
        ttk.Label(
            self.content,
            text="Checking online for updated profile data...",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(40, 8))
        ttk.Label(
            self.content,
            text="If a newer version of the profile database is available\n"
                 "online, it will be downloaded so that the picker shows\n"
                 "the latest vendors, lines, and materials.\n\n"
                 "If you're offline or the server is unreachable, the\n"
                 "installer will fall back to the data bundled with this\n"
                 "installer .exe.",
            foreground="#666", justify="left",
        ).pack(anchor="w", pady=(0, 12))
        pb = ttk.Progressbar(self.content, mode="indeterminate", length=320)
        pb.pack(anchor="w", pady=(8, 0))
        pb.start(10)
        self._loading_pb = pb

    def _fetch_bundle_worker(self) -> None:
        local_additions = self.install_mod.HERE / "additions.json"
        try:
            fetched = self.install_mod._try_auto_fetch_bundle(local_additions)
        except Exception:
            fetched = None
        # Hand result back to the UI thread.
        self.after(0, lambda: self._on_fetch_done(fetched))

    def _on_fetch_done(self, fetched: Path | None) -> None:
        self._fetch_attempted = True
        if fetched is not None:
            self.fetched_bundle_dir = fetched
        # Re-enter the picker step now that the fetch is settled. This
        # also rebuilds the content frame, which clears the loading UI.
        self._goto_step("picker")

    def _build_picker_ui(self) -> None:
        additions_path, _ = self._resolve_bundle_paths()
        try:
            additions = json.loads(
                additions_path.read_text(encoding="utf-8")
            )["filament_list_additions"]
        except FileNotFoundError:
            messagebox.showerror(
                "Missing bundle",
                f"Couldn't find additions.json.\n"
                f"Expected: {additions_path}",
                parent=self,
            )
            self._goto_step("action")
            return

        # Load remembered picks from the prior run (if any). First-time
        # users get None back, which makes PickerView default everything
        # to selected.
        initial_prefs = self.install_mod.load_picker_prefs()
        self.picker_view = PickerView(
            self.content, additions, initial_prefs=initial_prefs,
        )
        self.picker_view.pack(fill="both", expand=True)
        # Bind Next-button enabled state to the picker's validity flag.
        self.picker_view.selection_var.trace_add(
            "write", lambda *_: self._sync_picker_next()
        )

        self._set_buttons(back=True, next_text="Next >", next_enabled=False)
        self._sync_picker_next()

    def _sync_picker_next(self) -> None:
        if self.picker_view.is_valid_selection():
            self.next_btn.configure(state="normal")
        else:
            self.next_btn.configure(state="disabled")

    def _next_from_picker(self) -> None:
        sel = self.picker_view.get_selection()
        if "bambu_studio" not in sel.slicers:
            messagebox.showerror(
                "No supported slicer selected",
                "Bambu Studio is the only slicer with a working install "
                "adapter so far.",
                parent=self,
            )
            return
        self.selection = sel
        # Persist the picks so the next run re-opens with the same
        # boxes ticked instead of forcing the user to re-uncheck the
        # same lines every single time.
        try:
            self.install_mod.save_picker_prefs(self.picker_view.to_prefs())
        except Exception:
            pass  # never let a prefs glitch block the install
        self._goto_step("confirm")

    # ==================================================================
    # Step: confirm summary
    # ==================================================================
    def _step_confirm(self) -> None:
        self.title_var.set("Confirm")
        if self.action == "install":
            self.subtitle_var.set("Step 4 -- review and start the install")
        elif self.action == "update":
            self.subtitle_var.set("Step 4 -- review and apply the update")
        else:
            self.subtitle_var.set("Step 3 -- review and uninstall")

        if self.action == "uninstall":
            tracked_n = len(self.tracking.get("filament_files", []))
            ttk.Label(
                self.content,
                text=f"About to remove {tracked_n} files this installer added.\n\n"
                     f"   System folder:  {self.system_dir}\n"
                     "   Custom profiles you created -- preserved.\n"
                     "   OEM Bambu profiles -- preserved.\n"
                     "   User-modified files -- preserved (kept on disk).\n",
                font=("Segoe UI", 10), justify="left",
            ).pack(anchor="w", pady=(8, 12))
            next_text = "Uninstall"
        else:
            sel = self.selection
            n_groups = len(sel.profile_keys)
            n_slicers = len(sel.slicers)
            slicer_names = ", ".join(sorted(sel.slicers)) or "(none)"
            verb = "install" if self.action == "install" else "update"
            ttk.Label(
                self.content,
                text=f"About to {verb} {n_groups} (vendor, line, material) groups\n"
                     f"into {n_slicers} slicer(s): {slicer_names}.\n\n"
                     f"   System folder: {self.system_dir}\n",
                font=("Segoe UI", 10), justify="left",
            ).pack(anchor="w", pady=(8, 12))
            next_text = "Update" if self.action == "update" else "Install"

        ttk.Label(
            self.content,
            text="A backup of your current BBL.json + BBL/filament/ will be\n"
                 "saved to system/_backup-<timestamp>/ first.",
            foreground="#666",
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(
            self.content,
            text="Click " + next_text + " to start. You can still cancel.",
            font=("Segoe UI", 10, "italic"), foreground="#666",
        ).pack(anchor="w")

        self._set_buttons(back=True, next_text=next_text, next_enabled=True)

    def _next_from_confirm(self) -> None:
        self._goto_step("progress")

    # ==================================================================
    # Step: progress (runs the actual work)
    # ==================================================================
    def _step_progress(self) -> None:
        verb = {
            "install":   "Installing",
            "update":    "Updating",
            "uninstall": "Uninstalling",
        }.get(self.action, "Working")
        self.title_var.set(f"{verb} Filanex profiles")
        self.subtitle_var.set("Please wait. Do not close the wizard or Bambu Studio.")

        # Determinate progress bar. install.py emits "[PROGRESS verb]
        # n/total" markers from its file-copy loops; _append_progress
        # parses them, suppresses the line from the visible log, and
        # drives this bar. Bundle is 16k+ files; without this the
        # wizard looks frozen for ~30s.
        self.progress_pct_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            self.content, mode="determinate", maximum=100,
            variable=self.progress_pct_var, length=560,
        )
        self.progress_bar.pack(fill="x", pady=(2, 4))
        self.progress_status_var = tk.StringVar(value="Starting...")
        ttk.Label(
            self.content, textvariable=self.progress_status_var,
            foreground="#555", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 8))

        self.progress_text = tk.Text(
            self.content, wrap="word", font=("Consolas", 9),
            bg="#fafafa", height=20,
        )
        self.progress_text.pack(fill="both", expand=True)
        self.progress_text.configure(state="disabled")
        sb = ttk.Scrollbar(
            self.content, orient="vertical",
            command=self.progress_text.yview,
        )
        self.progress_text.configure(yscrollcommand=sb.set)
        sb.place(in_=self.progress_text, relx=1.0, rely=0,
                 relheight=1.0, anchor="ne", width=14)

        self.relaunch_btn = ttk.Button(
            self.content, text="Re-launch Bambu Studio",
            command=self._do_relaunch, state="disabled",
        )
        self.relaunch_btn.pack(side="left", anchor="s", pady=(8, 0))

        # Disable the wizard's Cancel + Back during the run, hide Next
        # until completion turns it into Finish.
        self.cancel_btn.state(["disabled"])
        self.back_btn.pack_forget()
        self.next_btn.configure(text="Finish", state="disabled",
                                command=self._finish)

        self._return_code: int | None = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._run_thread, daemon=True).start()
        self.after(80, self._poll_queue)

    def _run_thread(self) -> None:
        try:
            with redirect_stdout(_TeeStream(self._stdout_queue)):
                if self.action == "install":
                    additions_path, filament_dir = self._resolve_bundle_paths()
                    # auto_fetch=False -- the wizard already fetched
                    # before the picker step (so the picker could see
                    # any new content), no point hitting the network
                    # again here.
                    rc = self.install_mod.cmd_install(
                        self.system_dir,
                        additions_path, filament_dir,
                        self.args.force,
                        selection=self.selection,
                        auto_fetch=False,
                    )
                elif self.action == "update":
                    rc = self.install_mod.cmd_update(
                        self.system_dir, self.args.force, self.selection,
                        no_backup=self.args.no_backup,
                        was_running=False, exe_path=None, yes=True,
                    )
                elif self.action == "uninstall":
                    rc = self.install_mod.cmd_uninstall(
                        self.system_dir, self.args.force,
                    )
                else:
                    rc = 1
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
            self._stdout_queue.put(f"\nERROR (exit {rc})\n")
        except Exception:
            import traceback
            self._stdout_queue.put(
                "\n\nERROR -- traceback:\n" + traceback.format_exc()
            )
            rc = 1
        self._return_code = rc or 0
        self._stdout_queue.put("\n")  # flush

    def _poll_queue(self) -> None:
        try:
            while True:
                chunk = self._stdout_queue.get_nowait()
                self._append_progress(chunk)
        except queue.Empty:
            pass
        if self._return_code is not None and self._stdout_queue.empty():
            self._on_progress_complete()
        else:
            self.after(80, self._poll_queue)

    # Matches "[PROGRESS Installing files] 1234/16699" from install.py.
    _PROGRESS_RE = re.compile(r"\[PROGRESS\s+([^\]]+)\]\s+(\d+)\s*/\s*(\d+)")

    def _append_progress(self, s: str) -> None:
        # Pull out progress markers; suppress them from the log and
        # update the bar instead. Non-marker lines pass through.
        if "[PROGRESS" in s:
            visible: list[str] = []
            for line in s.splitlines(keepends=True):
                m = self._PROGRESS_RE.search(line)
                if m:
                    verb = m.group(1).strip()
                    current = int(m.group(2))
                    total = int(m.group(3))
                    pct = (current / total * 100) if total > 0 else 0
                    try:
                        self.progress_pct_var.set(pct)
                        self.progress_status_var.set(
                            f"{verb}: {current:,} of {total:,} ({pct:.0f}%)"
                        )
                    except Exception:
                        pass  # widgets gone (window closed) -- best-effort
                else:
                    visible.append(line)
            s = "".join(visible)
            if not s:
                return
        self.progress_text.configure(state="normal")
        self.progress_text.insert("end", s)
        self.progress_text.see("end")
        self.progress_text.configure(state="disabled")

    def _on_progress_complete(self) -> None:
        rc = self._return_code or 0
        # Snap to 100% so the bar doesn't linger mid-fill on completion.
        try:
            self.progress_pct_var.set(100.0)
            self.progress_status_var.set(
                "Done." if rc == 0 else f"Finished with exit code {rc}."
            )
        except Exception:
            pass
        self._append_progress(f"\n--- Finished (exit code {rc}) ---\n")
        self.next_btn.configure(state="normal")
        # Re-enable Back + Cancel so the user can navigate to a different
        # action without relaunching the installer. Re-show Back too.
        self.back_btn.state(["!disabled"])
        self.back_btn.pack(side="left")
        self.cancel_btn.configure(state="normal")
        if rc == 0 and self.exe_path is not None and self.action != "uninstall":
            self.relaunch_btn.configure(state="normal")
        # Pull the latest "==..." banner out of the captured stdout so
        # the title can reflect exactly what the cmd reported.
        text = self.progress_text.get("1.0", "end")
        banner = ""
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped and not stripped.startswith("="):
                banner = stripped
                if "COMPLETE" in banner or "UP TO DATE" in banner or "NOTHING" in banner:
                    break
                banner = ""
        if rc == 0:
            if self.action == "install":
                self.title_var.set("Install complete")
                self.subtitle_var.set(banner or
                    "See the log below for details. "
                    "Click Re-launch Bambu Studio or Finish.")
            elif self.action == "update":
                self.title_var.set("Update complete")
                self.subtitle_var.set(banner or
                    "See the log below. Click Re-launch Bambu Studio or Finish.")
            elif self.action == "uninstall":
                self.title_var.set("Uninstall complete")
                self.subtitle_var.set(banner or
                    "Filanex profiles removed. Click Finish to close.")
            else:
                self.title_var.set("Done")
                self.subtitle_var.set("Click Finish to close.")
        else:
            self.title_var.set("Finished with errors")
            self.subtitle_var.set("See the log above; click Finish to close.")

    def _do_relaunch(self) -> None:
        if self.exe_path is None:
            return
        try:
            self.install_mod.relaunch(self.exe_path)
            self._append_progress(
                f"\nLaunching {self.exe_path} ... close this window when ready.\n"
            )
        except Exception as e:
            messagebox.showerror("Re-launch failed", str(e), parent=self)

    def _finish(self) -> None:
        self.exit_code = self._return_code or 0
        self.destroy()

    # ==================================================================
    # Step: status (read-only)
    # ==================================================================
    def _step_status(self) -> None:
        self.title_var.set("Install status")
        self.subtitle_var.set("Read-only -- no changes are made.")

        text = tk.Text(
            self.content, wrap="word", font=("Consolas", 9),
            bg="#fafafa", height=20,
        )
        text.pack(fill="both", expand=True)

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.install_mod.cmd_status(self.system_dir)
        text.insert("1.0", buf.getvalue())
        text.configure(state="disabled")

        self._set_buttons(back=True, next_text="Finish", next_enabled=True)
        self.next_btn.configure(command=self._finish_ok)

    def _finish_ok(self) -> None:
        self.exit_code = 0
        self.destroy()


# ---------------------------------------------------------------------------
# stdout tee for the worker thread
# ---------------------------------------------------------------------------

class _TeeStream:
    def __init__(self, q):
        self._q = q
    def write(self, s):
        self._q.put(s)
        return len(s)
    def flush(self):
        pass
