# Secrets & Environment Configuration

This project splits runtime credentials between:
- `.env` (passed into Docker containers as environment variables) – infrastructure-level passwords.
- `configs/homeassistant/secrets.yaml` – Home Assistant application‑level secrets consumed via `!secret` in YAML.

## Files
| File | Purpose | Commit? |
|------|---------|---------|
| `.env` | Environment variables for Compose (DB passwords, timezone) | Template with placeholders only |
| `configs/homeassistant/secrets.yaml` | HA secrets referenced by `!secret` (e.g., `mysql_recorder_url`) | Keep placeholders / encrypt real values |

## Flow
1. Docker Compose reads `.env` and substitutes variables into service environment sections (e.g. `MYSQL_ROOT_PASSWORD`).
2. Home Assistant, on startup, loads `secrets.yaml` and replaces `!secret key` entries in its YAML configuration with the values.
3. Recorder uses the resolved `mysql_recorder_url` to connect to MariaDB.

## Setting Real Credentials
Edit `.env`:
```
TZ=Europe/London
DB_ROOT_PASSWORD=your-strong-root-pass
DB_PASSWORD=your-strong-user-pass
```
Edit `configs/homeassistant/secrets.yaml`:
```
mysql_recorder_url: mysql://ha:your-strong-user-pass@mariadb:3306/homeassistant?charset=utf8mb4
```
(Ensure the password matches DB_PASSWORD; user `ha` is created automatically.)

Restart stack:
```
docker compose up -d --pull always
```

## Keeping Secrets Out of Git
Add (or ensure) these lines in `.gitignore` if committing real secrets:
```
.env
configs/homeassistant/secrets.yaml
```
Currently `.env` is ignored; adjust for secrets.yaml as needed.

## Optional: SOPS Encryption
1. Install `sops` and create an age key (`age-keygen -o key.txt`).
2. Create `.sops.yaml`:
```
creation_rules:
  - path_regex: configs/homeassistant/secrets.yaml
    age: AGE_PUBLIC_KEY_HERE
```
3. Encrypt: `sops -e -i configs/homeassistant/secrets.yaml`
4. Decrypt locally on restore: `sops -d -i configs/homeassistant/secrets.yaml`

## Rotating the DB Password
1. Stop HA & MariaDB: `docker compose stop homeassistant mariadb`.
2. Change `DB_PASSWORD` & `mysql_recorder_url` (with new pass) in respective files.
3. Remove MariaDB volume only if you accept data loss: `docker volume rm home_mariadb_data` (replace prefix accordingly) or leave as-is and manually ALTER USER inside DB.
4. Start services: `docker compose up -d`.

## Troubleshooting Recorder
| Symptom | Cause | Fix |
|---------|-------|-----|
| Startup error: auth failed | Password mismatch | Align `.env` and `secrets.yaml` URL | 
| HA falls back to SQLite | Invalid `db_url` | Correct URL & restart |
| Slow history | DB growing large | Tune purge_keep_days or migrate to external host |

## Example Minimal secrets.yaml
```
mysql_recorder_url: mysql://ha:changeme-user@mariadb:3306/homeassistant?charset=utf8mb4
```

That’s it—update these two files and redeploy for a restored environment.
