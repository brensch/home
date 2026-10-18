#!/usr/bin/env python3
"""Google's own address ranges, minus Google Cloud's (which anyone can rent): the addresses Google's smart home
servers should come from. Writes them one per line for the relay's RELAY_GOOGLE_RANGES (see README.md).

    python3 google-ranges.py > ../../data/google-relay/google-ranges.txt
"""
import ipaddress
import json
import sys
import urllib.request


def prefixes(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    return [ipaddress.ip_network(p.get("ipv4Prefix") or p["ipv6Prefix"]) for p in data["prefixes"]]


goog = prefixes("https://www.gstatic.com/ipranges/goog.json")
cloud = prefixes("https://www.gstatic.com/ipranges/cloud.json")
out = []
for net in goog:
    parts = [net]
    for c in cloud:
        if c.version != net.version:
            continue
        nxt = []
        for p in parts:
            if c.subnet_of(p):
                nxt.extend(p.address_exclude(c))
            elif p.subnet_of(c):
                pass
            else:
                nxt.append(p)
        parts = nxt
    out.extend(parts)
if len(out) < 20:
    sys.exit(f"only {len(out)} ranges; refusing to write a list that small")
print("# Google's own ranges minus Google Cloud's, from gstatic.com/ipranges (config/google-relay/google-ranges.py)")
for net in sorted(ipaddress.collapse_addresses([n for n in out if n.version == 4])):
    print(net)
for net in sorted(ipaddress.collapse_addresses([n for n in out if n.version == 6])):
    print(net)
