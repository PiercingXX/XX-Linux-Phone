"""Static guard: Gio call/call_sync reply_type must be GLib.VariantType.

Real PyGObject raises
``TypeError: argument reply_type: Expected GLib.VariantType, but got str``
for a plain-string reply_type, while test fakes accept anything, so a
string literal here is a silent no-op on a real system (the receive
prompt never appeared). This AST walk fails on any string literal in
the reply_type slot (positional index 5, or the ``reply_type=``
keyword) of a ``call``/``call_sync`` invocation anywhere in src/.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _offenders(path: Path) -> list[str]:
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name not in ("call", "call_sync"):
            continue
        target = None
        for keyword in node.keywords:
            if keyword.arg == "reply_type":
                target = keyword.value
        if target is None and len(node.args) >= 6:
            target = node.args[5]
        if (isinstance(target, ast.Constant)
                and isinstance(target.value, str)):
            found.append(f"{path.name}:{node.lineno} "
                         f"reply_type={target.value!r}")
    return found


def test_no_string_reply_type_in_dbus_calls():
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        offenders.extend(_offenders(path))
    assert not offenders, (
        "string reply_type in D-Bus call (use "
        "GLib.VariantType.new(...) or None): " + "; ".join(offenders)
    )
