#!/usr/bin/env python3
"""Save lights' real state from the devices and put it back, including while off.

Home Assistant only knows "turn on (with a colour)" and "turn off", and it has
no colour for a light that's off. The devices themselves do: a Hue bulb or a
Nanoleaf panel remembers the colour and brightness it will come back on with.
So a flash that turns an off light on in some colour leaves it remembering that
colour. This talks to the devices directly to save that state beforehand and
write it back afterwards, with off lights staying off.

  light_memory.py save <name> <light entity_id>...
  light_memory.py off <light entity_id>... [--fade SECONDS]
  light_memory.py restore <name> [--fade SECONDS]

"off" fades to black on the device rather than through HA: HA's Hue
integration remembers the brightness whenever it turns a bulb off with a
transition and forces it on the next plain turn-on, which would override a
brightness restored here.

Run from HA as shell_command.light_memory (see configuration.yaml). Device
addresses and credentials come from HA's own registries under /config/.storage.

What the devices allow while staying off:
  Hue       colour and brightness: both kept (PUT without "on").
  Nanoleaf  colour only: setting it switches the panel on unless brightness 0
            is sent with it, and a panel at brightness 0 comes back at 100%.
"""

import json
import os
import ssl
import sys
import urllib.request

CONFIG = os.environ.get("HASS_CONFIG", "/config")
STATE_DIR = os.path.join(CONFIG, ".light_memory")
TIMEOUT = 5


