# OAuth setup (bring your own client ID)

XX Email talks **directly** to Google from your device. There is no
developer middleman server — which also means there is no pre-baked
credential. You create your own OAuth client ID once (~5 minutes);
your quota, your project, your control.

## Why this design?

- Google requires a per-app OAuth client for the Gmail API.
- If we shipped ONE central client, we would need Google's
  "restricted scope" verification, including an annual third-party
  security assessment (CASA) — impractical for a free open-source
  phone app, and it would put a company in between you and your mail.
- With your own client ID, tokens are issued to *your* project;
  nobody else can query them.

## What to create: a Desktop-type client

The Linux app receives Google's answer on a **loopback address**
(`http://127.0.0.1:<port>/oauth2redirect`, with a free port picked
automatically at sign-in time), so you need a **Desktop app**
OAuth client — Desktop clients accept any loopback port without
further registration. Do NOT use the Android type: the Android
custom scheme (`dev.xxemail:/oauth2redirect`) has no reliable
per-app claim on a Linux phone.

## Steps

1. Go to <https://console.cloud.google.com/> and create a project
   (any name, e.g. `xx-email`).
2. **APIs & Services → Library → search "Gmail API" → Enable.**
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - App name: anything; add your own account as a **test user**, OR
     publish the app ("In production") without verification to avoid
     7-day token expiry (you'll see a one-time "unverified app"
     warning — expected for self-registered open-source apps).
4. **APIs & Services → Credentials → Create credentials →
   OAuth client ID**:
   - Application type: **Desktop app** (this is the loopback-capable
     type the Linux app needs — do NOT pick Android or Web)
   - Name: anything, e.g. `xx-email-linux`.
   - No redirect URI needs typing: Desktop clients accept
     `http://127.0.0.1:<port>` loopback redirects automatically.
   - No client secret is needed: the app proves itself with PKCE,
     so the secret field (if shown) stays empty.
5. Copy the **Client ID** (looks like
   `1234567890-abc123.apps.googleusercontent.com`) and paste it into
   XX Email's setup screen.
6. Tap **Sign in with Google**: the consent page opens in your
   browser. Approve it and the browser tab shows "Signed in; you can
   close this tab." The app then adds the account and syncs your
   mail.

## Notes & gotchas

- While your consent screen is in **Testing** status, refresh tokens
  expire after **7 days** (Google policy for restricted scopes).
  You'll be asked to sign in weekly — flip the consent screen to
  production to stop this.
- If tokens expire or get revoked, the mailbox shows a
  "Sign in again" banner; signing in again replaces the tokens for
  that account without losing any cached mail.
- The app requests ONLY: `openid email profile` +
  `https://www.googleapis.com/auth/gmail.modify`.
  It cannot permanently delete mail (Google enforces this
  server-side for that scope).
- Tokens live in your login keyring (Secret Service), never in a
  file. If the keyring is locked, the setup screen says
  "Unlock your keyring to continue" — unlock it and tap Retry.
- To revoke access at any time:
  <https://myaccount.google.com/permissions>
  (the app's Settings page links there too). Signing out inside the
  app optionally revokes the server grant as well.

## Troubleshooting

- The browser tab shows an error instead of "Signed in": check the
  setup screen — the app keeps waiting for the real answer, so just
  complete the consent step again.
- Nothing opened in a browser: the app shows the sign-in address it
  tried to open — paste it into any browser on the phone manually.
- "Authorization failed: access_denied": you declined consent; start
  over and approve it.
- The port is always chosen fresh per attempt, so a busy port only
  costs you a retry.
