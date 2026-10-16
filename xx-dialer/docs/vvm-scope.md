# Visual voicemail lane (scoped, not built)

Carrier visual voicemail is deferred (PORT_SPEC §2.9): dial-in
voicemail (long-press 1 on the keypad) already works — it is just
placing a call. What remains is a whole lane by itself:

1. **STATUS-SMS capture** — watch `org.freedesktop.ModemManager1`
   `Modem.Messaging`, filter carrier STATUS SMSes (`VvmSmsParser`
   port), never the user SMS store (Chatty/TxxT owns SMS).
2. **IMAP fetch** — opt-in carrier-mailbox client (`VvmImapClient`
   port), host taken from a live-SIM STATUS transcript only; no
   greeting/PIN/transcription cloud. Upstream the Android client
   itself still awaits that transcript (todo.md V0), so the lane is
   gated on it.
3. **Credential store** — mailbox secrets in libsecret (`Secret` over
   GI), never config.json.
4. **Inbox UI** — carrier inbox + player page; the Rules-screen
   voicemail row stays hidden until this lane lands.

The lane needs mobile-data IMAP plus SMS-filter plumbing and its own
dogfood (message-waiting latency, credential survival across
updates). Nothing about the deferral affects the ringer: silence,
batching, and the notification actions in this lane work with
dial-in voicemail only.
