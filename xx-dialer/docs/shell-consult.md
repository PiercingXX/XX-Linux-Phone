# Shell consult contract: `io.piercingxx.XXDialer.Policy`

How the XX-WM shell screens an incoming call through the dialer's
policy engine. Bus name `io.piercingxx.XXDialer.Policy`, object path
`/io/piercingxx/XXDialer/Policy`, interface `io.piercingxx.XXDialer.Policy1`.
Reference implementation: `src/xx_dialer/service.py`
(`consult_check_number` is the shell-side helper);
introspection XML is `INTROSPECTION_XML` there.

The policy endpoint has its OWN bus name on purpose: the dialer GUI's
`application_id` is `io.piercingxx.XXDialer`, and `Gtk.Application`
registers that id as a bus name at startup. If the background service
owned the same name, the GUI would treat the service as the primary
instance, call `org.gtk.Actions` on it and exit with "Failed to
register: No such interface `org.gtk.Actions`". Never move the
service back onto the app id.

## Activation: call it, do not probe for it

The policy service is D-Bus activatable
(`data/io.piercingxx.XXDialer.Policy.service`, exec `xx-dialer
--service`). An activatable service has **no owner** until the bus
daemon starts it on the first method call addressed to the name, so
never gate the consult on `GetNameOwner` / `NameHasOwner` /
`bus_name_has_owner`: after a reboot that probe reports "absent", the
service never starts, and screening is silently off. Either ask
`ListActivatableNames` on `org.freedesktop.DBus`, or — simplest and
preferred — just call `CheckNumber` and let the call itself activate
the service.

## `CheckNumber`

- Signature: `CheckNumber(in s number) -> (out s verdict, out s reason)`.
  `number` is the caller ID as delivered (any format; the service
  normalizes to E.164 itself). Empty/withheld input is accepted and
  screened under the hidden-caller policy.
- Call it **once per incoming call, before the first ring**, with
  auto-start allowed: `Gio.DBusCallFlags.NONE` — never
  `G_DBUS_CALL_FLAGS_NO_AUTO_START`, which would suppress activation.
- Timeout: **2000 ms**. Make the call asynchronously; never block the
  shell main loop waiting for it.
- Reply: unpack the `(ss)` tuple (`reply.unpack()` yields
  `(verdict, reason)`). Callback takes exactly `(conn, result)` — do
  not pass trailing `user_data` to `bus.call`.
- **Fail-open:** any error, timeout, or a verdict outside
  `ring|silence|block` means `("ring", "fail-open")` — the phone must
  never miss a call because screening failed.
- Cold start: the `--service` process imports no Gtk/Adw and answers a
  first (activating) call in well under the timeout (measured ~65 ms
  warm-cache); no shell-side warm-up is needed.

### Verdict semantics

| Verdict | Shell behavior |
|---|---|
| `ring` | Normal incoming surface: ringer/vibration plus the answer/decline UI. |
| `silence` | No ringer and no vibration, but still show the incoming surface while it rings and then raise a missed-call notification carrying the `reason` string. |
| `block` | **The shell hangs up**: call `Hangup` on `org.freedesktop.ModemManager1.Call` for that call path, log the hangup (number + reason) in the shell log, and show no incoming UI at all. The dialer never hangs up incoming calls itself. |

The call may vanish out from under the shell (modem-side
disappearance); that path already exists for blocked calls that end
before the surface paints — tolerate it.

`reason` is a stable human-readable token from this vocabulary
(`policy.Reason` values plus fallbacks): `emergency callback`,
`send to voicemail`, `blocked`, `blocked by pattern`, `STIR failed`,
`starred`, `business`, `Business, outside 09–19`, `saved contact`,
`you called them Tue`, `silenced by pattern`, `unknown caller`,
`Unknown, outside 09–17`, `hidden caller`, `repeat caller`,
`rang · expecting a call`, `silenced`, `fail-open`. Treat it as opaque
display text, not a dispatch key: switch on `verdict` only.

## Silenced-call record cards: exactly one owner

