# Local router access

`https://router.brensch.com` proxies the UniFi console at `https://192.168.1.1`.

- UniFi persistent DNS record: `router.brensch.com` → `192.168.1.8`, A, TTL 60. Clients must use the LAN DNS resolver.
- The Caddy route allows only direct peers in `192.168.1.0/24` and loopback; all other peers receive HTTP 403. Forwarded headers do not grant access.
- Cloudflare has a TXT record at `router.brensch.com` to shadow the existing public wildcard. There is no public A/AAAA record for this name.
- Let’s Encrypt certificates are automatically issued and renewed with DNS-01 through the Cloudflare module. The API token remains in the existing untracked `.env`, passed to Caddy at runtime.
- Caddy's Dockerfile pins Caddy 2.11.4 and Cloudflare module 0.2.4. Rebuild Caddy when updating those versions.
- Upstream TLS uses the console's existing self-signed certificate; browser TLS terminates at Caddy with the public certificate.

Validated trusted TLS, internal DNS, login and authenticated Network API through the proxy, public DNS with no A/AAAA answers, HTTP 403 for non-LAN peers including spoofed forwarded headers, and continued Home Assistant access.
