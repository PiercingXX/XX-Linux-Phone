"""Vendored pywayland protocol classes (generated -- see README.md).

Upstream pywayland ships no wlr-foreign-toplevel-management module; this
package commits the scanner output so devices work out of the box. The .py
files are generated from the protocol XMLs vendored at launcher/data/ --
do not edit them by hand.
"""


def _patch_global_subscriptable() -> None:
    """pywayland 0.4.18's Global is not Generic; the 0.4.19 scanner emits
    ``class XGlobal(Global[Iface])`` bases. On Python 3.14 that raises
    ``TypeError: type 'Global' is not subscriptable`` and takes the shell
    down at ``import wayland_proto.wayland``. Make ``Global[Iface]`` return
    ``Global`` so the vendored modules import on both versions.
    """
    try:
        from pywayland.protocol_core import Global
    except ImportError:
        return
    if not hasattr(Global, '__class_getitem__'):
        Global.__class_getitem__ = classmethod(  # type: ignore[attr-defined,misc]
            lambda cls, _item: cls)


_patch_global_subscriptable()
