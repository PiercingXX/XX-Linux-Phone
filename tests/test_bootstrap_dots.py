"""bootstrap-dots.sh must not wipe an existing ~/piercing-dots clone."""
from pathlib import Path
import re
import subprocess

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'bootstrap-dots.sh'


def test_existing_home_clone_is_the_dest():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '${HOME}/piercing-dots' in text
    assert '-d "${HOME}/piercing-dots"' in text


def test_rm_rf_is_only_for_cache_clone():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'rm -rf "${HOME}/piercing-dots"' not in text
    assert '${HOME}/.cache/piercing-dots' in text
    assert re.search(r'rm -rf "\$DEST"', text)
    start = text.find('if [ -d "${HOME}/piercing-dots" ]')
    assert start != -1
    block = text[start:]
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


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