The **dialer policy service** owns the post-hoc "Silenced call" /
"N silenced calls" record cards (batching, the three-state
`silenced_notif_policy`, the daily digest, and the Call back / Ring
next time / Block actions, ported from `ring/SilencedNotifier.kt` +
`ring/SilenceBatcher.kt`). The shell's `NoteCall` after an unanswered
silenced call is what triggers the card: the service matches the call
against the screen row its own `CheckNumber` wrote and posts the card
from the service process, with or without the GUI running. The GUI
never posts these cards (it only shows the Recents rows and badges).

Shell rule: when the consult **reached the service** (any verdict
came back, including fail-open-shaped `("ring", "fail-open")` from a
bad verdict — i.e. the service answered), the shell must NOT post its
own silenced notification. The service posts the record card when the
silenced call ends unanswered. The shell posts its own silenced card
(`call_wiring._post_silenced_missed`, the "Silenced: \<reason\>" card)
**only on fail-open paths** where the service was unreachable (error
or timeout, no verdict), so an unanswered silenced call still leaves
exactly one card instead of none. With the dialer open, the user gets
one card (from the service); with it closed, the batching still
happens (in the service). Duplicates mean the shell posted on a
consult that reached the service — that is the bug to fix shell-side.

## `NoteCall`

After **every** call ends (incoming and outgoing, answered or not),
report the outcome so the dialer's repeat-caller (15 min) and
recent-outgoing (48 h) state stays correct no matter which surface
placed or answered the call. Fire-and-forget. `NoteCall` for an
unanswered silenced incoming is additionally the trigger for the
service's silenced-record card above.

- Signature: `NoteCall(in s number, in s direction, in x started_ms,
  in x duration_ms, in s disposition)` — no return values.
- `direction` is `incoming` or `outgoing` (matched
  case-insensitively; anything else is rejected with
  `io.piercingxx.XXDialer.Policy1.InvalidArgument`).
- `started_ms` is wall-clock milliseconds (a value `<= 0` records as
  "now"); `duration_ms` is milliseconds (stored as whole seconds).
- `disposition` is `answered` for a call that connected; **any other
  string** (e.g. `missed`, `unanswered`, `voicemail`, `failed`) records
  unanswered and never raises. Allowed strings are therefore open-ended,
  but the shell should send `answered` or `missed`.

## `HandleNotificationAction`

Every silenced-call card the service posts carries the hint
`x-xx-wm-action-target` (string):

```
"io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy io.piercingxx.XXDialer.Policy1"
```

with actions `call-back:<e164>` / `ring-next-time:<e164>` /
`block-contact:<e164>` (labels Call back / Ring next time / Block;
batch cards carry no actions). When the user invokes one, the shell
emits `ActionInvoked` as usual **and** calls
`HandleNotificationAction(in u id, in s action_key)` on that target
with auto-start allowed (async, 5 s timeout, fire-and-forget), so the
bus daemon starts the service and it acts.

- Signature: `HandleNotificationAction(in u id, in s action_key)` —
  no return values.
- The service acts **only on ids it posted** (its `posted_notif`
  table, which survives idle-exit, else live memory); anything else
  is ignored. The number rides in the key, so Call back / Ring next
  time / Block all work after the service idle-exited and was
  re-activated by the tap.
- A live service sees both `ActionInvoked` and the forwarded call for
  one tap; it dedupes them, so Call back still places exactly one
  call.

## `VerdictDecided` signal

Emitted by the service for every screened incoming call so the shell
can update its surface without polling:

- Signal: `VerdictDecided(s e164, s verdict, s reason)` on the same
  object path and interface — normalized number, verdict
  (`ring|silence|block`), and the reason token above.
- Informational only: a second signal,
  `BypassChanged(b active, t expires_unix)`, tracks the
  Expecting-a-call bypass state for a future quick-settings tile.

## Dialer absent

If the name is neither owned nor activatable (dialer not installed),
behave exactly as the shell does today: own ringer, own starred list,
and no `NoteCall`. Installing the dialer must never make the phone
less able to ring.
