# xx-share — LocalSend Protocol Port Spec (v2.2)

Source document: the public **LocalSend protocol README**
(`github.com/localsend/protocol`, "LocalSend Protocol v2.2"), cited
below as "protocol §X". Interop targets are the official
LocalSend apps (Android / iOS / Windows / macOS / Linux).

xx-share implements the v2.2 dialect. It does **not** implement the
legacy v1 API (`/api/localsend/v1/*`). Protocol `2.1` → `2.2` added
only the `422` checksum-mismatch reply on `POST upload` (protocol
CHANGELOG "2.2": "add `422` error code to `POST
/api/localsend/v2/upload` when the checksum (`sha256`) does not
match"; `2.1` had added the `?pin=` query parameter with `401`/`429`);
xx-share already implements `?pin=`/`401`/`429` and `422`, so it
advertises `"2.2"` and still accepts any `2.x` peer version.

## 1. Transport and identity

- Every device runs an **HTTPS server on TCP port 53317** with a
  **self-signed certificate**. There is no CA and no hostname check.
- A device's long-term identity is the **SHA-256 fingerprint of its
  certificate (DER bytes), lowercase hex**. The fingerprint is shown to the
  user and is the value compared when pairing / favoriting devices.
- TLS clients must **pin the expected fingerprint** (compare against the
  fingerprint learned via discovery) instead of trusting the system CA
  store. Certificates are accepted from any issuer and any subject; only
  the fingerprint matters.
- Every HTTPS client presents its own device certificate (mutual TLS):
  official LocalSend 1.18.x aborts the handshake without one
  (`TLSV13_ALERT_CERTIFICATE_REQUIRED`), so xx-share loads its cert/key
  into every client context (`info`, `register`, `prepare-upload`,
  `upload`, `cancel`).
- The server requests the sender certificate (`CERT_OPTIONAL`, no CA
  verification since certs are self-signed) and, for `register` and
  `prepare-upload`, compares `sha256(client_cert_der)` against the
  `fingerprint` the JSON body claims. On mismatch — or when no client
  cert is presented — the sender is treated as unknown/unverified:
  favorites never auto-accept and the prompt shows `(unverified)`.
- xx-share announces `version` `"2.2"` and `deviceType` `"mobile"`; its
  `fingerprint` is the SHA-256 of its serving certificate (protocol §2:
  "When encryption is on (HTTPS), then the fingerprint is the SHA-256
  hash of the certificate").
- All JSON bodies are UTF-8. All endpoints below are under the prefix
  `/api/localsend/v2`.

## 2. Discovery

### 2.1 UDP multicast (primary)

- Multicast group **`224.0.0.167`**, UDP port **`53317`**.
- A device joining the network (or becoming visible) multicasts an
  **announce message**; every visible receiver **responds** so the joiner
  learns the roster. Devices also re-announce periodically and when their
  alias changes.
- One JSON object per datagram, UTF-8, fields:

| Field | Type | Meaning |
|---|---|---|
| `alias` | string | User-visible device name |
| `version` | string | Protocol version implemented, e.g. `"2.2"` |
| `deviceModel` | string \| null | e.g. `"Fairphone 5"` |
| `deviceType` | string \| null | `mobile`, `desktop`, `web`, `headless`, `server` |
| `fingerprint` | string | Lowercase hex SHA-256 of the device certificate |
| `port` | number | HTTPS port (default `53317`) |
| `protocol` | string | `"https"` (plain `"http"` exists only for the debug simulator) |
| `download` | bool | Whether the device supports the pull (`download`) API |
| `announce` | bool | `true` = "I just arrived / please answer", `false` = response |

- Rule: on receipt of a message with `announce: true`, reply **first**
  with an HTTP `POST /api/localsend/v2/register` to the announcer (its
  source IP plus the `port`/`protocol` from the announcement), carrying
  your own device-info JSON; only if that HTTP call fails, fall back to
  the UDP reply (unicast to the sender) with your own info and
  `announce: false` (protocol §3.1: "First, an HTTP/TCP request is sent
  to the origin … As fallback, members can also respond with a
  Multicast/UDP message"). `announce: false` messages only populate the
  roster — never reply to them ("A response is only triggered when
  `announce` is `true`").
- Receivers must ignore malformed datagrams (bad JSON, missing `alias` /
  `fingerprint` / `port`) without logging noise on the UI.

### 2.2 HTTP register fallback

- `POST /api/localsend/v2/register` with the sender's device-info JSON
  (same fields as the multicast message minus `announce`) as the body.
- Used when UDP multicast is unavailable (guest Wi-Fi isolation, VPNs):
  the sender, knowing the receiver's IP (manual entry or subnet scan),
  registers directly; the receiver adds the sender to its roster.
- Response: `200` with the receiver's info JSON containing exactly
  `alias`, `version`, `deviceModel`, `deviceType`, `fingerprint`,
  `download` — no `port`/`protocol` (protocol §3.2 response shape).

### 2.3 Subnet scan

- When multicast yields nothing, the sender may probe its local /24(s):
  `GET /api/localsend/v2/info` against each candidate IP on port 53317
  with a short timeout. The info endpoint needs no authentication and
  returns the same device-info JSON as discovery.

## 3. Info endpoint

- `GET /api/localsend/v2/info` → `200`, device-info JSON (same shape as
  §2.1 minus `announce`). Used by scanners and by the receiver's
  favorites matching. No authentication.

## 4. Send flow (push; the common case)

Sender S, receiver R. S already knows R's IP, port and fingerprint from
discovery, and pins R's fingerprint for every TLS connection below.

1. **Prepare.** S sends `POST /api/localsend/v2/prepare-upload`
   (appending `?pin=<digits>` when R requires a PIN — the PIN travels in
   the query string, never in the JSON body, protocol §4.1: "If a PIN is
   required, the query parameter `?pin=123456` should be added"):

   ```json
   {
     "info": { "<sender device-info, same shape as discovery>" },
     "files": {
       "<fileId>": {
         "id": "<fileId>",
         "fileName": "photo.jpg",
         "size": 123456,
         "fileType": "image/jpeg",
         "sha256": null,
         "preview": null
       }
     }
   }
   ```

   - `fileId` is an opaque sender-chosen string, unique per session.
   - `fileType` is a MIME type string; xx-share sends the guessed MIME
     type with fallback `application/octet-stream`. `unknown` (older
     senders) is still accepted on receipt.
   - `sha256` / `preview` are optional (null when absent).
2. **R answers.** R holds this request open until the local user Accepts
   or Declines (120 s with no decision counts as a decline), then
   answers — the sender learns the decision from this response
   (protocol §4.1: "The receiver will decide if this request gets
   accepted, partially accepted or rejected"):
   success → `200` with `{ "sessionId": "<opaque>",
   "files": { "<fileId>": "<token>" } }` for the accepted subset only
   (partial accept allowed — every accepted file gets a **per-file
   token**); `204` = accepted but nothing to transfer;
   `403` = rejected/declined; `409` = blocked by another active session;
   `429` = too many requests; `401` = PIN required/invalid (§6).
3. **Upload.** For each accepted file, S sends
   `POST /api/localsend/v2/upload?sessionId=<s>&fileId=<f>&token=<t>`
   with the raw file bytes as the body (protocol §4.2). R answers `200`
   per file; when `prepare-upload` carried a `sha256` for the file, R
   verifies the received bytes and answers `422` on mismatch
   (protocol §4.2: "the receiver should verify the received data
   against it and respond with `422` on a mismatch").
   Official senders stream the body with `Transfer-Encoding: chunked`
   (no `Content-Length`); xx-share decodes chunked framing, enforcing
   the declared file size while streaming.
4. **Completion.** When all accepted files of the session are uploaded,
   the transfer is complete. Either side may then close local state.
5. **Cancel.** `POST /api/localsend/v2/cancel?sessionId=<s>` (no body,
   protocol §4.3) aborts a session; R deletes partial data, both sides
   reset UI state.

## 5. Receive-side accept prompt (xx-share behavior)

LocalSend's own apps show an accept dialog unless "quick save" is on.
xx-share maps this onto the phone as follows (the server is threaded,
so the held `prepare-upload` never blocks other requests):

- On `prepare-upload`, xx-share posts a desktop notification with
  **Accept / Decline** actions (`org.freedesktop.Notifications`) and
  **holds the request open** until the user acts or 120 s pass
  (timeout counts as a decline). The notification-action callback wakes
  the held request through a thread-safe event; only then is the
  `200` / `204` / `403` answer sent, so the sender always learns the
  decision from this response.
- Uploads start only after the `200`; bytes land in a **staging
  directory** (keyed by session id), never directly in Downloads. When
  every accepted file has arrived they move to `~/Downloads/xx-share/`
  (sanitized, collision-safe names). **Decline / cancel / timeout**
  deletes staged bytes; further `upload` calls for that session then get
  `403`, and uploads arriving before the decision get `403` too.
- Exception: sender on the user's **favorites list** AND the
  "auto-accept favorites" setting on → accept is implicit (no prompt;
  uploads then proceed and are delivered on completion).
- Rationale: holding the response preserves the "no file lands without
  explicit Accept" guarantee and matches official senders, which wait for
  this response before uploading.

## 6. PIN

- When R has "require PIN" enabled, `prepare-upload` without a PIN, or
  with a wrong PIN, fails with **`401 Unauthorized`** (protocol §4.1
  error table: "401 — PIN required / Invalid PIN").
- The PIN travels as the **`?pin=<digits>` query parameter**
  (protocol §4.1). The server reads it from the query string and still
  accepts a top-level `"pin"` body field leniently; the sender always
  uses the query string.
- The sending user types the PIN shown on R's screen; S retries the same
  `prepare-upload` with `?pin=` set.
- xx-share's PIN is a short numeric secret stored in its (0600) config;
  comparison is constant-time.

## 7. Errors

| Situation | Status |
|---|---|
| Accepted but nothing to transfer (`prepare-upload`) | `204` |
| Unknown `sessionId` | `404` |
| Wrong per-file `token`, session declined/cancelled, or upload before accept | `403` |
| Rejected/declined `prepare-upload` (or decision timeout) | `403` |
| Another session already active (`prepare-upload`) | `409` |
| Too many `prepare-upload` attempts | `429` |
| Missing/wrong PIN on `prepare-upload` | `401` |
| `sha256` declared in `prepare-upload` mismatches uploaded bytes | `422` |
| Total transfer size over the configured limit | `413` |
| Malformed JSON / missing fields | `400` |

## 8. What xx-share does NOT implement

- Legacy v1 endpoints (`/v1/register`, `/v1/send-request`, `/v1/send`,
  `/v1/show`, `/v1/cancel`, `/v1/info`, `/v1/download`).
- The `download` (pull) API: xx-share advertises `download: false`.
- Plain-HTTP mode: xx-share always serves and dials HTTPS.

## 9. Former uncertain points — resolved against the protocol document

Checked against the official protocol README
(`github.com/localsend/protocol`, "LocalSend Protocol v2.2"); each
point below is now implemented, tested, and no longer uncertain. (Live
interop with an official client on the LAN remains a useful
pre-v1.0 sanity check, but nothing below waits on it.)

1. **`announce` polarity — RESOLVED.** Protocol §3.1 confirms
   `announce: true` = "please answer" and that a response "is only
   triggered when `announce` is `true`", with the UDP fallback carrying
   `announce: false`. Implemented exactly that way (§2.1).
2. **PIN placement — RESOLVED.** Protocol §4.1 states: "If a PIN is
   required, the query parameter `?pin=123456` should be added" — the
   PIN is a query parameter on `prepare-upload`, not a JSON body field.
   The sender now uses `?pin=`; the server reads the query string (a
   body `"pin"` is still accepted leniently) and answers `401` for a
   missing/wrong PIN (§6).
3. **Deferred `prepare-upload` response — RESOLVED.** Protocol §4.1:
   "The receiver will decide if this request gets accepted, partially
   accepted or rejected" — the sender learns the decision from this
   response, so xx-share no longer answers `200` up front and stages.
   The request is held open until Accept/Decline (120 s timeout counts
   as a decline) and then answered with `200` (accepted subset only),
   `204` (nothing to transfer), `403` (rejected), `409` (another
   session active), or `429` (too many requests), matching the §4.1
   error table (§5).
4. **Decline signalling — RESOLVED.** Protocol §4.1 maps a refused
   prepare to `403` ("Rejected"), and §4.2 maps bad-token uploads to
   `403` ("Invalid token or IP address"). Decline/timeout therefore
   surfaces as `403` on `prepare-upload`, and post-decline `upload`
   calls also get `403`; the sender-initiated abort stays
   `POST /cancel?sessionId=` with no body (protocol §4.3) (§7).
5. **`register` response body — RESOLVED.** Protocol §3.2 shows the
   `register` response as exactly `alias`, `version`, `deviceModel`,
   `deviceType`, `fingerprint`, `download` — no `port`/`protocol`
   (those appear only in the request and the multicast messages).
   xx-share answers with exactly these six fields (§2.2).
6. **`fileType` vocabulary — RESOLVED.** The protocol examples (§4.1,
   §4.2, §5.2) always show MIME types such as `"image/jpeg"`.
   xx-share sends the guessed MIME type with fallback
   `"application/octet-stream"`; on receipt any non-empty string
   (including legacy `"unknown"`) is still accepted (§4).
7. **Multicast TTL / loopback — RESOLVED (by design).** Protocol §1
   fixes the defaults (group `224.0.0.167`, port `53317` for both UDP
   and HTTP), and §3.1 orders discovery as HTTP-`register`-first with
   the UDP `announce: false` reply only as fallback — so even where an
   AP eats multicast, the unicast HTTP reply plus the §2.2/§2.3
   register fallback and subnet scan carry discovery (§2.1).
