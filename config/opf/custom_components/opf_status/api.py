"""OPF read requests, verified with OPF 1.4.99 and live responses 2026-10-27."""
from datetime import datetime, timezone
import time
from time import monotonic

import aiohttp

BASE = "https://api.wevo.energy/mobileapp/rest/public/charging"
AUTH = "https://cognito-idp.eu-central-1.amazonaws.com/"
CLIENT_ID = "2amm11et52j39kubdekse641b6"
FILTERS = {"availableOnly": False, "chargerSpeed": ["SLOW", "FAST", "ULTRA_FAST"],
           "connectorTypes": ["TYPE1", "CCS1", "NACS", "NACS_AC", "CHAdeMO"]}
KNOWN = {"AVAILABLE", "CHARGING", "PREPARING", "FINISHING", "SUSPENDEDEV",
         "SUSPENDEDEVSE", "RESERVED", "FAULTED", "OFFLINE", "UNAVAILABLE",
         "BLOCKED", "EVCONNECTED", "IDLE", "OCCUPIED", "UNCOMMISSIONED"}


class Stopped(Exception):
    """Access/auth/rate-limit responses require manual review; no retry."""


def snapshot(rows, site):
    """Match internal IDs and connector numbers, never display aliases."""
    if not isinstance(rows, list):
        raise ValueError("Invalid OPF status response")
    expected = {(c["identifier"], str(c["connector"])): c for c in site["chargers"]}
    states = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid OPF connector")
        key = (str(row.get("chargerIdentifier")), str(row.get("connector")))
        if key not in expected:
            continue
        if key in states:
            raise ValueError("Duplicate OPF connector")
        raw = row.get("status")
        if not isinstance(raw, str):
            raise ValueError("Missing OPF status")
        normalized = raw.replace("_", "").upper()
        states[key] = normalized if normalized in KNOWN else "UNKNOWN"
    if states.keys() != expected.keys():
        raise ValueError("Incomplete OPF site snapshot")
    aliases = sorted(expected[k]["alias"] for k, v in states.items() if v == "AVAILABLE")
    return {"states": states, "free": len(aliases) if "UNKNOWN" not in states.values() else None,
            "total": len(expected), "available_aliases": aliases,
            "checked_at": datetime.now(timezone.utc).isoformat()}


class ReadOnlyClient:
    def __init__(self, session, tokens, sites, save_tokens):
        self.session, self.tokens, self.sites = session, tokens, sites
        self.save_tokens = save_tokens
        self.stopped = bool(tokens.get("stopped"))
        self.next_poll = 0
        self.backoff = 120
        self.cached = None
        self.poll_interval = 120
        self.last_poll_started = None

    def set_poll_interval(self, seconds):
        if seconds not in (30, 120):
            raise ValueError("OPF supports only 30 or 120 second polling")
        self.poll_interval = seconds
        # A mode change must not bypass an error backoff or access-stop latch.
        if self.cached is not None and self.last_poll_started is not None:
            self.next_poll = self.last_poll_started + seconds

    def stop(self):
        self.stopped = True
        raise Stopped("OPF polling stopped; review the official app before restarting")

    async def refresh(self):
        if self.stopped:
            raise Stopped("OPF polling stopped until manual review")
        async with self.session.post(
            AUTH, headers={"X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                           "Content-Type": "application/x-amz-json-1.1"},
            json={"AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": CLIENT_ID,
                  "AuthParameters": {"REFRESH_TOKEN": self.tokens["refresh_token"]}},
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status != 200:
                self.stop()
            try:
                data = await response.json(content_type=None)
                auth = data["AuthenticationResult"]
                if data.get("ChallengeName") or not auth.get("AccessToken"):
                    self.stop()
                tokens = {"access_token": auth["AccessToken"],
                          "refresh_token": auth.get("RefreshToken", self.tokens["refresh_token"]),
                          "expires_at": time.time() + int(auth["ExpiresIn"])}
            except (KeyError, ValueError, TypeError, AttributeError):
                self.stop()
        await self.save_tokens(tokens)
        self.tokens = tokens

    async def request(self, route, *, params=None, body=None):
        if self.stopped:
            raise Stopped("OPF polling stopped until manual review")
        # POST is used by the app for these read operations, never control.
        if route != "/filtered-locations" and not any(
            route == f"/{prefix}/{site['building_id']}"
            for site in self.sites
            for prefix in ("filtered-location/details", "connectors/status/by-location")
        ):
            raise ValueError("Not an allowed read-only OPF route")
        async with self.session.post(
            BASE + route, params=params, json=body,
            headers={"Authorization": "Bearer " + self.tokens["access_token"],
                     "x-wevo-distributor": "OPF"},
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if 300 <= response.status < 500:
                self.stop()
            if response.status != 200:
                raise ValueError("OPF service unavailable")
            return await response.json()

    async def poll(self):
        if self.stopped:
            raise Stopped("OPF polling stopped until manual review and restart")
        now = monotonic()
        if now < self.next_poll:
            if self.cached is not None:
                return self.cached
            raise ValueError("Waiting before next OPF poll")
        self.last_poll_started = now
        self.next_poll = now + self.poll_interval
        try:
            if time.time() >= self.tokens.get("expires_at", 0) - 120:
                await self.refresh()
            result = {}
            for site in self.sites:
                rows = await self.request(f"/connectors/status/by-location/{site['building_id']}",
                                          params={"isRoaming": "false"})
                result[site["building_id"]] = snapshot(rows, site)
            self.backoff = 120
            self.cached = result
            return result
        except Stopped:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, OSError):
            self.cached = None
            self.backoff = min(self.backoff * 2, 3600)
            self.next_poll = monotonic() + self.backoff
            raise ValueError("OPF snapshot unavailable; backing off") from None
