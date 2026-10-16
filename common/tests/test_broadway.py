"""Broadway smoke test: apply() + every widget helper, no GLib criticals.

Spawns a private ``gtk4-broadwayd`` and runs
``tests/broadway_widgets_driver.py`` against it; skips cleanly when the
daemon binary is missing.
"""

from broadway_harness import make_home, run_driver


def test_widgets_under_broadway(tmp_path):
    home = make_home(tmp_path)
    done = run_driver('broadway_widgets_driver.py', home)
    assert done.returncode == 0, \
        f'stdout={done.stdout}\nstderr={done.stderr}'
    assert 'broadway widgets ok' in done.stdout
