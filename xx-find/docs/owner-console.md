# xx-find owner console (lane 6 UI + decrypt helper)

The server at `/` serves a single-page owner console (login, device
list, last-location map, command composer). It is plain HTML plus the
embedded `src/xx_find/owner.js` crypto: no map library, no third-party
scripts, OpenStreetMap tiles only.

## 1. What runs where

- The NAS **never sees plaintext**. The device list and the last
  report arrive as ciphertext (`/api/devices`, `/api/report`); the
  browser decrypts with the owner's box secret key and positions an
  OpenStreetMap tile `<img>` with a marker overlay.
- Commands are **signed in the browser**: canonical JSON
  (`sort_keys`, no whitespace, ASCII-escaped, the lane 2 contract) is
  Ed25519-signed with the owner's signing seed, then posted to
  `/v1/command`. The JavaScript reproduces the `CANONICAL_VECTORS`
  bytes and signatures exactly; `tests/test_lane6.py` replays those
  vectors through the shipped file under node/deno/qjs when one is
  installed and skips otherwise.
- Keys stay in the browser's form fields: the password, box secret
  key, and signing seed are never sent to the server. Sign out clears
  the session token; closing the tab drops the keys from memory.

## 2. xx-find-decrypt (local helper)

Same decrypt without a browser:

```sh
xx-find-decrypt --secret-key-file owner-box-secret.hex \
  --store store.json --device phone-1
```

Sources: `--box` (base64), `--box-file`, `--report-file` (envelope
JSON), or `--store` with `--device`. Prints the plaintext JSON to
stdout; exit nonzero with the reason on stderr. Only the owner can
open a box; tampered boxes are refused.

## 3. API surface (all JSON except `/`)

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /`, `GET /devices` | session cookie-free page | console shell (login form + app) |
| `POST /login` | password | returns `{"session": token}`; per-IP + per-account limited |
| `GET /api/devices` | `x-session` | device list, last-report time, counters, queue depth |
| `GET /api/report?device=` | `x-session` | last **ciphertext** envelope (or null) |
| `POST /v1/command` | `x-session` | queue a signed envelope; per-IP + per-device limited |
| `POST /v1/report` | device token | phone report intake (ciphertext only) |
| `GET /v1/commands` | device token | phone command poll |

Login and command posts past their budgets get `429`; device
endpoints fail closed to `401`.
