"""Unit tests for light_memory.py, against fake devices (no network).

  python3 -m unittest discover -s data/homeassistant/tools -v
"""

import json
import os
import struct
import tempfile
import unittest

import light_memory as lm


class FakeHttp:
    """Records requests and answers GETs from a dict of url -> response."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, body=None, headers=None, insecure=False):
        self.calls.append((method, url, body))
        if method == "GET":
            return self.responses[url]
        return None

    def puts(self):
        return [(url, body) for method, url, body in self.calls if method == "PUT"]


HUE_URL = "https://bridge/clip/v2/resource/light/abc"
NANO = "http://panel:16021/api/v1/tok"


def hue_resp(on, bri, mirek=None, xy=None):
    d = {"on": {"on": on}, "dimming": {"brightness": bri},
         "color_temperature": {"mirek": mirek}, "color": {"xy": xy or {"x": 0.3, "y": 0.3}}}
    return {"data": [d]}


def nano_resp(on, bri, mode, hue=0, sat=0, ct=4000):
    return {"on": {"value": on}, "brightness": {"value": bri}, "colorMode": mode,
            "hue": {"value": hue}, "sat": {"value": sat}, "ct": {"value": ct}}


class HueTests(unittest.TestCase):
    def test_reads_colour_temperature_when_set(self):
        http = FakeHttp({HUE_URL: hue_resp(True, 80.0, mirek=366)})
        self.assertEqual(lm.Hue("bridge", "k", "abc", http).read(),
                         {"on": True, "brightness": 80.0, "mirek": 366})

    def test_reads_xy_when_no_colour_temperature(self):
        http = FakeHttp({HUE_URL: hue_resp(False, 40.0, xy={"x": 0.17, "y": 0.7})})
        self.assertEqual(lm.Hue("bridge", "k", "abc", http).read(),
                         {"on": False, "brightness": 40.0, "xy": {"x": 0.17, "y": 0.7}})

    def test_restore_on_fades_up(self):
        b = lm.Hue("bridge", "k", "abc", FakeHttp({})).body({"on": True, "brightness": 80.0, "mirek": 366}, 1.5)
        self.assertEqual(b, {"dimming": {"brightness": 80.0}, "color_temperature": {"mirek": 366},
                             "on": {"on": True}, "dynamics": {"duration": 1500}})

    def test_restore_off_sets_colour_and_stays_off(self):
        b = lm.Hue("bridge", "k", "abc", FakeHttp({})).body(
            {"on": False, "brightness": 40.0, "xy": {"x": 0.17, "y": 0.7}}, 1)
        self.assertEqual(b["on"], {"on": False})
        self.assertEqual(b["color"], {"xy": {"x": 0.17, "y": 0.7}})
        self.assertEqual(b["dimming"], {"brightness": 40.0})
        self.assertNotIn("dynamics", b)


class ParallelTests(unittest.TestCase):
    def test_each_runs_lights_concurrently(self):
        import threading
        barrier = threading.Barrier(3, timeout=2)  # only passes if all 3 run at once
        results, failed = lm._each({"a": 1, "b": 2, "c": 3}, lambda eid, dev: (barrier.wait(), dev)[1])
        self.assertEqual((results, failed), ({"a": 1, "b": 2, "c": 3}, []))

    def test_each_collects_failures_without_stopping(self):
        def fn(eid, dev):
            if eid == "b":
                raise OSError("offline")
            return dev
        results, failed = lm._each({"a": 1, "b": 2}, fn)
        self.assertEqual(results, {"a": 1})
        self.assertEqual(failed, ["b: offline"])


class OffTests(unittest.TestCase):
    def test_hue_off_fades_on_the_bridge(self):
        self.assertEqual(lm.Hue("bridge", "k", "abc", FakeHttp({})).off_body(1),
                         {"on": {"on": False}, "dynamics": {"duration": 1000}})

    def test_nanoleaf_off_fades_to_black_but_stays_on(self):
        self.assertEqual(lm.Nanoleaf("panel", "tok", FakeHttp({})).off_body(1),
                         {"brightness": {"value": 1, "duration": 1}})

    def test_fade_argument_parsing(self):
        self.assertEqual(lm._split_fade(["light.a", "light.b", "--fade", "2"]), (["light.a", "light.b"], 2.0))
        self.assertEqual(lm._split_fade(["light.a"]), (["light.a"], 1.0))


class NanoleafTests(unittest.TestCase):
    def test_reads_hs(self):
        http = FakeHttp({NANO + "/state": nano_resp(True, 70, "hs", hue=300, sat=60)})
        self.assertEqual(lm.Nanoleaf("panel", "tok", http).read(),
                         {"on": True, "brightness": 70, "mode": "hs", "hue": 300, "sat": 60})

    def test_reads_ct(self):
        http = FakeHttp({NANO + "/state": nano_resp(False, 50, "ct", ct=3000)})
        self.assertEqual(lm.Nanoleaf("panel", "tok", http).read(),
                         {"on": False, "brightness": 50, "mode": "ct", "ct": 3000})

    def test_reads_effect(self):
        http = FakeHttp({NANO + "/state": nano_resp(True, 90, "effect"), NANO + "/effects/select": "Northern Lights"})
        self.assertEqual(lm.Nanoleaf("panel", "tok", http).read()["effect"], "Northern Lights")

    def test_restore_on_comes_up_from_dark_then_fades(self):
        bodies = lm.Nanoleaf("panel", "tok", FakeHttp({})).bodies(
            {"on": True, "brightness": 70, "mode": "hs", "hue": 300, "sat": 60}, 1)
        self.assertEqual(bodies, [
            ("/state", {"hue": {"value": 300}, "sat": {"value": 60}, "brightness": {"value": 1}}),
            ("/state", {"brightness": {"value": 70, "duration": 1}}),
        ])

    def test_restore_on_when_already_lit_fades_from_where_it_is(self):
        # No detour via brightness 1: that dips a lit panel visibly.
        bodies = lm.Nanoleaf("panel", "tok", FakeHttp({})).bodies(
            {"on": True, "brightness": 70, "mode": "hs", "hue": 300, "sat": 60}, 1, on_now=True)
        self.assertEqual(bodies, [("/state", {"hue": {"value": 300}, "sat": {"value": 60}}),
                                  ("/state", {"brightness": {"value": 70, "duration": 1}})])

    def test_restore_off_sets_colour_while_black_then_switches_off(self):
        # A colour sent to a fully-off panel lights it at full brightness first.
        bodies = lm.Nanoleaf("panel", "tok", FakeHttp({})).bodies(
            {"on": False, "brightness": 50, "mode": "ct", "ct": 3000}, 1)
        self.assertEqual(bodies, [("/state", {"brightness": {"value": 1}}),
                                  ("/state", {"ct": {"value": 3000}}),
                                  ("/state", {"brightness": {"value": 0}})])

    def test_restore_off_effect(self):
        bodies = lm.Nanoleaf("panel", "tok", FakeHttp({})).bodies(
            {"on": False, "brightness": 50, "mode": "effect", "effect": "Snowfall"}, 1)
        self.assertEqual(bodies, [("/state", {"brightness": {"value": 1}}), ("/effects", {"select": "Snowfall"}),
                                  ("/state", {"brightness": {"value": 0}})])


class FakeLifx:
    """Stands in for LifxTransport: records requests, answers GetColor."""

    def __init__(self, state=(0, 0, 0, 3500, 0)):
        hue, sat, bri, kelvin, power = state
        self.state = struct.pack("<HHHHhH32sQ", hue, sat, bri, kelvin, 0, power, b"bulb", 0)
        self.sent = []

    def prepare(self):
        pass

    def request(self, msg_type, payload=b"", reply=None):
        self.sent.append((msg_type, payload))
        return self.state if msg_type == lm.Lifx.GET_COLOR else b""


class LifxTests(unittest.TestCase):
    def test_header_is_36_bytes_addressed_to_the_bulb(self):
        t = lm.LifxTransport("1.2.3.4", "d0:73:d5:81:5c:c8")
        pkt = t.packet(101, res=True)
        self.assertEqual(len(pkt), 36)
        size, proto, _ = struct.unpack_from("<HHI", pkt, 0)
        self.assertEqual((size, proto & 0xFFF, bool(proto & 0x1000), bool(proto & 0x2000)), (36, 1024, True, False))
        self.assertEqual(pkt[8:16], bytes.fromhex("d073d5815cc8") + b"\0\0")
        self.assertEqual(pkt[22] & 1, 1)                       # res_required
        self.assertEqual(struct.unpack_from("<H", pkt, 32)[0], 101)

    def test_reads_colour_and_power(self):
        bulb = lm.Lifx("h", "s", FakeLifx((1000, 2000, 30000, 3500, 65535)))
        self.assertEqual(bulb.read(), {"on": True, "hue": 1000, "sat": 2000, "bri": 30000, "kelvin": 3500})

    def test_restore_on_sets_colour_then_fades_power_up(self):
        fake = FakeLifx()
        lm.Lifx("h", "s", fake).write({"on": True, "hue": 1, "sat": 2, "bri": 3, "kelvin": 4000}, 1.5)
        self.assertEqual(fake.sent, [(102, struct.pack("<BHHHHI", 0, 1, 2, 3, 4000, 0)),
                                     (117, struct.pack("<HI", 65535, 1500))])

    def test_restore_off_sets_colour_and_keeps_power_off(self):
        fake = FakeLifx()
        lm.Lifx("h", "s", fake).write({"on": False, "hue": 1, "sat": 2, "bri": 3, "kelvin": 4000}, 1)
        self.assertEqual(fake.sent[0][0], 102)
        self.assertEqual(fake.sent[1], (117, struct.pack("<HI", 0, 0)))

    def test_off_fades_power(self):
        fake = FakeLifx()
        lm.Lifx("h", "s", fake).off(1)
        self.assertEqual(fake.sent, [(117, struct.pack("<HI", 0, 1000))])


class RoundTripTests(unittest.TestCase):
    """save() then restore() through HA-style registry files."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, ".storage"))
        reg = {"data": {"entities": [
            {"entity_id": "light.fan", "platform": "hue", "unique_id": "abc", "config_entry_id": "h"},
            {"entity_id": "light.lines", "platform": "nanoleaf", "unique_id": "S1", "config_entry_id": "n"},
            {"entity_id": "light.other", "platform": "wled", "unique_id": "x", "config_entry_id": "l"},
        ]}}
        entries = {"data": {"entries": [
            {"entry_id": "h", "data": {"host": "bridge", "api_key": "k"}},
            {"entry_id": "n", "data": {"host": "panel", "token": "tok"}},
            {"entry_id": "l", "data": {"host": "wled"}},
        ]}}
        for name, obj in (("core.entity_registry", reg), ("core.config_entries", entries)):
            with open(os.path.join(self.dir.name, ".storage", name), "w") as f:
                json.dump(obj, f)

    def tearDown(self):
        self.dir.cleanup()

    def test_save_then_restore_writes_back_what_was_read(self):
        http = FakeHttp({HUE_URL: hue_resp(False, 40.0, xy={"x": 0.17, "y": 0.7}),
                         NANO + "/state": nano_resp(True, 70, "hs", hue=300, sat=60)})
        lm.save("t", ["light.fan", "light.lines"], config=self.dir.name, http=http)
        http.calls.clear()
        lm.restore("t", fade=1, config=self.dir.name, http=http)
        puts = dict((url, body) for url, body in http.puts() if url == HUE_URL)
        self.assertEqual(puts[HUE_URL]["on"], {"on": False})
        self.assertEqual(puts[HUE_URL]["color"], {"xy": {"x": 0.17, "y": 0.7}})
        nano_puts = [body for url, body in http.puts() if url.startswith(NANO)]
        self.assertEqual(nano_puts[-1], {"brightness": {"value": 70, "duration": 1}})

    def test_unsupported_platform_is_refused(self):
        with self.assertRaises(SystemExit):
            lm.devices(["light.other"], config=self.dir.name, http=FakeHttp({}))

    def test_one_failing_light_does_not_stop_the_rest(self):
        http = FakeHttp({HUE_URL: hue_resp(True, 80.0, mirek=366),
                         NANO + "/state": nano_resp(True, 70, "hs", hue=300, sat=60)})
        lm.save("t", ["light.fan", "light.lines"], config=self.dir.name, http=http)

        def flaky(method, url, body=None, headers=None, insecure=False):
            if url == HUE_URL:
                raise OSError("bulb offline")
            return http(method, url, body, headers, insecure)

        with self.assertRaises(SystemExit) as cm:
            lm.restore("t", config=self.dir.name, http=flaky)
        self.assertIn("light.fan", str(cm.exception))
        self.assertTrue(any(url.startswith(NANO) for url, _ in http.puts()))


if __name__ == "__main__":
    unittest.main()
