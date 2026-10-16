"""Safe DEST in scripts/bootstrap-dots.sh.

The tablet already has ~/piercing-dots without --profile phone. Pointing
DEST at that clone while leaving rm -rf would delete the user's repo.
rm -rf is allowed only on ~/.cache/piercing-dots.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / 'scripts' / 'bootstrap-dots.sh'


@pytest.fixture(scope='module')
def script_text():
    assert SCRIPT.is_file(), f'missing {SCRIPT}'
    return SCRIPT.read_text()


def test_existing_clone_is_reused(script_text):
    assert '${HOME}/piercing-dots' in script_text or '$HOME/piercing-dots' in script_text
    assert 'DEST="${HOME}/piercing-dots"' in script_text or \
        "DEST=\"$HOME/piercing-dots\"" in script_text or \
        'DEST="${HOME}/piercing-dots"' in script_text


def test_existing_home_clone_is_the_dest(script_text):
    assert '${HOME}/piercing-dots' in script_text
    assert '-d "${HOME}/piercing-dots"' in script_text


def test_no_rm_rf_on_existing_clone(script_text):
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('rm -rf'):
            assert 'piercing-dots' not in line or '.cache' in line, \
                f'rm -rf must only target the cache clone: {line}'
    assert 'rm -rf' in script_text


def test_rm_rf_is_only_for_cache_clone(script_text):
    assert 'rm -rf "${HOME}/piercing-dots"' not in script_text
    assert '${HOME}/.cache/piercing-dots' in script_text
    assert re.search(r'rm -rf "\$DEST"', script_text)
    start = script_text.find('if [ -d "${HOME}/piercing-dots" ]')
    assert start != -1
    block = script_text[start:]
    then_arm, rest = block.split('else', 1)
    cache_arm, after = rest.split('\nfi', 1)
    assert 'rm -rf' not in then_arm
    dest_at = cache_arm.find('DEST="${HOME}/.cache/piercing-dots"')
    rm_at = cache_arm.find('rm -rf')
    assert dest_at != -1
    assert rm_at != -1
    assert dest_at < rm_at
    assert 'rm -rf "$DEST"' in cache_arm
    # Code after this if/fi must not grow an rm of the home clone.
    assert 'rm -rf "${HOME}/piercing-dots"' not in after


def test_cache_clone_still_fresh(script_text):
    assert '.cache/piercing-dots' in script_text


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
