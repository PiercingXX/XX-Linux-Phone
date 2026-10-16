# XX-Dialer (Linux)

The phone app that knows who is allowed to ring — ported from the
PiercingXX Android dialer (`ref/xx-dialer/`) to Linux phones
(postmarketOS / Mobian / FuriOS / PureOS, phoc + XX-WM or Phosh).

Full port plan: [PORT_SPEC.md](PORT_SPEC.md).

## What this scaffold is

Spec + runnable skeleton (lane 0): meson install, an `Adw.Application`
placeholder that paints itself with the user's XX-WM theme, and the pure
Python modules later lanes build on — `policy` (ring-policy engine),
`theme` (shell-theme reuse), `config` (app config). No telephony yet.

## Run from the checkout

```sh
PYTHONPATH=src python3 -m xx_dialer
```

Installed (after `meson setup build && meson install`):

```sh
xx-dialer
```

## Tests

```sh
python3 -m pytest -q tests
ruff check src tests
```

Tests run headless: they cover only the pure modules (policy engine,
theme loading, config parsing) and never touch GTK or D-Bus.

## Layout

- `PORT_SPEC.md` — feature inventory, Linux mapping, XX-WM integration,
  data model, architecture, build lanes
- `meson.build` / `data/` — install (package to datadir, launcher to
  bindir, `.desktop` + metainfo + D-Bus activation)
- `src/xx_dialer/` — `main.py`, `policy.py`, `theme.py`, `config.py`
- `tests/` — headless pytest suite
