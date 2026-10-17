# xx-keyboard — the phone's own on-screen keyboard

A fork of Phosh [Stevia](https://gitlab.gnome.org/World/Phosh/stevia)
v0.58.0 (C/GTK3/libhandy, GPL-3.0-or-later; same license as upstream).
It is the XX phone's on-screen keyboard: xx-wm starts `xx-keyboard`
instead of squeekboard, and the shell talks to it over the unchanged
`sm.puri.OSK0` D-Bus interface, so `xx-wm/launcher/src/osk.py` works
with either backend.

## What the fork changes (kept small and commented for `git subtree pull`)

- **Identity.** The binary, meson project and install paths are
  `xx-keyboard`. The app id is `io.piercingxx.XXKeyboard` (own desktop
  file, systemd unit and metainfo). The D-Bus interface and protocol
  behaviour (`sm.puri.OSK0`, `SetVisible`) are unchanged on purpose.
  Upstream source file names (`phosh-osk-stevia.c`, the
  `/mobi/phosh/stevia/` GResource prefix) are kept so upstream diffs
  stay readable.
- **GSettings.** Schema ids stay `mobi.phosh.osk.*`. No rename is
  needed to coexist: squeekboard keeps its own configuration, and both
  keyboards read the shared `org.gnome.desktop.input-sources` sources.
- **Theme.** `src/pos-xx-theme.[hc]` reads `~/.config/xx-wm/config.json`
  (same keys/presets as `common/xx_ui/theme.py`) and renders a
  GTK3 CSS override loaded at APPLICATION priority, reloaded live via
  GFileMonitor. The preset table is the generated
  `xx-theme-presets.h`, produced at build time by
  `tools/gen-xx-theme.py` from `common/xx_ui/theme.py`, so the colours
  never drift. Missing or invalid config falls back to amoled.
- **Layouts.** `src/layouts/piercing-num.json` holds the owner's
  Piercing touch letters with a number/symbol layer like the Android
  keyboard's; `us+colemak.json` keeps shipping unchanged. With empty
  input sources the keyboard falls back to xx-wm's `keyboard_layout`
  config key (default `us+colemak`); Piercing is always offered
  alongside the configured layouts.
- **Glide.** The `glide/` decoder library is owned by a parallel lane;
  this lane only reserves the `-Dglide` meson option.

## Updating from upstream

The import is meant to track Stevia via `git subtree pull`:

```sh
git subtree pull --prefix xx-keyboard https://gitlab.gnome.org/World/Phosh/stevia main --squash
```

Fork-owned files (`pos-xx-theme.*`, `tools/gen-xx-theme.py`,
`piercing-num.json`, `README.xx.md`, `tests/test-xx-*`) do not exist
upstream and merge cleanly. Fork markers in shared files all start
with `xx-keyboard:` (meson) or `/* xx-keyboard: ... */` (C) — after a
pull, re-check those hunks, regenerate nothing by hand (the preset
header is a build artifact), and run `meson test`.

## Building

```sh
meson setup _b xx-keyboard -Dglide=false
ninja -C _b
meson test -C _b
```

Display-requiring tests (layout loading, widgets) need a display; with
none attached they abort on `cannot open display`. Run them under the
GTK3 Broadway backend instead:

```sh
broadwayd :3 &
GDK_BACKEND=broadway BROADWAY_DISPLAY=:3 meson test -C _b
```

`test-xx-theme` and `test-xx-layouts` are headless by design and pass
either way. `test-completers` needs `/usr/share/dict/words` (the
`wordlist` meson option) on the host.

There is no standalone preview/demo mode upstream (`examples/` holds
only a completer demo; `tools/screenshot-all-layouts.py` needs a
running phoc compositor), so headless rendering is verified through
the layout-loading tests under Broadway.
