#!/usr/bin/env python3
"""End-to-end test of script.flash_lights on the real lights.

Sets fan front (Hue), Lines Kitchen and Lines Desk (Nanoleaf) to distinctive
looks, runs the flash with them all on, all off and mixed, and checks the result
on the devices themselves (not HA's view): lights that were on are back as they
were, lights that were off stay off and come back on in their own colour rather
than the flash colour. Puts the lights back as it found them at the end.

The lights visibly change for about a minute.

  python3 tests/flash_e2e.py
"""

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "data", "homeassistant")
sys.path.insert(0, os.path.join(CONFIG, "tools"))
import light_memory as lm  # noqa: E402

HA = "http://localhost:8123"
TOKEN = open(os.path.expanduser("~/.config/hass/token")).read().strip()
FAN, LK, LD = "light.fan_front", "light.lines_kitchen", "light.lines_desk"
LIGHTS = [FAN, LK, LD]
# Distinct from each other and from the flash colour, one per colour mode.
LOOKS = {
    FAN: {"brightness": 60.0, "mirek": 300},
    LK: {"brightness": 70, "mode": "hs", "hue": 300, "sat": 60},
    LD: {"brightness": 50, "mode": "ct", "ct": 3000},
}
FLASH = [0, 255, 200]

failures = []


def ha(service, data):
    req = urllib.request.Request(f"{HA}/api/services/{service}", data=json.dumps(data).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()


def dev():
    return lm.devices(LIGHTS, config=CONFIG)


def set_look(on):
    """Put each light in its test look, on or off, straight on the device."""
    d = dev()
    for eid in LIGHTS:
        look = dict(LOOKS[eid], on=True)
        d[eid].write(look, 0)
        time.sleep(0.5)
        if not on.get(eid, True):
            # A plain off: no transition, so Nanoleaf keeps its brightness.
            ha("light/turn_off", {"entity_id": eid})
    time.sleep(2)


def check(label, got, want, keys, tol):
    for k in keys:
        g, w = got.get(k), want.get(k)
        ok = g == w if not isinstance(w, (int, float)) or isinstance(w, bool) else (g is not None and abs(g - w) <= tol.get(k, 0))
        print(f"    {'PASS' if ok else 'FAIL'} {label} {k}: got {g}, want {w}")
        if not ok:
            failures.append(f"{label} {k}: got {g}, want {w}")


TOL = {"brightness": 3, "mirek": 3, "hue": 2, "sat": 2, "ct": 60}


def colour_keys(eid):
    return [k for k in ("mirek", "hue", "sat", "ct") if k in LOOKS[eid]]


def scenario(name, on):
    print(f"\n== {name}: " + ", ".join(f"{e.split('.')[1]} {'on' if on[e] else 'off'}" for e in LIGHTS))
    set_look(on)
    ha("script/flash_lights", {"lights": LIGHTS, "color": FLASH, "hold": 1})
    time.sleep(3)  # restore fade
    d = dev()
    for eid in LIGHTS:
        got = d[eid].read()
        label = eid.split(".")[1]
        if on[eid]:
            check(label, got, dict(LOOKS[eid], on=True), ["on", "brightness"] + colour_keys(eid), TOL)
        else:
            check(label, got, {"on": False}, ["on"], TOL)
    # Now turn the ones that were off on the ordinary way: they should come up
    # in their own colour, not the flash colour.
    off = [e for e in LIGHTS if not on[e]]
    if off:
        ha("light/turn_on", {"entity_id": off})
        time.sleep(2)
        d = dev()
        for eid in off:
            got = d[eid].read()
            label = eid.split(".")[1] + " (turned on after)"
            keys = ["on"] + colour_keys(eid)
            if eid == FAN:  # Hue keeps brightness while off; Nanoleaf can't
                keys.append("brightness")
            check(label, got, dict(LOOKS[eid], on=True), keys, TOL)


def main():
    lm.save("e2e_backup", LIGHTS, config=CONFIG)
    try:
        scenario("A all on", {FAN: True, LK: True, LD: True})
        scenario("B all off", {FAN: False, LK: False, LD: False})
        scenario("C mixed", {FAN: True, LK: False, LD: True})
    finally:
        lm.restore("e2e_backup", fade=1, config=CONFIG)
        print("\nlights put back as they were before the test")
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
