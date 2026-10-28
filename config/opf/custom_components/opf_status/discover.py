"""List OPF locations; optionally resolve all connectors near a point.

Run in HA: python /config/custom_components/opf_status/discover.py
Defaults to the Rengstorff area. Does not enumerate or guess identifiers.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path

import aiohttp

from api import FILTERS, ReadOnlyClient


def distance(lat, lon, row):
    p1, p2 = map(math.radians, (lat, row['latitude']))
    dp = p2-p1
    dl = math.radians(row['longitude']-lon)
    return 6371 * 2 * math.asin(min(1, math.sqrt(math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2)))


async def main(args):
    path = Path(args.session_file)
    tokens = json.loads(path.read_text())

    async def save(tokens):
        import os
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(tokens, output)
        os.replace(temporary, path)

    async with aiohttp.ClientSession() as session:
        client = ReadOnlyClient(session, tokens, [], save)
        try:
            if client.stopped:
                raise ValueError('OPF client is stopped; review access before resuming')
            import time
            if time.time() >= tokens.get('expires_at',0)-120:
                await client.refresh()
            locations = await client.request('/filtered-locations', body=FILTERS)
            if not isinstance(locations, list):
                raise ValueError('Invalid location response')
            if args.all_locations:
                print(json.dumps(locations, indent=2))
                return
            selected = sorted([row for row in locations if not row.get('fromRoaming') and
                               distance(args.latitude, args.longitude, row) <= args.radius_km],
                              key=lambda row: distance(args.latitude, args.longitude, row))
            if len(selected)>10:
                raise ValueError('Area contains more than 10 sites; use a smaller radius')
            client.sites = [{'building_id':row['buildingId']} for row in selected]
            for row in selected:
                detail = await client.request('/filtered-location/details/'+str(row['buildingId']),
                                              params={'isRoamingLocation':'false'}, body=FILTERS)
                print(json.dumps({'building_id':detail['buildingId'], 'name':detail['buildingName'],
                                  'latitude':detail['latitude'], 'longitude':detail['longitude'],
                                  'chargers':[{'alias':c['alias'],'identifier':c['identifier'],
                                               'connectors':c['connectors']} for c in detail['chargers']]},indent=2))
        except Exception:
            if client.stopped:
                client.tokens['stopped']=True
                await save(client.tokens)
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-file',default='/config/.storage/opf_status_session.json')
    parser.add_argument('--all-locations',action='store_true',help='Return all map locations visible to this account; no detail requests')
    parser.add_argument('--latitude',type=float,default=37.401)
    parser.add_argument('--longitude',type=float,default=-122.098)
    parser.add_argument('--radius-km',type=float,default=0.6)
    asyncio.run(main(parser.parse_args()))
