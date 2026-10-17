# D-Bus registry

Every bus name in XX-Linux-Phone, who owns it, and who calls it. Rules that apply to all of them:

- A background service never owns the same name as a GTK app's `application_id` (the GUI would think it's a second instance and exit).
- Services that own data are the **single writer**; GUIs may read their own SQLite files directly, but every write goes through the service so it can emit `Changed`.
- Idle-exiting services ship a session activation file (`share/dbus-1/services/<Name>.service`); clients call methods directly with auto-start and never gate on `NameHasOwner`.
- Notifications whose actions must work after the poster exits carry the `x-xx-wm-action-target` hint (see [xx-wm/docs/notifications.md](../xx-wm/docs/notifications.md)); the target only acts on notification ids it posted.

## Services (session bus unless noted)

| Bus name | Object path | Interface | Owner (process) | Activation | Main callers |
|---|---|---|---|---|---|
| `io.piercingxx.Contacts` | `/io/piercingxx/Contacts` | `io.piercingxx.Contacts1` | `xx-contacts --service` | activatable, idle exit | shell, dialer, TxxT, email, xx-wm Medical ID |
| `io.piercingxx.XXDialer.Policy` | `/io/piercingxx/XXDialer/Policy` | `io.piercingxx.XXDialer.Policy1` | `xx-dialer --service` | activatable, idle exit | shell (`CheckNumber` before ringing, `NoteCall` after) |
| `io.piercingxx.TxxT.Daemon` | `/io/piercingxx/TxxT/Daemon` | `io.piercingxx.TxxT1` | `txxt --daemon` (owns SMS/MMS) | activatable + autostart | TxxT UI (`Send`, `SendMms`, `MarkRead`, `MessagesChanged`); shell defers SMS while it's owned |
| `io.piercingxx.Calendar` | `/io/piercingxx/Calendar` | `io.piercingxx.Calendar1` | `xx-calendar --service` | activatable, idle exit | calendar UI, reminders, shell shade |
| `io.piercingxx.XXEmail.Daemon` | (actions only) | | `xx-email --daemon-tick` (systemd timer) | timer | notification actions |
| `io.piercingxx.XXShare1` | `/io/piercingxx/XXShare` | `io.piercingxx.XXShare1` | `xx-share --service` | systemd user unit | xx-share UI |
| `io.piercingxx.Captions1` | `/io/piercingxx/Captions1` | `io.piercingxx.Captions1` | `python -m xx_captions --serve` | activatable | caption overlay, shell Captions tile |
| `io.piercingxx.XXWM.Helper1` (**system bus**) | `/io/piercingxx/XXWM/Helper1` | `io.piercingxx.XXWM.Helper1` | root helper (polkit) | system activation | shell (charge limit, per-app network, freeze) |

## `io.piercingxx.Contacts1` methods (email lookup)

- `LookupEmail(in s email)` → `(s uid, s display_name)`: exact,
  case-insensitive address match, `("", "")` on a miss (never an
  error). Email clients resolve senders through this, never through
  `Search`, whose digit matching would misattribute addresses such as
  `john1@example.com` to `+1…` phone contacts.
- `Search(in s query, in u limit)` → `(a(sss) results)` of
  `(uid, display_name, primary)`: `primary` is the contact's primary
  email when it has one, else the primary phone. Number matching is
  skipped for `@` queries and queries with fewer than 3 digits.
- Publishing to GNOME Contacts mirrors only into the dedicated
  `XX Contacts` EDS source, never the default address book.

## GUI application ids (never owned by a service)

`io.piercingxx.XXWM` (shell), `io.piercingxx.XXContacts`, `io.piercingxx.XXDialer`, `io.piercingxx.TxxT`, `io.piercingxx.XXCalendar`, `io.piercingxx.XXEmail`, `io.piercingxx.XXVitals`, `io.piercingxx.XXShare`, `io.piercingxx.Captions` (overlay), `io.piercingxx.Find` (agent; built, not enabled).

## External services the suite uses

ModemManager (system), mmsd-tng (session, `org.ofono.mms`), feedbackd (`org.sigxcpu.Feedback`), callaudiod (`org.mobian_project.CallAudio`), iio-sensor-proxy, GeoClue2, UPower, logind, BlueZ, NetworkManager, xdg-desktop-portal, PipeWire.
