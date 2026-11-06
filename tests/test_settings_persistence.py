import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.platform_registry import PlatformRegistry
from test_platform_registry import Connector
TEST_TEMP=None

class SettingsPersistence(unittest.TestCase):
    def test_amounts_and_breaker_choice_survive_without_restoring_old_equity(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            path=Path(tmp)/'preferences.json'
            registry=PlatformRegistry(state_path=path)
            registry.make_connector=lambda p:Connector(p);registry.refresh('BINANCE')
            registry.configure('BINANCE',dict(action='SET_RISK',symbol='BTCUSDT',settings=dict(stop_loss_pct=10,stop_loss_amount=2.5,trailing_activation_amount=5,trailing_retrace_amount=1)))
            registry.configure('BINANCE',dict(action='SET_ACCOUNT_BREAKER',symbol='BTCUSDT',enabled=True,pct=.12))
            restored=PlatformRegistry(state_path=path).get('BINANCE')
            self.assertEqual(restored.prefs('BTCUSDT')['protection']['trailing_retrace_amount'],1)
            self.assertTrue(restored.account_breaker['enabled'])
            self.assertEqual(restored.account_breaker['drawdown_pct'],.12)
            self.assertEqual(restored.account_breaker['high_water'],0)
            self.assertFalse(restored.account_breaker['triggered'])

    def test_restart_preserves_only_user_settings_by_platform(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            path=Path(tmp)/'preferences.json'
            registry=PlatformRegistry(state_path=path)
            registry.make_connector=lambda p:Connector(p)
            registry.refresh('BINANCE');registry.refresh('LIGHTER')
            registry.configure('BINANCE',dict(action='SET_RATIO',symbol='BTCUSDT',ratio=.1234567))
            registry.configure('BINANCE',dict(action='SET_RISK',symbol='BTCUSDT',settings=dict(stop_loss_enabled=True,stop_loss_pct=.07)))
            registry.configure('LIGHTER',dict(action='SET_RATIO',symbol='1',ratio=.8))
            restored=PlatformRegistry(state_path=path)
            self.assertEqual(restored.get('BINANCE').prefs('BTCUSDT')['ratio'],.1234567)
            self.assertTrue(restored.get('BINANCE').prefs('BTCUSDT')['protection']['stop_loss_enabled'])
            self.assertEqual(restored.get('LIGHTER').prefs('1')['ratio'],.8)
            self.assertIsNone(restored.get('BINANCE').account)
            self.assertFalse(restored.get('BINANCE').authenticated)
            data=json.loads(path.read_text(encoding='utf-8'))
            self.assertNotIn('available_balance',str(data));self.assertNotIn('api_key',str(data))

    def test_corrupt_settings_fail_to_manual_defaults_with_visible_error(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            path=Path(tmp)/'preferences.json';path.write_text('{broken',encoding='utf-8')
            registry=PlatformRegistry(state_path=path)
            self.assertTrue(registry.get('OKX').errors)
            self.assertFalse(registry.get('OKX').prefs('BTC-USDT-SWAP')['protection']['stop_loss_enabled'])

    def test_failed_save_rolls_back_in_memory_change(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as tmp:
            registry=PlatformRegistry(state_path=Path(tmp)/'preferences.json')
            registry.make_connector=lambda p:Connector(p);registry.refresh('BINANCE')
            with patch.object(registry,'_persist',side_effect=OSError('disk unavailable')):
                with self.assertRaises(RuntimeError):registry.configure('BINANCE',dict(action='SET_RATIO',symbol='BTCUSDT',ratio=.7))
            self.assertEqual(registry.get('BINANCE').prefs('BTCUSDT')['ratio'],.2)

if __name__=='__main__':unittest.main()
