"""Lock-screen handoff contract: dialer service vs the XX-WM shell.

The shell keeps the lock-screen + incoming-call surface and consults
this repo's policy service before its first ring. This test parses the
shell's own sources (``call_screen.py``, ``call_wiring.py``,
``notif_daemon.py``, ``docs/notifications.md``) and asserts every
name, path, signature, timeout and action key matches this service —
so a rename on either side fails here instead of silently unscreening
calls on the device.

The shell tree resolves to ``ref/xx-wm`` first (lane copies), then
the ``../xx-wm`` sibling checkout (real layout); the test skips when
neither exists.
"""
import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from xx_dialer import ringer, service

ROOT = Path(__file__).resolve().parent.parent


def _shell_root() -> Path:
    for candidate in (ROOT / "ref" / "xx-wm", ROOT.parent / "xx-wm"):
        if (candidate / "launcher" / "src" / "call_screen.py").is_file():
            return candidate
    pytest.skip("xx-wm tree not present (ref/xx-wm nor ../xx-wm)")


def _read(root: Path, *parts: str) -> str:
    return (root.joinpath(*parts)).read_text(encoding="utf-8")


def _consts(source: str, names: set[str]) -> dict:
    """Module-level NAME = <str/int/frozenset> assignments via ast."""
    found: dict = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in names:
            continue
        found[target.id] = _eval(node.value)
    return found


def _eval(node: ast.AST):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("frozenset", "set", "tuple", "list") \
            and len(node.args) == 1 and not node.keywords:
        return {"frozenset": frozenset, "set": set, "tuple": tuple,
                "list": list}[node.func.id](ast.literal_eval(node.args[0]))
    return ast.literal_eval(node)


def _screen() -> dict:
    return _consts(
        _read(_shell_root(), "launcher", "src", "call_screen.py"),
        {"BUS_NAME", "OBJECT_PATH", "INTERFACE", "CONSULT_TIMEOUT_MS",
         "FAIL_OPEN_VERDICT", "FAIL_OPEN_REASON", "VALID_VERDICTS",
         "NOTE_TIMEOUT_MS"})


def test_bus_name_path_and_interface_match():
    shell = _screen()
    assert shell["BUS_NAME"] == service.BUS_NAME
    assert shell["OBJECT_PATH"] == service.OBJECT_PATH
    assert shell["INTERFACE"] == service.INTERFACE
    assert service.BUS_NAME == "io.piercingxx.XXDialer.Policy"
    assert service.OBJECT_PATH == "/io/piercingxx/XXDialer/Policy"
    assert service.INTERFACE == "io.piercingxx.XXDialer.Policy1"


def test_timeouts_and_fail_open_match():
    shell = _screen()
    assert shell["CONSULT_TIMEOUT_MS"] == service.CONSULT_TIMEOUT_MS == 2000
    assert shell["FAIL_OPEN_VERDICT"] == service.FAIL_OPEN_VERDICT == "ring"
    assert shell["FAIL_OPEN_REASON"] == service.FAIL_OPEN_REASON == \
        "fail-open"
    assert set(shell["VALID_VERDICTS"]) == {"ring", "silence", "block"}


def test_consult_call_shape_matches():
    """Async CheckNumber, auto-start allowed, (ss) reply, no owner probe."""
    source = _read(_shell_root(), "launcher", "src", "call_screen.py")
    assert '"CheckNumber"' in source
    assert 'VariantType.new("(ss)")' in source
    assert "DBusCallFlags.NONE" in source
    assert "NO_AUTO_START" not in source
    assert "bus_name_has_owner" not in source
    assert "GetNameOwner" not in source
    assert "NameHasOwner" not in source
    assert '"NoteCall"' in source
    assert '"(ssxxs)"' in source


def test_wiring_handoff_matches():
    """Incoming consults once; block hangs up shell-side; silence only
    stops the ringer; the shell posts its own silenced card only when
    the consult never reached the service (the service owns those)."""
    source = _read(_shell_root(), "launcher", "src", "call_wiring.py")
    assert "consult_check_number(raw, _on_verdict" in source
    assert "hangup_call(path" in source
    assert "call_ui.stop_ringing()" in source
    assert "not pending.get('consult_reached')" in source
    assert "_post_silenced_missed" in source
    assert "XXDialer.Policy" not in source


def _introspection() -> ET.Element:
    return ET.fromstring(service.INTROSPECTION_XML)


def _method(name: str) -> ET.Element:
    for method in _introspection().iter("method"):
        if method.get("name") == name:
            return method
    raise AssertionError("missing method %s" % name)


def test_introspection_covers_shell_calls():
    check = _method("CheckNumber")
    assert [(a.get("direction"), a.get("type")) for a in check] == [
        ("in", "s"), ("out", "s"), ("out", "s")]
    note = _method("NoteCall")
    assert [(a.get("direction"), a.get("type")) for a in note] == [
        ("in", "s"), ("in", "s"), ("in", "x"), ("in", "x"), ("in", "s")]
    action = _method("HandleNotificationAction")
    assert [(a.get("direction"), a.get("type")) for a in action] == [
        ("in", "u"), ("in", "s")]
    signals = {signal.get("name"): [a.get("type") for a in signal]
               for signal in _introspection().iter("signal")}
    assert signals == {"VerdictDecided": ["s", "s", "s"],
                       "BypassChanged": ["b", "t"]}


def test_action_target_matches_shell_daemon():
    root = _shell_root()
    daemon = _read(root, "launcher", "src", "notif_daemon.py")
    assert re.search(r"_ACTION_TARGET_HINT\s*=\s*'x-xx-wm-action-target'",
                     daemon)
    assert re.search(r"_ACTION_METHOD\s*=\s*'HandleNotificationAction'",
                     daemon)
    assert re.search(r"_ACTION_TIMEOUT_MS\s*=\s*5_000", daemon)
    assert ringer.ACTION_TARGET_HINT == "x-xx-wm-action-target"
    doc = _read(root, "docs", "notifications.md")
    match = re.search(
        r'"(io\.piercingxx\.XXDialer\.Policy'
        r' /io/piercingxx/XXDialer/Policy'
        r' io\.piercingxx\.XXDialer\.Policy1)"', doc)
    assert match is not None
    assert ringer.action_target() == match.group(1)
    assert {ringer._ACTION_CALL_BACK, ringer._ACTION_RING_NEXT,
            ringer._ACTION_BLOCK} == {"call-back", "ring-next-time",
                                      "block-contact"}
    for name in ("call-back", "ring-next-time", "block-contact"):
        assert ("`%s`" % name) in doc