def _http(method, url, body=None, headers=None, insecure=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    ctx = None
    if insecure:  # the Hue bridge serves a self-signed certificate
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


class Hue:
    """A Hue bulb, addressed through the bridge's CLIP v2 API."""

    def __init__(self, host, key, light_id, http=_http):
        self.url = f"https://{host}/clip/v2/resource/light/{light_id}"
        self.headers = {"hue-application-key": key}
        self.http = http

    def read(self):
        d = self.http("GET", self.url, headers=self.headers, insecure=True)["data"][0]
        state = {"on": d["on"]["on"], "brightness": d["dimming"]["brightness"]}
        mirek = (d.get("color_temperature") or {}).get("mirek")
        if mirek is not None:
            state["mirek"] = mirek
        elif "color" in d:
            state["xy"] = d["color"]["xy"]
        return state

    def body(self, saved, fade):
        b = {"dimming": {"brightness": saved["brightness"]}}
        if "mirek" in saved:
            b["color_temperature"] = {"mirek": saved["mirek"]}
        elif "xy" in saved:
            b["color"] = {"xy": saved["xy"]}
        if saved["on"]:
            b["on"] = {"on": True}
            b["dynamics"] = {"duration": int(fade * 1000)}
        else:
            # Without "on" the bridge applies colour and brightness and the bulb
            # stays dark; the explicit off guards against anything that lit it.
            b["on"] = {"on": False}
        return b

    def write(self, saved, fade):
        self.http("PUT", self.url, self.body(saved, fade), headers=self.headers, insecure=True)

    def off_body(self, fade):
        return {"on": {"on": False}, "dynamics": {"duration": int(fade * 1000)}}

    def off(self, fade):
        self.http("PUT", self.url, self.off_body(fade), headers=self.headers, insecure=True)


class Nanoleaf:
    """A Nanoleaf panel set, addressed through its local API."""

    def __init__(self, host, token, http=_http):
        self.base = f"http://{host}:16021/api/v1/{token}"
        self.http = http

    def read(self):
        s = self.http("GET", self.base + "/state")
        state = {"on": s["on"]["value"], "brightness": s["brightness"]["value"], "mode": s["colorMode"]}
        if s["colorMode"] == "ct":
            state["ct"] = s["ct"]["value"]
        elif s["colorMode"] == "effect":
            state["effect"] = self.http("GET", self.base + "/effects/select")
        else:
            state["hue"] = s["hue"]["value"]
            state["sat"] = s["sat"]["value"]
        return state

    def bodies(self, saved, fade):
        """The (path, body) PUTs that put a panel back as saved."""
        if saved["on"]:
            level = {"brightness": {"value": saved["brightness"], "duration": int(fade)}}
            if "effect" in saved:
                return [("/effects", {"select": saved["effect"]}), ("/state", level)]
            colour = {"ct": {"value": saved["ct"]}} if "ct" in saved else {
                "hue": {"value": saved["hue"]}, "sat": {"value": saved["sat"]}}
            # Come up from (nearly) dark in the saved colour, then fade to level.
            return [("/state", {**colour, "brightness": {"value": 1}}), ("/state", level)]
        # Off: a colour on its own switches the panel on; sent with brightness 0
        # in the same request it's stored and the panel stays dark.
        dark = {"brightness": {"value": 0}}
        if "effect" in saved:
            return [("/effects", {"select": saved["effect"]}), ("/state", dark)]
        colour = {"ct": {"value": saved["ct"]}} if "ct" in saved else {
            "hue": {"value": saved["hue"]}, "sat": {"value": saved["sat"]}}
        return [("/state", {**colour, **dark})]

    def write(self, saved, fade):
        for path, body in self.bodies(saved, fade):
            self.http("PUT", self.base + path, body)

    def off_body(self, fade):
        # Fading brightness to 0 ends with the panel off. It forgets its
        # brightness that way, which is fine: restore() sets it explicitly.
        return {"brightness": {"value": 0, "duration": int(fade)}}

    def off(self, fade):
        self.http("PUT", self.base + "/state", self.off_body(fade))


def devices(entity_ids, config=CONFIG, http=_http):
    """Map light entity_ids to device handles using HA's registries."""
    with open(os.path.join(config, ".storage", "core.entity_registry")) as f:
        ents = {e["entity_id"]: e for e in json.load(f)["data"]["entities"]}
    with open(os.path.join(config, ".storage", "core.config_entries")) as f:
        entries = {e["entry_id"]: e for e in json.load(f)["data"]["entries"]}
    out = {}
    for eid in entity_ids:
        ent = ents.get(eid)
        if ent is None:
            raise SystemExit(f"{eid}: not in the entity registry")
        entry = entries[ent["config_entry_id"]]["data"]
        if ent["platform"] == "hue":
            out[eid] = Hue(entry["host"], entry["api_key"], ent["unique_id"], http)
        elif ent["platform"] == "nanoleaf":
            out[eid] = Nanoleaf(entry["host"], entry["token"], http)
        else:
            raise SystemExit(f"{eid}: unsupported platform {ent['platform']}")
    return out


def save(name, entity_ids, config=CONFIG, http=_http):
    saved = {eid: dev.read() for eid, dev in devices(entity_ids, config, http).items()}
    os.makedirs(os.path.join(config, ".light_memory"), exist_ok=True)
    with open(os.path.join(config, ".light_memory", f"{name}.json"), "w") as f:
        json.dump(saved, f)
    return saved


def restore(name, fade=1.0, config=CONFIG, http=_http):
    with open(os.path.join(config, ".light_memory", f"{name}.json")) as f:
        saved = json.load(f)
    failed = []
    for eid, dev in devices(saved, config, http).items():
        try:
            dev.write(saved[eid], fade)
        except Exception as e:  # keep going so one dead bulb doesn't strand the rest
            failed.append(f"{eid}: {e}")
    if failed:
        raise SystemExit("restore failed for " + "; ".join(failed))
    return saved


def off(entity_ids, fade=1.0, config=CONFIG, http=_http):
    failed = []
    for eid, dev in devices(entity_ids, config, http).items():
        try:
            dev.off(fade)
        except Exception as e:
            failed.append(f"{eid}: {e}")
    if failed:
        raise SystemExit("off failed for " + "; ".join(failed))


def _split_fade(args):
    if "--fade" in args:
        i = args.index("--fade")
        return args[:i] + args[i + 2:], float(args[i + 1])
    return args, 1.0


def main(argv):
    if len(argv) >= 3 and argv[0] == "save":
        save(argv[1], argv[2:])
    elif len(argv) >= 2 and argv[0] == "off":
        ents, fade = _split_fade(argv[1:])
        off(ents, fade)
    elif len(argv) >= 2 and argv[0] == "restore":
        args, fade = _split_fade(argv[1:])
        restore(args[0], fade)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
