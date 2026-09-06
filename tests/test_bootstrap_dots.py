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
    # rm -rf must sit in the cache branch, not the existing-clone branch.
    home_branch = text.split('if [ -d "${HOME}/piercing-dots" ]')[1]
    home_branch, cache_branch = home_branch.split('else', 1)
    assert 'rm -rf' not in home_branch
    assert 'rm -rf' in cache_branch
    assert '.cache/piercing-dots' in cache_branch


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
