# Google relay

Google Home's smart home servers have to reach Home Assistant from the internet: every voice command arrives as a
fulfillment request (`POST /api/google_assistant`), and Google refreshes its login at `POST /auth/token`. Nothing
else at home is reachable from the internet (WireGuard on the router is the only way in), so this is a narrow,
separate path for those two requests and nothing more.

```
Google ──TLS──▶ schlift.com VM :443 ──(same TLS, unopened)──▶ VM 127.0.0.1:47443
                Caddy + caddy-l4, by SNI                        sshd: the house's tunnel listens here
                                                                    │
                                     ◀── SSH, dialled out by the house (no port open at home)
                                                                    │
                              google-tunnel ──internal network──▶ google-relay ──internal network──▶ homeassistant
                              (ssh client)                         TLS ends here; 2 requests allowed
```

## What each piece can do, if it's taken over

| Piece | Holds | Can reach |
|---|---|---|
| The VM | nothing of the house's: no certificate, no key that opens anything | `127.0.0.1:47443`, i.e. the relay's TLS port, the same as anyone on the internet |
| `harelay` on the VM | the tunnel key's public half | its key may only ask sshd to listen on `127.0.0.1:47443`: no shell, no command, no `-L`, no agent, no pty |
| google-tunnel | the tunnel's private key (`~/.config/google-relay/tunnel_key`) | the VM's port 22, and the relay's port 8443. Nothing else: `host/google-tunnel-egress` drops the rest in `DOCKER-USER`, and UFW drops anything to the host |
| google-relay | the certificate for `hass-google.brensch.com` (read-only) | Home Assistant's port 8123 only, on a network shared with nothing else; no internet |

The VM passes the TLS through unopened, so even with full control of it nobody sees Google's tokens or can
forge a request; they can only stop it working. TLS ends in the relay, which runs as uid 0 *only* because Caddy's
certificate files are root's and 0600: it has no capabilities, a read-only filesystem and nothing writable.

## What the relay lets through

Everything else gets a 404 before Home Assistant sees it: the login page, the API, websockets, the frontend.

- **`POST /api/google_assistant`**: JSON with a `requestId` and `inputs` naming the four smart home intents, and a
  bearer token. The body is parsed and re-encoded; only `Authorization` and `Content-Type` are passed on.
- **`POST /auth/token`**: a form with Google's client id for this project, and `grant_type` `authorization_code` or
  `refresh_token`. Home Assistant gets only those fields. Anything else (another client, `password`, revoke,
  duplicates, credentials in a header) is refused.
- The request target must be exactly one of those two paths (no query, no alternative encodings), with
  `Host: hass-google.brensch.com` and TLS for that name; HTTP/1.1 only.
- 64 KB per request, 120 requests a minute per address (10 to `/auth/token`), 64 connections at once. Every request
  is logged with its address (from the PROXY header) and nothing else; tokens never are.
- Home Assistant trusts the relay's `X-Forwarded-For` (trusted proxy `172.31.97.2/32`), so its own IP bans fall on
  the real address, not on the relay.

The account linking login is **not** exposed: its authorization URL is `https://hass.brensch.com/auth/authorize`,
which is only reachable at home or over WireGuard, so the phone doing the linking has to be on one of them.
Google links to the `Google` user (non-admin; login in `~/.config/google-relay/hass_google_login`).

### Google's address ranges

`make ranges` writes Google's own ranges minus Google Cloud's (`google-ranges.py`, from gstatic.com/ipranges) to
`data/google-relay/google-ranges.txt`, and the relay logs `google=true/false` for every request. Once the logs show
Google's real requests all come from inside them, set `RELAY_GOOGLE_RANGES_MODE: enforce` in the compose file and
everyone else is refused before anything is parsed. Enforcing needs the list kept fresh (a weekly `make ranges`);
a stale list could refuse Google.

## Setting it up

1. **DNS**: `hass-google.brensch.com` A record → `146.235.203.8`, not proxied by Cloudflare.
2. **Certificate**: home Caddy obtains it by DNS-01 (the `hass-google` site in `config/caddy/Caddyfile`, which
   only ever aborts).
3. **This machine**:
   ```
   cd config/google-relay
   make key               # the tunnel's SSH key
   make host              # egress rule for the tunnel's network + UFW denies for the relay's bridges
   make ranges
   cd ../.. && docker compose up -d google-relay google-tunnel homeassistant
   ```
   and add `172.31.97.2/32` to Home Assistant's trusted proxies (websocket `http/config/configure`, then
   `http/config/promote` once it's working).
4. **The VM** (as `opc`):
   ```
   make setup-vm-tunnel   # the harelay user and its sshd Match block
   make setup-vm-route    # Caddy with caddy-l4 and the passthrough; the relay is reachable from here on
   ```
5. **Google** (Google Home Developer Console, project `passenger-princess-protector`):
   - Fulfillment URL: `https://hass-google.brensch.com/api/google_assistant`
   - Account linking, OAuth authorization code: client id
     `https://oauth-redirect.googleusercontent.com/r/passenger-princess-protector`, any client secret, sent **in
     the request body** (the relay refuses it in a header); authorization URL `https://hass.brensch.com/auth/authorize`;
     token URL `https://hass-google.brensch.com/auth/token`.
   - Then link in the Google Home app (Add → Works with Google → the project), from a phone at home or on
     WireGuard, as the `google` user.

## Running it

- Logs: `docker logs google-relay` (each request, and why it was refused), `docker logs google-tunnel` (errors only).
- The tunnel reconnects by itself (the container restarts when ssh exits; sshd drops a dead one within 45 s).
- New key: `rm ~/.config/google-relay/tunnel_key*; make key setup-vm-tunnel; docker compose restart google-tunnel`.
- VM Caddy updates are manual now (the COPR package's `/usr/bin/caddy` is no longer the one running): bump the
  versions in `vm/Dockerfile`, then `make setup-vm-route`.
- Turning it off: `docker compose stop google-tunnel` closes the path at once. To remove it for good, also take the
  `import /etc/caddy/google-relay.global` line out of the VM's Caddyfile, delete
  `/etc/systemd/system/caddy.service.d/google-relay.conf`, the `harelay` user and the `# BEGIN google-relay` block at
  the end of the VM's `/etc/ssh/sshd_config`.
