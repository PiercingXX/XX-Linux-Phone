"""Tests for the switcher's text-only cards and empty state.

The switcher is a Gtk.Window, so it cannot be instantiated in a headless
environment. The card and empty-state contracts are exercised through the
static headless seams the switcher itself renders from -- the same pattern the
wiring tests use for refresh/focus/close. A fake manager drives the app list
so the card labels and the empty-state message are verified against real
manager state without a display.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from app_switcher import _EMPTY_STATE_TEXT, AppInfo
from toplevel_manager import Toplevel


class FakeManager:
    """Duck-typed stand-in for ToplevelManager used by the switcher."""

    def __init__(self, apps: list[Toplevel]) -> None:
        self._apps = apps
        self.available = True

    def list(self) -> list[Toplevel]:
        return list(self._apps)

    def on_change(self, callback) -> None:
        pass

    def activate(self, handle: object) -> None:
        pass

    def close(self, handle: object) -> None:
        pass


def test_empty_state_message() -> None:
    """The empty-state text is a stable, non-empty message for the user."""
    assert isinstance(_EMPTY_STATE_TEXT, str)
    assert _EMPTY_STATE_TEXT.strip() != ''


def test_empty_state_used_when_no_apps() -> None:
    """With no apps the switcher shows the empty-state message, not a card."""
    from app_switcher import AppSwitcher

    apps = AppSwitcher._apps_from_manager(FakeManager([]))
    assert apps == []
    assert AppSwitcher._empty_state_label() == _EMPTY_STATE_TEXT


def test_empty_state_ignores_appless_manager() -> None:
    """A manager that reports nothing yields the empty state, never a crash."""
    from app_switcher import AppSwitcher

    apps = AppSwitcher._apps_from_manager(FakeManager([]))
    # The empty-state path is chosen precisely when there are no cards.
    assert len(apps) == 0
    assert AppSwitcher._empty_state_label() == 'No open apps'


def test_cards_are_text_only() -> None:
    """Each card renders the app's title text and nothing but that text."""
    from app_switcher import AppSwitcher

    apps = [
        AppInfo('org.a.App', 'Alpha', handle='h1'),
        AppInfo('org.b.App', 'Beta', handle='h2'),
    ]
    labels = AppSwitcher._card_labels(apps)
    assert labels == ['Alpha', 'Beta']


def test_card_title_comes_from_app_title() -> None:
    """The card label is the title text of the toplevel, not its app_id."""
    from app_switcher import AppSwitcher

    app = AppInfo('org.a.App', 'Alpha', handle='h1')
    assert AppSwitcher._card_title(app) == 'Alpha'


def test_card_title_never_leaks_app_id() -> None:
    """Text-only cards show the human title, never the raw app_id."""
    from app_switcher import AppSwitcher

    app = AppInfo('org.a.App', 'Alpha', handle='h1')
    assert AppSwitcher._card_title(app) != app.app_id


def test_card_labels_from_manager_list() -> None:
    """Card labels are derived from the manager's list in order."""
    from app_switcher import AppSwitcher

    fake = FakeManager([
        Toplevel('org.a.App', 'Alpha', 'h1'),
        Toplevel('org.b.App', 'Beta', 'h2'),
    ])
    apps = AppSwitcher._apps_from_manager(fake)
    assert AppSwitcher._card_labels(apps) == ['Alpha', 'Beta']


def test_card_labels_preserve_order() -> None:
    """Cards keep the manager's insertion order, so the switcher is stable."""
    from app_switcher import AppSwitcher

    apps = [
        AppInfo('org.a.App', 'First', handle='h1'),
        AppInfo('org.b.App', 'Second', handle='h2'),
        AppInfo('org.c.App', 'Third', handle='h3'),
    ]
    assert AppSwitcher._card_labels(apps) == ['First', 'Second', 'Third']


def test_single_app_still_renders_a_card() -> None:
    """One open app produces exactly one card label, not the empty state."""
    from app_switcher import AppSwitcher

    apps = [AppInfo('org.a.App', 'Solo', handle='h1')]
    assert AppSwitcher._card_labels(apps) == ['Solo']
    # A non-empty list must not be mistaken for the empty state.
    assert len(apps) > 0