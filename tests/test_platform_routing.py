"""Connection-scoped routing without a socket/event-loop fixture."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_ui import server
from core.platform_registry import PlatformRegistry
from test_platform_registry import Connector

def complete(coroutine):
    try:coroutine.send(None)
    except StopIteration as done:return done.value
    raise AssertionError('Unexpected asynchronous I/O in pure routing contract')

class ConnectionRouting(unittest.TestCase):
    def setUp(self):
        registry=PlatformRegistry();registry.make_connector=lambda name:Connector(name)
        for name in ('OKX','BINANCE','LIGHTER'):registry.refresh(name)
        self.patch=patch.object(server,'platform_registry',registry);self.patch.start()
        self.scheduled=[]
        self.task_patch=patch.object(server.asyncio,'create_task',self.scheduled.append);self.task_patch.start()
        self.ws=object();server.connection_platforms[self.ws]='OKX'
    def tearDown(self):
        self.task_patch.stop()
        for task in self.scheduled:task.close()
        self.patch.stop();server.connection_platforms.pop(self.ws,None);server.connection_catalog_versions.pop(self.ws,None)
    def call(self,**command):return complete(server.route_connection_command(self.ws,command))
    def test_connection_switch_binds_subsequent_native_commands(self):
        self.call(action='SWITCH_PLATFORM',platform='BINANCE')
        self.call(action='SET_RATIO',platform='BINANCE',symbol='BTCUSDT',ratio=.12345)
        self.assertEqual(server.platform_registry.get('BINANCE').prefs('BTCUSDT')['ratio'],.12345)
        with self.assertRaises(ValueError):self.call(action='SET_RATIO',platform='OKX',symbol='BTC-USDT-SWAP',ratio=.5)
        self.assertEqual(server.platform_registry.get('OKX').prefs('BTC-USDT-SWAP')['ratio'],.2)
    def test_no_platform_field_cannot_escape_connection_owner(self):
        self.call(action='SWITCH_PLATFORM',platform='LIGHTER')
        with self.assertRaises(ValueError):self.call(action='SWITCH_SYMBOL',symbol='BTC-USDT-SWAP')
        for action in ('BUY_LONG','SELL_SHORT','FLATTEN_CURRENT','FLATTEN_ALL','CANCEL_ALL'):
            with self.assertRaises(PermissionError):self.call(action=action,symbol='1')
    def test_heartbeat_and_unknown_platform(self):
        self.assertEqual(self.call(action='PING',sent_at=321),{'type':'PONG','sent_at':321})
        with self.assertRaises(ValueError):self.call(action='SWITCH_PLATFORM',platform='UNREGISTERED')

    def test_switch_starts_read_connection_without_duplicate_refresh(self):
        context=server.platform_registry.get('BINANCE')
        context.last_refresh_attempt_at-=21
        scheduled=[]
        def capture(coroutine):scheduled.append(coroutine)
        with patch.object(server.asyncio,'create_task',capture):
            self.call(action='SWITCH_PLATFORM',platform='BINANCE')
            self.call(action='SWITCH_PLATFORM',platform='BINANCE')
        for task in scheduled:task.close()
        self.assertEqual(len(scheduled),1)
        self.assertTrue(server.platform_registry.get('BINANCE').loading)

    def test_switch_reuses_fresh_platform_snapshot(self):
        self.call(action='SWITCH_PLATFORM',platform='BINANCE')
        self.assertFalse(self.scheduled)
        self.assertFalse(server.platform_registry.get('BINANCE').loading)

    def test_only_viewed_platform_gets_periodic_refresh(self):
        b=server.platform_registry.get('BINANCE')
        b.metadata_at=server.time.monotonic()-21
        b.last_refresh_attempt_at=b.metadata_at
        b.connector=object()
        self.assertFalse(server.platform_refresh_due('BINANCE'))
        server.connection_platforms[self.ws]='BINANCE'
        self.assertTrue(server.platform_refresh_due('BINANCE'))
        b.loading=True
        self.assertFalse(server.platform_refresh_due('BINANCE'))

if __name__=='__main__':unittest.main()
