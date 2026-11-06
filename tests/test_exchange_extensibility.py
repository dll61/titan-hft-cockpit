"""
Multi-Exchange Extensibility and Adapter Compliance Regression Tests
Ensures zero hardcoding: OKX, Binance, Lighter DEX, and custom platforms can be swapped seamlessly.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import unittest
from core.exchange_adapter import (
    BaseExchangeAdapter,
    OKXAdapter,
    BinanceFuturesAdapter,
    LighterExchangeAdapter,
    ExchangeFactory
)
from core.smart_order_router import SmartOrderRouter
from config.instruments_catalog import get_instrument_spec

class DummyExchange(BaseExchangeAdapter):
    name = "DUMMY_HL"
    def __init__(self):
        self.orders = []
        self.positions = {}
    def get_ticker(self, inst_id: str):
        return {"instId": inst_id, "last": 100.0, "bid": 99.9, "ask": 100.1, "rtt_ms": 1.5}
    def get_account_balance(self, ccy: str = "USDT"):
        return {"total_equity": 1000.0, "available_balance": 800.0}
    def get_positions(self, inst_id = None):
        return [{"instId": k, "pos": str(v)} for k, v in self.positions.items()]
    def get_order(self, inst_id, ord_id):
        return {"instId": inst_id, "ordId": ord_id, "state": "filled", "accFillSz": "1.0", "avgPx": "100.0"}
    def place_order(self, inst_id, side, pos_side, ord_type, sz, px=None):
        self.orders.append((inst_id, side, sz, px))
        return {"success": True, "ordId": "DUMMY_123", "mode": "MOCK_FILL", "state": "filled"}
    def cancel_order(self, inst_id, ord_id):
        return {"success": True, "cancelled": True}
    def close_position_instant(self, inst_id):
        self.positions.pop(inst_id, None)
        return {"success": True, "closed": True}
    def cancel_all_orders(self, inst_id = None):
        return {"success": True, "cancelled_count": len(self.orders)}
    def detect_position_mode(self):
        return "net_mode"
    def audit_instruments(self, specs):
        return [{"instId": s.inst_id, "lever": "50"} for s in specs]


class TestExchangeExtensibility(unittest.TestCase):
    def test_factory_registration_and_creation(self):
        # Verify default supported adapters
        bn = ExchangeFactory.create_adapter("BINANCE")
        self.assertIsInstance(bn, BinanceFuturesAdapter)
        self.assertEqual(bn.name, "BINANCE_FUTURES")

        lt = ExchangeFactory.create_adapter("LIGHTER")
        self.assertIsInstance(lt, LighterExchangeAdapter)
        self.assertEqual(lt.name, "LIGHTER_DEX")

        # Test dynamic extension (e.g. Hyperliquid DEX)
        ExchangeFactory.register_adapter("HYPERLIQUID", DummyExchange)
        hl = ExchangeFactory.create_adapter("HYPERLIQUID")
        self.assertIsInstance(hl, DummyExchange)
        self.assertEqual(hl.name, "DUMMY_HL")

    def test_smart_router_works_with_generic_adapter(self):
        dummy = DummyExchange()
        router = SmartOrderRouter(dummy)
        spec = get_instrument_spec("SOXL-USDT-SWAP")
        
        # Test generic entry on dummy exchange without any OKX dependency
        res = router.execute_smart_entry(spec, "buy", 1.0)
        self.assertTrue(res.get("success"))
        self.assertEqual(len(dummy.orders), 1)

        # Test generic emergency flatten on dummy exchange
        flat_res = router.execute_emergency_flatten("SOXL-USDT-SWAP")
        self.assertTrue(flat_res.get("success"))


class TestAdapterSafety(unittest.TestCase):
    def test_unimplemented_platforms_never_fabricate_success(self):
        for name in ('BINANCE','LIGHTER'):
            adapter=ExchangeFactory.create_adapter(name)
            with self.assertRaises(NotImplementedError):adapter.get_ticker('BTC-USDT-SWAP')
            with self.assertRaises(NotImplementedError):adapter.place_order('BTC-USDT-SWAP','buy','net','market','1')
            with self.assertRaises(NotImplementedError):adapter.close_position_instant('BTC-USDT-SWAP')
    def test_okx_adapter_preserves_reduce_only_and_position_mode(self):
        class Engine:
            def position_side(self,side):return 'net'
            def place_order(self,*a,**kw):self.last=kw;return {'success':True}
        engine=Engine();adapter=OKXAdapter(engine)
        self.assertEqual(adapter.position_side('buy'),'net')
        adapter.place_order('BTC-USDT-SWAP','sell','net','market','1',reduce_only=True)
        self.assertTrue(engine.last['reduce_only'])

if __name__ == "__main__":
    unittest.main()
