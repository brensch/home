# OPF / Rengstorff charging

Verified against OPF's live service on 2026-10-27 and installed in Home Assistant.
This is a personal-use, read-only integration. The accepted scope is ordinary
account authentication and app data requests; no endpoint enumeration,
security bypasses, charging commands, payments or changes to the OPF account.
Stop on access denials, login challenges or rate limits. The generic Wevo EULA
was not established to apply to OPF; OPF's app points to its own blank terms
page. No third-party client can promise how a provider will classify access.

## What is running

- Community Center, site **51057**: 9 chargers, aliases **099101–099109**.
- Aquatics Center, site **50447**: 4 chargers, aliases **990801–990804**.
- **099109** (Community Center) is the accessibility-designated bay, marked
  `"accessible": true` in `sites.json` (2026-10-28, per the user). It is left
  out of every ordinary count, total and alert, so Community Center counts
  **8**. It is tracked on its own: `sensor.opf_099109` (status) and
  `sensor.opf_accessible_free` (free count, every site's accessible bays, with
  the same `total` / `available_chargers` attributes and long-term statistics).
- Thirteen status sensors, two site free-count sensors, one combined count,
  one accessible count and one last-checked timestamp. All thirteen are
  Type 1 / J1772 connectors.
- Garage dashboard shows just two compact free-count tiles, one per location.
  Counts have numeric measurement metadata, unit `chargers`, and integer
  display precision, so HA plots numeric history rather than categorical states.
- **Watch for a charger · 30 sec** is a toggle above those counts. While on,
  status polling speeds up to 30 seconds. Newly free charger aliases at either
  site notify both Brendan (Schlixel) and Alyssa. A new opening is detected even
  if the total free count stays the same because another bay became occupied.
  Existing free bays do not generate an alert just from enabling the toggle;
  unchanged availability and recovery from unknown/unavailable do not alert.
  The toggle stays on until turned off, restores across HA restarts, and
  returns to 120-second polling when off. Error backoff and access stops still
  take precedence over watch mode. This watch is independent of the commute alert.
- Brendan's commute alert arms after a work visit of at least 30 minutes.
  Within four hours, when his phone reports him within **2 km of home**, it
  sends **one Schlixel push notification** with both counts and free aliases.
  Persistent helpers keep an armed commute across HA restarts. Returning to
  work or exceeding four hours clears it. The home uses metric units, so
  HA's `distance()` returns km. A phone location update is required; sparse
  GPS can delay the message until arrival.
- Data older than five minutes, unknown states or failed requests produce an
  explicit unknown-availability message. Offline, occupied, charging, idle,
  faulted and reserved connectors are never counted free. `AVAILABLE` alone
  counts as free; parking-bay obstruction is not reported by this API.

At initial verification, Community Center had 2/9 available (099102, 099109;
1/8 now that 099109 is counted separately),
Aquatics Center 1/4 (990804), with 990802 offline. These are historical test
observations, not promises of present availability.

## API mapping and evidence

OPF is Wevo-backed: Android package `energy.wevo.opf`, published by Wevo Energy.
The distributed OPF **1.4.99 / build 897** app was inspected locally, without
patching it, running instrumentation or intercepting TLS. Its resources link
`https://opf.energy/terms-conditions`, whose terms body was blank when checked.
The app's request formats were then verified with ordinary live requests.

The API base is `https://api.wevo.energy/mobileapp/rest`. Read requests use
Bearer access-token auth and `x-wevo-distributor: OPF`.

| Operation | Method and route | Body / parameters |
| --- | --- | --- |
| Normal login | Cognito `InitiateAuth`, eu-central-1 | `USER_PASSWORD_AUTH`, client `2amm11et52j39kubdekse641b6`, username **`opf/<trimmed email>`**, password |
| Refresh | Same Cognito endpoint | `REFRESH_TOKEN_AUTH`, same client, refresh token; no password |
| All visible map locations | POST `/public/charging/filtered-locations` | Filter JSON below; returned **66 locations** in this account at verification |
| Chargers at a location | POST `/public/charging/filtered-location/details/{buildingId}` | Same filters, query `isRoamingLocation=false`; includes real internal IDs, display aliases, connector numbers and statuses |
| Lightweight ongoing status | POST `/public/charging/connectors/status/by-location/{buildingId}` | No body; query `isRoaming=false`; list of `chargerIdentifier`, `connector`, `status` |

