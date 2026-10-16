# io.piercingxx.Contacts — consumer contract (this app)

Per PORT_SPEC review note 5, xx-contacts owns the contacts and serves
them on the session bus; there is no sidecar file. This app's
`star_overlay` (stars), `tier_member` (biz + Blocked rows), and
`blocked_number` (exact numbers) tables are a warm cache kept by
`contacts_client.ContactsClient`, and the `contact_mirror` rows behind
the Policy1 `LookupNumber` feed come from the same source.

Bus name `io.piercingxx.Contacts`, object `/io/piercingxx/Contacts`,
interface `io.piercingxx.Contacts1`:

| Method | Signature | Notes |
|---|---|---|
| `LookupNumber` | `(s) -> (s uid, s display_name, as tags)` | `s` is a phone number; unknown numbers answer `("", "", [])` |
| `Search` | `(s query, u limit) -> a(sss)` | rows are `(uid, display_name, number)` with the first TEL as number |
| `Get` | `(s uid) -> (s vcard)` | vCard text; `FN` names, `TEL` numbers, first wins |
| `List` | `(t since_revision) -> (t revision, as changed, as deleted)` | `changed` holds new + updated uids; full warm is `List(0)` |
| `SetTags` | `(s uid, as tags)` | replaces the set; unknown tags are ignored |

Signal `Changed(t revision, as uids)` is a hint only — the client
always re-reads `List(since_revision)` so coalesced signals stay exact.

Tags are exactly `starred` / `business` / `blocked` (lowercase).
Reads tolerate case, whitespace, and duplicates; writes send the
sorted valid set.

## Assumed semantics (xx-contacts tree not in this repo)

`Search` triples and the empty-`LookupNumber` shape above are not
pinned by any tree under `ref/`; they are this lane's reading of the
task text, implemented identically by the fake bus in
`tests/test_contacts_client.py`. If the xx-contacts lane defines them
differently, only `parse`/row-ordering needs to change — the warming
discipline below does not.

## Cache mapping

| Service | SQLite cache |
|---|---|
| uid tagged `starred` | `star_overlay(uid)` + mirror `starred` flag |
| uid tagged `business` | `tier_member(uid, 'biz')` |
| uid tagged `blocked` | `tier_member(uid, 'Blocked')` + `blocked_number(e164)` per mirror number |
| `Get` vCard | `contact_mirror` rows `(uid, e164, displayName)` |

Custom `tier_member` groups (e.g. `family`) are never touched, and a
`blocked_number` row claimed by another still-Blocked contact is never
removed.

## Offline rule

Without the service the local tables are authoritative. Edits made
while the service is absent are recorded in the `pending_tag_edit`
table (uid, exact tag set, timestamp) and pushed once with `SetTags`
on the absent -> present transition, then cleared — only edits
recorded while absent are ever pushed. Refreshes otherwise never push:
when the service is present it is the single source of truth and every
refresh overwrites the local cache from `List`/`Get` (tags removed
upstream disappear locally, including the star overlay). Dialer edits
send the exact intended set (`SetTags` replaces the flag set) without
merging cache state.
