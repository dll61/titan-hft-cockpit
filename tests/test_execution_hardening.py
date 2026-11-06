import sys, unittest
from pathlib import Path
from dataclasses import replace
from requests import Response
from requests.adapters import BaseAdapter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.physical_engine_pool import PhysicalLimitEnginePool
from core.smart_order_router import SmartOrderRouter
from core.position_calculator import PositionCalculator
from config.instruments_catalog import get_instrument_spec
from test_regressions import FakeExchange


class ExecutionHardening(unittest.TestCase):
    def test_minimum_must_not_exceed_selected_allocation(self):
        spec=replace(get_instrument_spec('BTC-USDT-SWAP'),min_sz=1,lot_sz=1)
        with self.assertRaises(ValueError):
            PositionCalculator.calculate_order_size(spec,10000,1,1000,.01)

    def test_weighted_average_keeps_small_price_precision(self):
        price,size=PositionCalculator.compute_weighted_average(1,.00001234,2,.00001237)
        self.assertAlmostEqual(price,.00001236,places=12)
        self.assertEqual(size,3)

    def test_invalid_bbo_never_submits(self):
        for bid,ask in ((float('nan'),101),(102,101),(100,float('inf'))):
            e=FakeExchange();e.get_ticker=lambda s:dict(bid=bid,ask=ask,last=100)
            result=SmartOrderRouter(e).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
            self.assertFalse(result['success']);self.assertFalse(e.calls)

    def test_invalid_maker_average_cannot_trigger_chase(self):
        for price in ('NaN','Infinity','invalid','0'):
            e=FakeExchange();e.get_order=lambda s,o:dict(state='canceled',accFillSz='.4',avgPx=price)
            result=SmartOrderRouter(e).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
            self.assertFalse(result['success']);self.assertEqual(len(e.calls),1)

    def test_timeout_after_submit_requires_reconciliation(self):
        e=FakeExchange()
        def lost(*a):raise TimeoutError('fixture request contains sensitive headers')
        e.get_order=lost
        result=SmartOrderRouter(e).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
        self.assertFalse(result['success']);self.assertTrue(result['reconciliation_required'])
        self.assertNotIn('sensitive',str(result));self.assertEqual(len(e.calls),1)

    def test_untrusted_credential_destination_rejected(self):
        for url in ('http://www.okx.com','https://attacker.invalid','https://www.okx.com@attacker.invalid','https://www.okx.com/other'):
            with self.assertRaises(ValueError):PhysicalLimitEnginePool('fixture-key','fixture-secret','fixture-pass',base_url=url)

    def test_redirect_cannot_forward_authentication_headers(self):
        class Redirect(BaseAdapter):
            def __init__(self):self.calls=[]
            def send(self,request,**kwargs):
                self.calls.append(request.url)
                r=Response();r.status_code=302;r.headers['Location']='https://attacker.invalid/collect'
                r._content=b'{}';r.url=request.url;r.request=request
                return r
            def close(self):pass
        pool=PhysicalLimitEnginePool('fixture-key','fixture-secret','fixture-pass')
        adapter=Redirect();pool.session.mount('https://',adapter)
        try:
            with self.assertRaises(RuntimeError):pool.read_api('/api/v5/account/config')
            self.assertEqual(adapter.calls,['https://www.okx.com/api/v5/account/config'])
        finally:pool.close()


if __name__=='__main__':unittest.main()
