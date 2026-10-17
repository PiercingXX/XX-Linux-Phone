# xx-share

Nearby Share for Linux phones (XX-WM / Phosh), LocalSend-compatible.

Build and install `common/` first: the UI needs the installed `xx_ui`
python package (themes, stylesheet and widget builders from
`common/xx_ui`). Running from source or tests works without installing:
`tests/conftest.py` and `tools/` put `../common` on `sys.path`.

Interop: LocalSend protocol `2.2` (advertised; `2.x` peers accepted).
`2.1` → `2.2` added only the `422` checksum-mismatch reply on upload;
see `PORT_SPEC.md`.
