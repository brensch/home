# Home Automation Stack (Config-as-Code + DR)

This repository contains declarative configuration plus scripts to provision a Home Assistant + Mosquitto + MariaDB stack after a disaster recovery (DR) event.

## Goals
- Clone + decrypt + up = running stack.
- Version only human-authored configuration under `configs/`.
- Keep secrets encrypted with SOPS until provision time.

## Directory Layout
| Path | Purpose |
|------|---------|
| `docker-compose.yaml` | Service definitions (Home Assistant, Mosquitto, MariaDB) |
| `configs/homeassistant/` | HA YAML config (automations, scenes, scripts, configuration.yaml, secrets.yaml) |
| `configs/mosquitto/mosquitto.conf` | Broker config (static) |
| `dr_provision.sh` | Automated DR script (decrypt + bootstrap + start) |
| `.sops.yaml` | SOPS encryption rules |
| `.env` | (Encrypted) infrastructure secrets (DB passwords, TZ) |

## SOPS Overview
SOPS (Secrets OPerationS) encrypts selected files while keeping the repo diff‑friendly. Only specified key names/paths are encrypted; the rest stays plaintext for context.

### Age Key Generation
```bash
age-keygen -o age.key
# Show public key (line starting with age1...)
grep '^#' -v age.key | grep '^age1'
```
Set environment variable for SOPS:
```bash
export SOPS_AGE_KEY_FILE=$PWD/age.key
```

### Configure `.sops.yaml`
Replace `PLACEHOLDER_AGE_RECIPIENT_KEY` with your public key in `.sops.yaml`.

### Encrypt Files
```bash
sops -e -i .env
sops -e -i configs/homeassistant/secrets.yaml
```
Encrypted files keep structure; decrypted values only available locally when you have `age.key`.

## Disaster Recovery (DR) Restore
1. Clone repo:
   ```bash
   git clone <repo> home && cd home
   ```
2. Obtain `age.key` (secure channel) and place it at repo root, then:
   ```bash
   export SOPS_AGE_KEY_FILE=$PWD/age.key
   ```
3. Run provisioning script:
   ```bash
   ./dr_provision.sh
   ```
4. Access Home Assistant at: `http://<host>:8123`.
5. Re-pair integrations that live in HA `.storage` (not versioned). If you decide you want to encrypt and commit `.storage/`, extend `.sops.yaml` accordingly.

## Adding / Rotating Secrets
Edit the decrypted file, then re‑encrypt:
```bash
sops -d .env > /tmp/env.clear && $EDITOR /tmp/env.clear && mv /tmp/env.clear .env && sops -e -i .env
```
(SOPS will refuse to encrypt if already encrypted; decrypt first.)

## Recorder Database
- MariaDB credentials are taken from (encrypted) `.env` (`DB_ROOT_PASSWORD`, `DB_PASSWORD`).
- Recorder URL stored in `configs/homeassistant/secrets.yaml` under key `mysql_recorder_url`.
- To rotate password: decrypt both files, change, re-encrypt, restart stack.

## Common Commands
Validate compose file:
```bash
docker compose config
```
Decrypt temporarily (DO NOT COMMIT decrypted versions):
```bash
sops -d configs/homeassistant/secrets.yaml | head
```

## Extending Encryption
To also encrypt entire secrets file or more keys, adjust `.sops.yaml` `encrypted_regex` or add more creation_rules.

Example (encrypt everything in secrets.yaml):
```yaml
creation_rules:
  - path_regex: configs/homeassistant/secrets.yaml$
    age: <public-age-key>
```

## Optional: Encrypt `.storage`
If you want zero re‑pairing after DR, add a rule for `configs/homeassistant/.storage/` and start committing those files (contains tokens—treat carefully).

## Security Notes
- Keep `age.key` OFF the host unless needed—load it, decrypt, start, remove if desired.
- Never commit decrypted secrets (CI can verify by grepping for plain passwords).

## Next Enhancements (suggested)
- CI pipeline: verify compose syntax, ensure secrets still encrypted.
- Automated MariaDB backup + encryption.
- Sanitized export of HA entity registry for documentation.

---
Need help extending encryption to `.storage` or adding CI checks? Open an issue or ask.

## Exposing Home Assistant over HTTPS (Caddy)

This stack now includes an optional Caddy reverse proxy that terminates TLS (Let's Encrypt) and forwards to the internal Home Assistant container.

Steps:
1. Create a public DNS A (and AAAA if IPv6) record pointing `example.com` to your WAN IP.
2. Edit `config/caddy/Caddyfile` and replace `example.com` with your domain.
3. (Optional) Put an email in a global block for ACME if you want notifications:
   ````
   {
     email you@example.com
   }
   ````
4. Open/forward ports 80 and 443 from your router to this host (same IP that runs Docker).
5. Start / restart the stack:
   ```bash
   docker compose up -d caddy
   ```
6. Watch logs to confirm cert issuance:
   ```bash
   docker logs -f caddy | grep -i "certificate obtained"
   ```
7. Access HA at: `https://example.com` (port 8123 no longer needed externally).

Security notes:
- The HA `http:` config now trusts Docker bridge subnets; tighten to the actual network CIDR if you customize networks.
- Keep port 8123 un-forwarded on the router; only 80/443 should be exposed.
- Consider adding IP filtering or GeoIP at Caddy if desired.

Renewals are automatic; certificates are stored in the named volume `caddy_data`.