```json
{"availableOnly":false,"chargerSpeed":["SLOW","FAST","ULTRA_FAST"],"connectorTypes":["TYPE1","CCS1","NACS","NACS_AC","CHAdeMO"]}
```

`099102` is a display alias; its internal identifier is **1111118189**.
Querying `099102` as an internal ID gave a misleading `UNCOMMISSIONED`
response from the public scan-to-charge client. Do not infer identifiers from
printed numbers. An initial email-only Cognito login returned
`UserNotFoundException`; local inspection then established the exact OPF
prefix, and the correctly formatted normal login succeeded. No username
variants were guessed or iterated. No MFA, access-denial or rate-limit
challenge occurred in the verified OPF flow.

The live component uses **two read requests per 120 seconds**, or per **30
seconds while watching**, one per selected location, plus a token refresh
before expiry. Manual refreshes use the cached snapshot until the current
interval has elapsed. Connection/service
failures back off up to an hour. An auth failure, redirect, 4xx or rate limit
latches polling stopped, including across restarts. Review the account in the
official app before explicitly clearing the private session's `stopped` flag
or creating a new session. There is no automatic password retry.

## Files and credentials

`custom_components/opf_status/` is the versioned source; `install.sh` copies it
into HA's ignored runtime component directory. `sites.json` is verified public
inventory. Unknown/new sockets are not silently added to the tracked count;
rediscover and review inventory changes before updating that file.

The private session lives at
`data/homeassistant/.storage/opf_status_session.json` (mode 0600, ignored by
git). It contains access/refresh tokens and expiry, **no password**. Refreshes
are persisted atomically. Do not commit this file or print it. The password
provided in the conversation was used through a hidden terminal prompt and
was not saved to source or runtime configuration.

To establish a new session only when needed:

```sh
python3 config/opf/login_probe.py /tmp/opf-new-session.json
```

This makes one exact OPF login, with no retries or alternate usernames. After
manual verification, install that private session at the path above with mode
0600 and the HA user's ownership. Existing sessions refresh without a password.

To discover **all visible map locations**, or all chargers within the Rengstorff
area (default 600 m radius), using the same ordinary app requests:

```sh
docker exec homeassistant python /config/custom_components/opf_status/discover.py --all-locations
docker exec homeassistant python /config/custom_components/opf_status/discover.py
```

Use `--latitude`, `--longitude`, `--radius-km` to select another area. Discovery
resolves only sites returned by the map response, with a maximum of ten detail
requests per invocation. It does not probe station IDs. It is a manual tool,
not a recurring whole-network poll; the installed sensors query only the two
Rengstorff sites.

## Validation

```sh
python3 -m unittest discover -s tests -p test_opf_status.py
docker exec -i homeassistant python < tests/check_opf_templates.py
docker exec -i homeassistant python < tests/check_opf_watch.py
docker exec homeassistant python -m homeassistant --script check_config --config /config
```

Ten protocol tests use captured live response fixtures, including watch-mode
timing and preserving backoff across toggle changes. Isolated watch tests
exercise availability transitions and capture both notification actions locally
without sending phone pushes. Eight isolated HA
template checks cover near/far, commute expiry/disarming, missing GPS, fresh
availability, stale data and unavailable sensors. Live authentication, token
refresh, map lookup, site details and the installed HA sensors were verified.
A physical commute and actual phone push delivery are not simulated by these
tests. Heartbeat timestamps are excluded from Recorder; charger state changes
and free counts retain normal history.

The watch toggle was also clicked in the real dashboard at desktop width,
checked at phone width, and two consecutive scheduled watch updates were
observed 31 seconds apart. It was left switched off after verification.
