"""One explicit normal login, with no retries or alternate usernames.

Password is read from the terminal, never argv, files or log output. The
session output file must be outside the repository and is mode 0600.
"""
import getpass
import json
import os
from pathlib import Path
import sys
import time

import requests


def main():
    destination = Path(sys.argv[1]).resolve()
    if destination.is_relative_to(Path(__file__).resolve().parents[2]):
        raise SystemExit("Session file must be outside the repository")
    if destination.exists():
        raise SystemExit("Choose a new session filename before logging in")
    email = input("OPF account email: ").strip()
    # OPF 1.4.99: Cognito.getAuthUsername = lower(WhitelabelClient.name)
    # + '/' + trimmed email. OPF's WhitelabelClient.name is 'OPF'.
    username = "opf/" + email
    password = getpass.getpass("Password (one attempt, no fallback): ")
    session = requests.Session()
    response = session.post(
        "https://cognito-idp.eu-central-1.amazonaws.com/",
        headers={"X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                 "Content-Type": "application/x-amz-json-1.1"},
        json={"AuthFlow": "USER_PASSWORD_AUTH", "ClientId": "2amm11et52j39kubdekse641b6",
              "AuthParameters": {"USERNAME": username, "PASSWORD": password}},
        timeout=20, allow_redirects=False,
    )
    del password
    data = response.json()
    if response.status_code != 200 or data.get("ChallengeName"):
        kind = str(data.get("__type", data.get("ChallengeName", "unknown"))).split("#")[-1]
        print(f"STOPPED: HTTP {response.status_code}; {kind}. No retry performed.")
        return 1
    auth = data.get("AuthenticationResult", {})
    if not auth.get("AccessToken") or not auth.get("RefreshToken"):
        print("STOPPED: unexpected login response; no retry performed.")
        return 1
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump({"access_token": auth["AccessToken"], "refresh_token": auth["RefreshToken"],
                   "expires_at": time.time() + auth.get("ExpiresIn", 3600)}, output)
    print("Normal login succeeded; session stored privately. No charger actions performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
