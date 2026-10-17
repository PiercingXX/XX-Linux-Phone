"""Run a Broadway driver subprocess with one connection-error retry.

A contacts Broadway run once hit "Connection reset by peer": the
daemon socket can drop mid-run under load. Retry exactly once, and
only when the failure looks like a transport error — marker
mismatches and assertion failures still fail fast.
"""

import subprocess

_CONNECTION_SNIPPETS = (
    "Connection reset by peer",
    "Connection refused",
    "Broken pipe",
    "connection closed",
)


def run_driver(cmd, env, timeout):
    """Run ``cmd``; retry once on transport-level Broadway failures."""
    last = None
    for _attempt in range(2):
        last = subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            timeout=timeout,
        )
        if last.returncode == 0:
            return last
        err = last.stderr or ""
        if not any(snippet in err for snippet in _CONNECTION_SNIPPETS):
            return last
    return last
