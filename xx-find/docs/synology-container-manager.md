# xx-find on Synology Container Manager (built, never running)

Nothing here starts anything by itself. The Dockerfile is a build
recipe and `server/docker-compose.yml` is a static recipe: the owner
applies both by hand in Container Manager. The agent stays disabled
until the owner enables and pairs it in Settings.

## 1. Build the image

1. Copy this repo's `server/Dockerfile`, `server/docker-compose.yml`,
   `src/xx_find/`, and the two launchers onto the NAS (any file copy
   the owner already uses; this repo ships no copy automation).
2. In Container Manager, create a Project from the folder holding the
   compose file. Building produces a local image only.

## 2. Volume layout

| Mount | Inside the container | Holds |
|---|---|---|
| `xx-find-data` (named volume) | `/home/xxfind/data/` | `store.json` (devices, ciphertext reports, command queue, owner password hash, sessions) |

`store.json` is written atomically (tmp + fsync + rename) with mode
`0600`; its parent is created mode `0700`. Back up the
`xx-find-data` volume with the NAS backup task like any other app
data. The store holds ciphertext reports only: location plaintext
never reaches the NAS (it decrypts in the owner's browser, lane 6 UI,
or with `xx-find-decrypt`).

## 3. First-run setup (one-off, inside the running container)

The server has no default password. With the container started, open a
terminal on it (Container Manager > container > Terminal) and run:

```sh
python3 - <<'EOF'
from xx_find.server import Store
store = Store("/home/xxfind/data/store.json")
store.set_owner_password("a long passphrase the owner picks")
print("pairing token:", store.pair_device("phone-1"))
EOF
```

The pairing token prints once: enter it on the phone during Settings
pairing together with the server URL and owner verify key. Rotate it
by calling `pair_device` again (the old token hash is replaced).

## 4. Reachability over the tailnet (no public port)

- The compose file publishes `8080` bound to the NAS **Tailscale
  address only** (edit `100.64.0.10` to the NAS tailnet address from
  the Tailscale admin panel). Nothing listens on the LAN or the
  public interface.
- Install the Tailscale package on the NAS and sign it into the
  owner's tailnet first; the container reaches `100.x` peers through
  the host route, bridge mode needs no extra flag.
- Optional reverse proxy (DSM Control Panel > Login Portal >
  Advanced > Reverse Proxy): source `https://nas.<tailnet-name>`
  with the tailnet TLS certificate, destination
  `http://127.0.0.1:8080`. Plain HTTP between proxy and container
  stays on the NAS itself. The phone polls the tailnet address
  directly; it never needs the proxy name.

## 5. Login protection

Owner login (`POST /login`) and command submission (`POST
/v1/command`) are rate limited per IP and per account/device (5 login
attempts per 15 min, 10 command posts per minute; `429` past that).
A correct password always succeeds and clears the caller's counters.
Pairing/device endpoints need the per-device token and fail closed
to `401`.
