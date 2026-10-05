#!/usr/bin/env python3
"""End-to-end test of script.flash_lights on the real lights.

Sets fan front (Hue), Lines Kitchen and Lines Desk (Nanoleaf) and Brightboy
(LIFX) to distinctive looks, runs the flash with them all on, all off and mixed, and checks the result
on the devices themselves (not HA's view): lights that were on are back as they
were, lights that were off stay off without a blink and come back on in their
own colour rather than the flash colour. Puts the lights back as it found them at the end.

The lights visibly change for about a minute.

  python3 tests/flash_e2e.py
"""

import http.client
import json
import os
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "data", "homeassistant")
sys.path.insert(0, os.path.join(CONFIG, "tools"))
import light_memory as lm  # noqa: E402

HA = "http://localhost:8123"
TOKEN = open(os.path.expanduser("~/.config/hass/token")).read().strip()
FAN, LK, LD, BB = "light.fan_front", "light.lines_kitchen", "light.lines_desk", "light.brightboy1"
LIGHTS = [FAN, LK, LD, BB]
# Distinct from each other and from the flash colour, one per colour mode.
LOOKS = {
    FAN: {"brightness": 60.0, "mirek": 300},
    LK: {"brightness": 70, "mode": "hs", "hue": 300, "sat": 60},
    LD: {"brightness": 50, "mode": "ct", "ct": 3000},
    BB: {"hue": 0, "sat": 0, "bri": 45875, "kelvin": 3200},  # LIFX raw units: 70%, warm white
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


TOL = {"brightness": 3, "mirek": 3, "hue": 2, "sat": 2, "ct": 60, "bri": 700, "kelvin": 60}


def colour_keys(eid):
    return [k for k in ("mirek", "hue", "sat", "ct", "kelvin") if k in LOOKS[eid]]


def level_keys(eid, after_off=False):
    """Brightness keys to check. A Nanoleaf can't keep its brightness while off."""
    keys = [k for k in ("brightness", "bri") if k in LOOKS[eid]]
    return [k for k in keys if not (after_off and isinstance(dev()[eid], lm.Nanoleaf))]


class Watch:
    """Reads a light's on/brightness every 20-40 ms, to catch blinks that a
    before/after comparison can't see. Nanoleaf over HTTP, LIFX over UDP."""

    def __init__(self, device):
        self.device = device
        self.readings, self.stop = [], threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        if isinstance(self.device, lm.Lifx):
            bulb = lm.Lifx(self.device.transport.host, self.device.transport.target[:6].hex(":"))
            while not self.stop.is_set():
                try:
                    st = bulb.read()
                    self.readings.append((st["on"], st["bri"] * 100 // 65535))
                except OSError:
                    pass
                time.sleep(0.02)
            return
        host = self.device.base.split("/")[2]
        path = "/" + self.device.base.split("/", 3)[3] + "/state"
        c = http.client.HTTPConnection(host, timeout=2)
        while not self.stop.is_set():
            try:
                c.request("GET", path)
                s = json.loads(c.getresponse().read())
                self.readings.append((s["on"]["value"], s["brightness"]["value"]))
            except Exception:
                c = http.client.HTTPConnection(host, timeout=2)
            time.sleep(0.02)

    def lit_after_fading_out(self):
        """On-readings brighter than 1 after the flash has faded to (near) black."""
        self.stop.set()
        r = self.readings
        peak = max((i for i, (o, b) in enumerate(r) if o and b >= 90), default=None)
        if peak is None:
            return None
        faded = next((i for i in range(peak, len(r)) if not r[i][0] or r[i][1] <= 1), None)
        return [] if faded is None else [x for x in r[faded:] if x[0] and x[1] > 1]


def scenario(name, on, together=False):
    print(f"\n== {name}: " + ", ".join(f"{e.split('.')[1]} {'on' if on[e] else 'off'}" for e in LIGHTS))
    set_look(on)
    d = dev()
    watches = {e: Watch(d[e]) for e in LIGHTS if not on[e] and isinstance(d[e], (lm.Nanoleaf, lm.Lifx))}
    if not together:
        ha("script/flash_lights", {"lights": LIGHTS, "color": FLASH, "hold": 1})
    else:
        # Both arrive at once: two flashes requested in the same instant. The
        # script is queued, so the second must start only after the first has
        # finished (call returns ~a whole flash apart), not run over it.
        t0, done = time.monotonic(), {}

        def call(colour):
            ha("script/flash_lights", {"lights": LIGHTS, "color": colour, "hold": 1})
            done[tuple(colour)] = time.monotonic() - t0

        threads = [threading.Thread(target=call, args=(c,)) for c in (FLASH, [255, 110, 0])]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        first, second = sorted(done.values())
        ok = second - first >= 5
        print(f"    {'PASS' if ok else 'FAIL'} flashes took turns: finished at {first:.1f}s and {second:.1f}s")
        if not ok:
            failures.append(f"flashes overlapped: finished at {first:.1f}s and {second:.1f}s")
    time.sleep(3)  # restore fade
    for eid, w in watches.items():
        lit = w.lit_after_fading_out()
        ok = lit == []
        print(f"    {'PASS' if ok else 'FAIL'} {eid.split('.')[1]} no blink after fading out: "
              + ("none" if ok else f"{len(lit or [])} lit readings, e.g. {(lit or ['no flash seen'])[:3]}"))
        if not ok:
            failures.append(f"{eid} blinked after fading out: {(lit or ['no flash seen'])[:3]}")
    d = dev()
    for eid in LIGHTS:
        got = d[eid].read()
        label = eid.split(".")[1]
        if on[eid]:
            check(label, got, dict(LOOKS[eid], on=True), ["on"] + level_keys(eid) + colour_keys(eid), TOL)
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
            keys = ["on"] + level_keys(eid, after_off=True) + colour_keys(eid)
            check(label, got, dict(LOOKS[eid], on=True), keys, TOL)


def main():
    lm.save("e2e_backup", LIGHTS, config=CONFIG)
    try:
        scenario("A all on", {FAN: True, LK: True, LD: True, BB: True})
        scenario("B all off", {FAN: False, LK: False, LD: False, BB: False})
        scenario("C mixed", {FAN: True, LK: False, LD: True, BB: False})
        scenario("D both arrive at once, mixed", {FAN: True, LK: False, LD: True, BB: False}, together=True)
    finally:
        lm.restore("e2e_backup", fade=1, config=CONFIG)
        print("\nlights put back as they were before the test")
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
