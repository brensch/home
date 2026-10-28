"""Offline tests using real OPF response fixtures. Never contact the service."""
import importlib.util
import json
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
class ClientError(Exception):
    pass

transport = types.SimpleNamespace(ClientError=ClientError, ClientTimeout=lambda **kw: kw)
spec = importlib.util.spec_from_file_location('opf_api_under_test', ROOT/'config/opf/custom_components/opf_status/api.py')
api = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'aiohttp': transport}):
    spec.loader.exec_module(api)
SITES = json.loads((ROOT/'config/opf/custom_components/opf_status/sites.json').read_text())
FIXTURES = {s['building_id']: json.loads((ROOT/f"tests/fixtures/opf/{s['building_id']}.json").read_text()) for s in SITES}

class Response:
    def __init__(self, body=None, status=200):
        self.status, self.body = status, body
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def json(self, **kwargs):
        return self.body

class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

async def save(tokens):
    pass

def client(session):
    return api.ReadOnlyClient(session, {'access_token':'test-access','refresh_token':'test-refresh',
                                       'expires_at':time.time()+3600}, SITES, save)

class Tests(unittest.IsolatedAsyncioTestCase):
    def test_real_sites_and_alias_resolution(self):
        for site, free, total in zip(SITES, (2,1), (9,4)):
            data=api.snapshot(FIXTURES[site['building_id']],site)
            self.assertEqual((data['free'],data['total']), (free,total))
        self.assertIn('099102',api.snapshot(FIXTURES[51057],SITES[0])['available_aliases'])
        target=next(c for c in SITES[0]['chargers'] if c['alias']=='099102')
        self.assertEqual(target['identifier'],'1111118189')

    def test_unknown_missing_and_duplicate_fail_closed(self):
        site=SITES[0]
        rows=[dict(d) for d in FIXTURES[51057]]
        rows[0]['status']='NewState'
        self.assertIsNone(api.snapshot(rows,site)['free'])
        for bad in (rows[:-1], rows+[rows[0]], {}, [{'chargerIdentifier':'099102','connector':1,'status':'Available'}]):
            with self.assertRaises(ValueError):api.snapshot(bad,site)

    async def test_success_uses_two_read_routes_and_throttles_manual_updates(self):
        session=Session(*(Response(FIXTURES[s['building_id']]) for s in SITES))
        instance=client(session)
        result=await instance.poll()
        self.assertEqual(sum(s['free'] for s in result.values()),3)
        self.assertEqual(await instance.poll(),result)
        self.assertEqual(len(session.calls),2)
        for url,kwargs in session.calls:
            self.assertIn('/connectors/status/by-location/',url)
            self.assertEqual(kwargs['params'],{'isRoaming':'false'})
            self.assertFalse(kwargs['allow_redirects'])

    async def test_access_denial_rate_limit_and_redirect_latch(self):
        for status in (400,401,403,429,302):
            session=Session(Response(status=status))
            instance=client(session)
            for _ in range(2):
                with self.assertRaises(api.Stopped):await instance.poll()
            self.assertEqual(len(session.calls),1)

    async def test_partial_snapshot_not_published_and_backs_off(self):
        session=Session(Response(FIXTURES[51057]),Response(status=503))
        instance=client(session)
        with self.assertRaises(ValueError):await instance.poll()
        self.assertEqual(instance.backoff,240)
        with self.assertRaises(ValueError):await instance.poll()
        self.assertEqual(len(session.calls),2)

    async def test_refresh_persists_without_password_and_handles_rotation(self):
        session=Session(Response({'AuthenticationResult':{'AccessToken':'new-access','RefreshToken':'new-refresh','ExpiresIn':3600}}))
        instance=client(session)
        saved=[]
        async def persist(tokens):saved.append(tokens)
        instance.save_tokens=persist
        await instance.refresh()
        self.assertEqual(saved[0]['refresh_token'],'new-refresh')
        payload=session.calls[0][1]['json']
        self.assertEqual(payload['AuthFlow'],'REFRESH_TOKEN_AUTH')
        self.assertEqual(payload['AuthParameters'],{'REFRESH_TOKEN':'test-refresh'})

    async def test_refresh_challenge_stops(self):
        instance=client(Session(Response({'ChallengeName':'SMS_MFA'})))
        with self.assertRaises(api.Stopped):await instance.refresh()

    async def test_control_route_is_impossible(self):
        instance=client(Session())
        with self.assertRaises(ValueError):await instance.request('/start/transaction')
        self.assertEqual(instance.session.calls,[])

    async def test_watch_changes_polling_between_30_and_120_seconds(self):
        session = Session(*(Response(FIXTURES[site['building_id']]) for _ in range(3) for site in SITES))
        instance = client(session)
        clock = [1000]
        with patch.object(api, 'monotonic', side_effect=lambda: clock[0]):
            await instance.poll()
            instance.set_poll_interval(30)
            clock[0] = 1029
            await instance.poll()
            self.assertEqual(len(session.calls), 2)
            clock[0] = 1030
            await instance.poll()
            self.assertEqual(len(session.calls), 4)
            instance.set_poll_interval(120)
            clock[0] = 1060
            await instance.poll()
            self.assertEqual(len(session.calls), 4)
            clock[0] = 1150
            await instance.poll()
            self.assertEqual(len(session.calls), 6)

    async def test_watch_cannot_shorten_backoff_or_restart_stopped_client(self):
        session = Session(Response(status=503))
        instance = client(session)
        clock = [1000]
        with patch.object(api, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(ValueError):
                await instance.poll()
            next_poll = instance.next_poll
            instance.set_poll_interval(30)
            self.assertEqual(instance.next_poll, next_poll)
            clock[0] = 1030
            with self.assertRaises(ValueError):
                await instance.poll()
        self.assertEqual(len(session.calls), 1)
        instance.stopped = True
        instance.set_poll_interval(120)
        with self.assertRaises(api.Stopped):
            await instance.poll()
        self.assertEqual(len(session.calls), 1)

if __name__=='__main__':unittest.main()
