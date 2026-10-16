"""Broadway width test: helpers with very long strings stay 360 wide.

Builds every widget helper with ~280-char strings inside a 360 px-wide
window and asserts the window allocation stays 360 wide. A long
unwrapped label silently widens the whole window, so this fails while
any helper imposes a natural width above the phone canvas.
"""

from broadway_harness import make_home, run_driver


def test_helpers_with_long_strings_stay_360_wide(tmp_path):
    home = make_home(tmp_path)
    done = run_driver('broadway_width_driver.py', home)
    assert done.returncode == 0, \
        f'stdout={done.stdout}\nstderr={done.stderr}'
    assert 'width ok 360' in done.stdout
