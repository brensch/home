#!/usr/bin/env python3
"""How in-sync do the lights actually change? Measured from what they report.

Listens to the Hue bridge's and each Nanoleaf's event streams, sends an off or
on command to fan front, Lines Kitchen and Lines Desk, and times command ->
reported change for each light, plus the spread between the first and last.

What "reported" means: Nanoleaf panels are asked for their state every ~25 ms
(their event stream batches on a timer, so it can't time anything); the Hue
bridge's event stream reports once it has sent the change over Zigbee. Neither
is a light sensor, but both are much closer to the truth than HA's view.

Compares sending to the lights one after another, all at once (light_memory),
and through HA. The lights blink for a minute or two; they're put back after.

  python3 tests/sync_probe.py [rounds]
"""

import http.client
import json
import os
import ssl
import statistics
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "data", "homeassistant")
sys.path.insert(0, os.path.join(CONFIG, "tools"))
import light_memory as lm  # noqa: E402

TOKEN = open(os.path.expanduser("~/.config/hass/token")).read().strip()
LIGHTS = ["light.fan_front", "light.lines_kitchen", "light.lines_desk"]
LOOK = {"light.fan_front": {"on": True, "brightness": 80.0, "mirek": 300},
        "light.lines_kitchen": {"on": True, "brightness": 80, "mode": "ct", "ct": 3300},
        "light.lines_desk": {"on": True, "brightness": 80, "mode": "ct", "ct": 3300}}

events = []  # (monotonic time, entity_id, on)
lock = threading.Lock()


def record(eid, on):
    with lock:
        events.append((time.monotonic(), eid, on))


def hue_stream(dev, eid_by_id):
    host = dev.url.split("/")[2]
    conn = http.client.HTTPSConnection(host, context=ssl._create_unverified_context(), timeout=None)
    conn.request("GET", "/eventstream/clip/v2", headers={**dev.headers, "Accept": "text/event-stream"})
    resp = conn.getresponse()
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        for ev in json.loads(line[5:]):
            for d in ev.get("data", []):
                if d.get("id") in eid_by_id and "on" in d:
                    record(eid_by_id[d["id"]], d["on"]["on"])


def nano_poll(dev, eid):
    # Nanoleaf's event stream batches reports on a timer per panel (a near
    # constant ~0.5 s offset between the two Lines), so it can't time a change.
    # Asking the panel every ~25 ms over one kept-alive connection can.
    host = dev.base.split("/")[2]
    path = "/" + dev.base.split("/", 3)[3] + "/state/on"
    conn = http.client.HTTPConnection(host, timeout=2)
    last = None
    while True:
        try:
            conn.request("GET", path)
            on = json.loads(conn.getresponse().read())["value"]
        except Exception:
            conn = http.client.HTTPConnection(host, timeout=2)
            continue
        if on != last:
            record(eid, on)
            last = on
        time.sleep(0.025)


def ha_turn(service, ents):
    req = urllib.request.Request(f"http://localhost:8123/api/services/light/{service}", method="POST",
                                 data=json.dumps({"entity_id": ents}).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def trial(action, want_on):
    with lock:
        events.clear()
    t0 = time.monotonic()
    action()
    time.sleep(3)
    with lock:
        first = {}
        for t, eid, on in events:
            if on == want_on and eid not in first:
                first[eid] = (t - t0) * 1000
    return first


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    devs = lm.devices(LIGHTS, config=CONFIG)
    lm.save("probe_backup", LIGHTS, config=CONFIG)
    hue = {eid: d for eid, d in devs.items() if isinstance(d, lm.Hue)}
    threading.Thread(target=hue_stream, args=(next(iter(hue.values())), {d.url.rsplit("/", 1)[1]: e for e, d in hue.items()}), daemon=True).start()
    for eid, d in devs.items():
        if isinstance(d, lm.Nanoleaf):
            threading.Thread(target=nano_poll, args=(d, eid), daemon=True).start()
    time.sleep(2)

    def put_on_all(parallel):
        if parallel:
            lm._each(devs, lambda eid, d: d.write(LOOK[eid], 0))
        else:
            for eid, d in devs.items():
                d.write(LOOK[eid], 0)

    def put_off_all(parallel):
        if parallel:
            lm._each(devs, lambda eid, d: d.off(0))
        else:
            for d in devs.values():
                d.off(0)

    modes = {
        "one after another": (lambda: put_off_all(False), lambda: put_on_all(False)),
        "all at once (light_memory)": (lambda: put_off_all(True), lambda: put_on_all(True)),
        "through HA": (lambda: ha_turn("turn_off", LIGHTS), lambda: ha_turn("turn_on", LIGHTS)),
    }
    results = {m: {"off": [], "on": []} for m in modes}
    try:
        put_on_all(True)
        time.sleep(3)
        for r in range(rounds):
            for m, (do_off, do_on) in modes.items():
                results[m]["off"].append(trial(do_off, False))
                results[m]["on"].append(trial(do_on, True))
                print(f"round {r + 1} {m}: off {fmt(results[m]['off'][-1])} | on {fmt(results[m]['on'][-1])}", flush=True)
    finally:
        lm.restore("probe_backup", fade=1, config=CONFIG)

    print("\nmedian ms from command to reported change, and spread (last - first light)")
    for m in modes:
        for kind in ("off", "on"):
            trials = [t for t in results[m][kind] if len(t) == len(LIGHTS)]
            per = {e.split(".")[1]: statistics.median(t[e] for t in trials) for e in LIGHTS} if trials else {}
            spread = statistics.median(max(t.values()) - min(t.values()) for t in trials) if trials else float("nan")
            missing = len(results[m][kind]) - len(trials)
            print(f"  {m:28s} {kind:3s}  " + "  ".join(f"{k} {v:5.0f}" for k, v in per.items())
                  + f"   spread {spread:5.0f}" + (f"   ({missing} trials missing a report)" if missing else ""))


def fmt(first):
    return " ".join(f"{e.split('.')[1][:5]}={v:.0f}" for e, v in sorted(first.items())) or "no reports"


if __name__ == "__main__":
    main()
