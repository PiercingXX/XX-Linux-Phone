"""First-run setup page: BYO client ID plus Google sign-in (thin view).

Ports SetupScreen over xx_email.auth (loopback flow) and
xx_email.setup (completion): paste a Desktop-type OAuth client ID,
read the short how-to (full guide in docs/oauth-setup.md), then
"Sign in with Google" opens the consent page in the browser. The
loopback listener, the exchange, and the keyring writes all run on
the app's worker threads through hooks — this page only shows state.
A locked keyring shows "Unlock your keyring to continue" with Retry.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from xx_email import auth as auth_mod
from xx_email import setup as setup_mod
from xx_email.ui import common as common_mod

TITLES = {"first-run": "Welcome", "add": "Add account",
          "reauth": "Sign in again"}

INTRO = ("A private Gmail client. No tracking, no ads: mail goes "
         "straight between this phone and Google.")

HOWTO = ("One-time setup:\n"
         "1. In Google Cloud Console, enable the Gmail API and create\n"
         "an OAuth client of type Desktop app (loopback-capable).\n"
         "2. Paste its client ID below.\n"
         "3. Sign in with Google: approve the consent page that opens\n"
         "in your browser.\n"
         "Full guide: docs/oauth-setup.md")


class SetupPage(Gtk.Box):
    """BYO-client-ID entry plus sign-in for one flow (see hooks)."""

    def __init__(self, hooks, toast, *, mode: str = "first-run",
                 account: str | None = None,
                 client_id: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._hooks = hooks
        self._toast = toast
        self._mode = mode if mode in TITLES else "first-run"
        self._busy = False
        self._locked = False

        header = common_mod.phone_header(TITLES[self._mode])
        if self._mode != "first-run":
            back = common_mod.text_button("Back")
            back.connect("clicked", lambda _b: self._on_back())
            header.pack_start(back)
        self.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=12)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(16)
        content.set_margin_bottom(16)

        intro = common_mod.wrapped(Gtk.Label(label=INTRO))
        content.append(intro)
        if self._mode == "reauth" and account:
            who = common_mod.wrapped(Gtk.Label(
                label=f"Signing in again as {account} — your cached "
                      "mail stays on this phone."))
            content.append(who)

        howto = common_mod.wrapped(Gtk.Label(label=HOWTO))
        howto.set_selectable(True)
        content.append(howto)

        guide = common_mod.text_button("View setup guide")
        guide.set_tooltip_text("Open docs/oauth-setup.md")
        guide.connect("clicked", lambda _b: hooks.open_setup_guide())
        content.append(guide)

        id_label = Gtk.Label(label="OAuth client ID")
        id_label.set_xalign(0.0)
        content.append(id_label)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text(
            "1234567890-abc.apps.googleusercontent.com")
        self._entry.set_hexpand(True)
        self._entry.set_text(client_id or "")
        self._entry.connect("changed", lambda _e: self._paint())
        self._entry.connect("activate", lambda _e: self._submit())
        content.append(self._entry)

        self._lock_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._lock_box.add_css_class("email-banner")
        self._lock_box.set_visible(False)
        lock_label = common_mod.wrapped(Gtk.Label(
            label=setup_mod.KEYRING_LOCKED_MESSAGE))
        lock_label.set_hexpand(True)
        self._lock_box.append(lock_label)
        retry = common_mod.text_button("Retry")
        retry.connect("clicked", lambda _b: self._recheck_keyring())
        self._lock_box.append(retry)
        content.append(self._lock_box)

        self._status = common_mod.wrapped(Gtk.Label(label=""))
        self._status.add_css_class("email-muted")
        content.append(self._status)

        action_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Adw.Spinner()
        self._spinner.set_visible(False)
        action_row.append(self._spinner)
        self._signin = common_mod.text_button("Sign in with Google")
        self._signin.set_hexpand(True)
        self._signin.connect("clicked", lambda _b: self._submit())
        action_row.append(self._signin)
        content.append(action_row)

        revoke = common_mod.text_button("Revoke Google access")
        revoke.set_tooltip_text(
            "Open your Google permissions page in the browser")
        revoke.connect("clicked",
                       lambda _b: hooks.open_permissions())
        content.append(revoke)

        clamp = Adw.Clamp(maximum_size=480)
        clamp.set_child(content)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(clamp)
        self.append(scrolled)

        self._paint()
        hooks.lookup_keyring(self._on_lock_known)

    # ------------------------------------------------------------------ state

    def _paint(self) -> None:
        valid = auth_mod.is_valid_client_id(self._entry.get_text())
        self._signin.set_sensitive(bool(valid) and not self._busy
                                   and not self._locked)
        self._lock_box.set_visible(self._locked)

    def _recheck_keyring(self) -> None:
        self._status.set_label("Checking the keyring…")
        self._hooks.lookup_keyring(self._on_lock_known)

    def _on_lock_known(self, result) -> None:
        if isinstance(result, Exception):
            self._locked = False
            self._status.set_label("Couldn't check the keyring.")
        else:
            self._locked = bool(result)
            if self._locked:
                self._status.set_label(
                    setup_mod.KEYRING_LOCKED_MESSAGE + ".")
            elif self._status.get_label() in (
                    "Checking the keyring…",
                    setup_mod.KEYRING_LOCKED_MESSAGE + "."):
                self._status.set_label("")
        self._paint()

    # ---------------------------------------------------------------- sign-in

    def _submit(self) -> None:
        if self._busy or not self._signin.get_sensitive():
            return
        self._busy = True
        self._spinner.set_visible(True)
        self._status.set_label("Waiting for Google…")
        self._paint()
        self._hooks.start_sign_in(self._entry.get_text().strip(),
                                  self._on_browser_url,
                                  self._on_sign_in_result)

    def _on_browser_url(self, _url: str) -> None:
        self._status.set_label("Waiting for Google…")

    def _on_sign_in_result(self, result) -> None:
        self._busy = False
        self._spinner.set_visible(False)
        if isinstance(result, Exception):
            if isinstance(result, setup_mod.KeyringLocked):
                self._locked = True
                self._status.set_label(str(result) + ".")
            else:
                self._status.set_label(str(result))
            self._paint()
            return
        self._status.set_label("")
        self._paint()
        self._hooks.account_ready(result)

    def _on_back(self) -> None:
        self._hooks.cancel_sign_in()
        self._hooks.go_back()


def setup_navigation_page(page: SetupPage) -> Adw.NavigationPage:
    navigation = Adw.NavigationPage(title="Setup")
    navigation.set_child(page)
    return navigation
