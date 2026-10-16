"""IPC page-driving verbs for rigs (no gesture timing needed).

page.home / page.apps / page.settings switch the stack page and
settings.open[:section] reuses the settings deep-link
(open_settings(section)). Dispatch is split so it stays testable without
a display: ipc.parse_page_command owns the table (unit-tested here
against the real ipc module), main.py routes through it (asserted by
call-site text, the test_preload_gate idiom — main.py needs GTK).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

REPO = Path(__file__).parent.parent
MAIN_PY = REPO / 'launcher' / 'src' / 'main.py'


sys.path.insert(0, str(Path(__file__).parent))

# Reuse test_ipc's fake-gi stack instead of installing a second one: two
# competing `gi` fakes break whichever ipc test module imports second.
import test_ipc  # noqa: E402,F401  (fake gi stack for ipc)
import ipc  # noqa: E402


class TestParsePageCommand:
    @pytest.mark.parametrize(('command', 'expected'), [
        ('page.home', ('page', 'home')),
        ('page.apps', ('page', 'apps')),
        ('page.settings', ('page', 'settings')),
        ('settings.open', ('settings.open', None)),
        ('settings.open:wifi', ('settings.open', 'wifi')),
        ('settings.open:bluetooth', ('settings.open', 'bluetooth')),
        ('settings.open:mobile', ('settings.open', 'mobile')),
        ('settings.open:bogus', ('settings.open', 'bogus')),
    ])
    def test_page_commands_parse(self, command, expected):
        assert ipc.parse_page_command(command) == expected

    @pytest.mark.parametrize('command', [
        'lock', 'shade.show', 'switcher.hide', 'gesture.home', 'welcome',
        'page', 'page.homes', 'settings', 'settings.openwifi', 'PAGE.HOME',
        '',
    ])
    def test_other_commands_not_page_commands(self, command):
        assert ipc.parse_page_command(command) is None

    def test_table_covers_all_stack_pages(self):
        """Every stack page stays reachable without gesture timing."""
        import ast
        import re

        text = (REPO / 'launcher' / 'src' / 'window.py').read_text(
            encoding='utf-8')
        match = re.search(r'_PAGE_ORDER\s*=\s*(\[[^\]]*\])', text)
        assert match, '_PAGE_ORDER missing from window.py'
        assert (sorted(ipc.PAGE_COMMANDS.values())
                == sorted(ast.literal_eval(match.group(1))))

    def test_server_docstring_lists_page_verbs(self):
        doc = ipc.IPCServer.__doc__ or ''
        for verb in ('page.home', 'page.apps', 'page.settings',
                     'settings.open'):
            assert verb in doc, f'{verb} missing from IPCServer docstring'


class TestMainDispatch:
    @pytest.fixture(scope='class')
    def main_src(self):
        assert MAIN_PY.is_file()
        return MAIN_PY.read_text(encoding='utf-8')

    def test_routes_through_parse_helper(self, main_src):
        assert 'parse_page_command' in main_src
        handler_at = main_src.find('def _on_ipc_command')
        assert handler_at != -1
        assert main_src.find('parse_page_command', handler_at) != -1

    def test_page_verb_switches_stack(self, main_src):
        assert 'def _show_ipc_page' in main_src
        assert 'set_visible_child_name' in main_src

    def test_settings_verb_reuses_deep_link(self, main_src):
        """settings.open must reuse open_settings(section), not grow a
        second deep-link implementation."""
        assert 'def _open_ipc_settings' in main_src
        body_start = main_src.find('def _open_ipc_settings')
        body = main_src[body_start:body_start + 400]
        assert 'open_settings' in body

    def test_page_verbs_silenced_while_locked(self, main_src):
        for verb in ('page.home', 'page.apps', 'page.settings'):
            assert verb in main_src
        assert '_ipc_silenced_while_locked' in main_src
        assert "startswith('settings.open:')" in main_src

    def test_main_py_still_parses(self):
        import ast

        ast.parse(MAIN_PY.read_text(encoding='utf-8'))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
