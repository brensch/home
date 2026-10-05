#!/usr/bin/env python3
"""Screenshot dashboards at phone size (390x844, dark) in headless Chromium.

Signs in with the long-lived token in ~/.config/hass/token. Runs in the
Playwright container so nothing is installed on the host:

  docker run --rm --network host -e T="$(cat ~/.config/hass/token)" \\
    -v "$PWD/tests/shots:/out" -v "$PWD/tests/dashboard_shots.py:/shoot.py:ro" \\
    mcr.microsoft.com/playwright/python:v1.55.0-noble \\
    sh -c 'pip install -q playwright==1.55.0; python /shoot.py home-dash/home rooms-dash/rooms'

Writes /out/<dashboard>_<view>.png and prints any console errors (a broken
card usually shows up there).
"""

import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

token = os.environ["T"]
pages = sys.argv[1:] or ["home-dash/home"]
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark")
    # The frontend reads its auth from localStorage; a long-lived token works as-is.
    ctx.add_init_script("""localStorage.setItem('hassTokens', JSON.stringify({access_token: %s, token_type: 'Bearer',
        expires_in: 1800, hassUrl: 'http://localhost:8123', clientId: 'http://localhost:8123/',
        expires: Date.now() + 365*24*3600*1000, refresh_token: ''}));""" % json.dumps(token))
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for path in pages:
        page.goto(f"http://localhost:8123/{path}", wait_until="networkidle")
        time.sleep(4)  # let cards render after the websocket connects
        name = path.replace("/", "_")
        page.screenshot(path=f"/out/{name}.png", full_page=True)
        print("shot", name)
    print("console errors:", errors or "none")
    browser.close()
