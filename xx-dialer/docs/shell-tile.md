# Shell quick-settings tile: Screening (spec for the shell lane)

The Android app's Expecting-a-call QS tile (`ring/XxTileService.kt`,
bounded countdown label) has no per-app QS-tile API on Phosh/XX-WM,
so the equivalent lives in the **shell**, reading this app's state.
This file specifies it; nothing here is implemented in the dialer.

## Tile: "Screening"

- Label: `Screening: Enforcing` when
  `~/.config/xx-dialer/config.json` `enforcement_mode` is
  `enforcing`, else `Screening: Observing`. Tolerant parse (missing
  file or keys → `Observing`, the fail-open default); watch the file
  with a debounced monitor, exactly like the theme hot-reload.
- Suffix while a bypass runs: `· expecting call H:MM` from
  `bypass_until` (unix ms) in the same file, live-updated by the
  `BypassChanged(b active, t expires_unix)` signal on
  `io.piercingxx.XXDialer.Policy` `/io/piercingxx/XXDialer/Policy`
  `io.piercingxx.XXDialer.Policy1` (no polling).
- Tap: launch the dialer Rules page (app id `io.piercingxx.XXDialer`);
  the in-app Expecting-a-call button stays the primary arming surface.
- Long-press (if the shell supports it): same — open Rules. No direct
  arm/cancel from the tile: arming writes `bypass_until`, which today
  only the dialer UI writes. If a later lane wants one-tap arming from
  the tile, the dialer must first expose it on Policy1 (e.g.
  `ArmBypass(u minutes)`); the tile must never write the dialer's
  config file itself (single writer: the dialer owns its config).

## Absent dialer

Tile hidden when `io.piercingxx.XXDialer.Policy` is neither owned nor
activatable and no dialer config file exists — same rule as the
consult: installing the dialer never removes shell behavior, and the
shell never shows dead dialer UI.
