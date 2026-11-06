import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.isolated_risk_ledger import IsolatedRiskLedger
from core.smart_order_router import SmartOrderRouter
from config.instruments_catalog import get_instrument_spec

class FakeExchange:
    def __init__(self, terminal=True):
        self.calls=[]; self.terminal=terminal
    def position_side(self,side): return 'long' if side=='buy' else 'short'
    def get_ticker(self,s): return dict(bid=100,ask=101,last=100)
    def place_order(self,*args,**kw):
        self.calls.append((args,kw)); return dict(success=True,ordId=str(len(self.calls)))
    def get_order(self,s,o):
        if o=='1': return dict(state='canceled' if self.terminal else 'live',accFillSz='0.4',avgPx='100')
        return dict(state='filled',accFillSz='0.6',avgPx='101')
    def cancel_order(self,*a): return True
    def get_position(self,*a): raise AssertionError('Order fill must not use aggregate position')

class Regression(unittest.TestCase):
    def test_btc_contract_value(self):
        r=IsolatedRiskLedger(); r.register_entry('BTC-USDT-SWAP','buy',1,10000,100,1)
        _,a=r.evaluate_risk('BTC-USDT-SWAP',10100)
        self.assertAlmostEqual(a['net_pnl'],1.0,places=4)
    def test_failed_close_still_monitored(self):
        r=IsolatedRiskLedger(); r.register_entry('SOXL-USDT-SWAP','buy',1,100,10,10)
        self.assertEqual(r.evaluate_risk('SOXL-USDT-SWAP',90)[0],'TRIGGER_STOP_LOSS')
        self.assertEqual(r.evaluate_risk('SOXL-USDT-SWAP',90)[0],'TRIGGER_STOP_LOSS')
    def test_partial_fill_only_chases_remaining(self):
        e=FakeExchange(); result=SmartOrderRouter(e).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
        self.assertEqual(len(e.calls),2)
        self.assertEqual(float(e.calls[1][1]['sz']),0.6)
        self.assertAlmostEqual(result['fill_px'],100.6)
        self.assertEqual(e.calls[0][1]['pos_side'],'long')
    def test_cancel_ack_is_not_cancel_finality(self):
        e=FakeExchange(False); r=SmartOrderRouter(e).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
        self.assertEqual(len(e.calls),1)
        self.assertFalse(r['success'])


class AdditionalRegression(unittest.TestCase):
    def test_independent_optional_protection(self):
        r=IsolatedRiskLedger()
        a=r.register_entry('SOXL-USDT-SWAP','buy',1,100,5,20)
        b=r.register_entry('TSLA-USDT-SWAP','buy',1,100,5,20)
        r.configure('SOXL-USDT-SWAP',stop_loss_enabled=False,breakeven_enabled=False,trailing_enabled=False)
        self.assertEqual(r.evaluate_risk('SOXL-USDT-SWAP',1)[0],'HOLD')
        self.assertEqual(r.evaluate_risk('TSLA-USDT-SWAP',1)[0],'TRIGGER_STOP_LOSS')
        r.configure('SOXL-USDT-SWAP',fixed_take_profit_pct=.1)
        self.assertEqual(r.evaluate_risk('SOXL-USDT-SWAP',101)[0],'TRIGGER_FIXED_TP')
        r.configure('SOXL-USDT-SWAP',fixed_take_profit_pct=None)
        self.assertEqual(r.evaluate_risk('SOXL-USDT-SWAP',101)[0],'HOLD')
        r.configure('SOXL-USDT-SWAP',stop_loss_enabled=True,stop_loss_pct=.08)
        r.register_entry('SOXL-USDT-SWAP','buy',1,110,5.5,20)
        self.assertTrue(a.stop_loss_enabled)
        self.assertFalse(a.trailing_enabled)
        self.assertEqual(a.stop_loss_pct,.08)

    def test_entry_execution_policies(self):
        spec=get_instrument_spec('SOXL-USDT-SWAP')
        e=FakeExchange(); result=SmartOrderRouter(e).execute_smart_entry(spec,'buy',1,execution_mode='speed')
        self.assertEqual(len(e.calls),1)
        self.assertEqual(e.calls[0][1]['ord_type'],'ioc')
        self.assertLessEqual(float(e.calls[0][1]['px']),101*1.001)
        self.assertEqual(result['sz'],.4)
        e=FakeExchange(); result=SmartOrderRouter(e).execute_smart_entry(spec,'buy',1,timeout_ms=0,execution_mode='maker_only')
        self.assertEqual(len(e.calls),1)
        self.assertEqual(result['sz'],.4)
        self.assertTrue(result['success'])

    def test_account_breaker_optional(self):
        from web_ui import server
        old=dict(server.runtime_state['account_breaker'])
        try:
            server.runtime_state['account_breaker'].update(enabled=False,high_water=100,drawdown_pct=.05,triggered=False)
            self.assertFalse(server.check_account_breaker(90))
            server.runtime_state['account_breaker']['enabled']=True
            self.assertTrue(server.check_account_breaker(95))
            self.assertTrue(server.check_account_breaker(110))
        finally: server.runtime_state['account_breaker']=old

    def test_public_feed_freshness(self):
        import json,time
        from core.okx_ws_client import OKXPublicWS
        feed=OKXPublicWS(['BTC-USDT-SWAP']); feed.connected=True
        feed.ingest(json.dumps({'data':[dict(instId='BTC-USDT-SWAP',last='100',bidPx='99',askPx='101',ts=str(int(time.time()*1000)))]}))
        self.assertEqual(feed.get('BTC-USDT-SWAP')['last'],100)
        feed.cache['BTC-USDT-SWAP']['received_at']-=3
        self.assertIsNone(feed.get('BTC-USDT-SWAP'))

    def test_position_mode_and_live_guard(self):
        from core.physical_engine_pool import PhysicalLimitEnginePool
        pool=PhysicalLimitEnginePool('fake','fake','fake')
        try:
            pool.read_api=lambda *a,**k:[{'posMode':'long_short_mode'}]
            self.assertEqual(pool.detect_position_mode(),'long_short_mode')
            self.assertEqual(pool.position_side('sell'),'short')
            pool.pos_mode='net_mode'
            self.assertEqual(pool.position_side('sell'),'net')
            with self.assertRaises(PermissionError): pool.place_order('BTC-USDT-SWAP','buy','net','market','1')
        finally: pool.close()

    def test_command_isolation_and_throttle_unit(self):
        # Test the real command service with an in-memory exchange and an inline
        # thread boundary. This is a unit test, NOT an ASGI/browser E2E claim.
        from unittest.mock import patch
        from web_ui import server
        def finish(coro):
            try: coro.send(None)
            except StopIteration as done: return done.value
            raise AssertionError('Unexpected asynchronous suspension')
        async def inline(fn,*a,**k): return fn(*a,**k)
        class Exchange:
            def __init__(self): self.positions={'SOXL-USDT-SWAP':1,'BTC-USDT-SWAP':1}; self.cancelled=[]; self.fail=False; self.calls=0
            def require_trading_authorization(self): pass
            def cancel_all_orders(self,s=None): self.cancelled.append(s)
            def get_positions(self,s=None): return [{'instId':k} for k,v in self.positions.items() if v and (s is None or k==s)]
            def close_position_instant(self,s):
                if self.fail: return {'success':False}
                self.positions[s]=0; return {'success':True}
            def get_account_balance(self): self.calls+=1; return {'total_equity':1,'available_balance':1}
        e=Exchange(); old_pool,old_router,old_risk=server.engine_pool,server.smart_router,server.risk_ledger
        server.engine_pool=e; server.smart_router=SmartOrderRouter(e); server.risk_ledger=IsolatedRiskLedger()
        server.risk_ledger.register_entry('SOXL-USDT-SWAP','buy',1,100,5,20)
        try:
            with patch('asyncio.to_thread',inline):
                e.fail=True
                self.assertFalse(finish(server.handle_command({'action':'FLATTEN_CURRENT','symbol':'SOXL-USDT-SWAP'}))['success'])
                self.assertIn('SOXL-USDT-SWAP',server.risk_ledger.ledgers)
                e.fail=False
                self.assertTrue(finish(server.handle_command({'action':'FLATTEN_CURRENT','symbol':'SOXL-USDT-SWAP'}))['success'])
                self.assertEqual(e.positions['BTC-USDT-SWAP'],1)
                self.assertTrue(finish(server.handle_command({'action':'FLATTEN_ALL'}))['success'])
                self.assertFalse(any(e.positions.values()))
                server.runtime_state.pop('balance_polled_at',None)
                for t in (0,.1,1.9,2,2.1): finish(server.refresh_balance_once(t))
                self.assertEqual(e.calls,2)
        finally:
            server.engine_pool,server.smart_router,server.risk_ledger=old_pool,old_router,old_risk


class DeepAcceptanceRegression(unittest.TestCase):
    def test_chase_with_zero_fill_price_is_rejected(self):
        class BadFill(FakeExchange):
            def get_order(self,s,o):
                row=super().get_order(s,o)
                if o=='2': row['avgPx']='0'
                return row
        r=SmartOrderRouter(BadFill()).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,timeout_ms=0)
        self.assertFalse(r['success'])

    def test_speed_with_nan_fill_size_is_rejected(self):
        class BadFill(FakeExchange):
            def get_order(self,s,o): return dict(state='filled',accFillSz='NaN',avgPx='100')
        r=SmartOrderRouter(BadFill()).execute_smart_entry(get_instrument_spec('SOXL-USDT-SWAP'),'buy',1,execution_mode='speed')
        self.assertFalse(r['success'])

    def test_risk_validator_rejects_unknown_numeric_spec(self):
        from core.position_calculator import PositionCalculator
        from dataclasses import replace
        spec=replace(get_instrument_spec('BTC-USDT-SWAP'),ct_val=float('nan'))
        with self.assertRaises(ValueError): PositionCalculator.calculate_order_size(spec,100,1,1,.1)


class EmergencyRegression(unittest.TestCase):
    def test_cancel_failure_does_not_prevent_flattening_other_symbols(self):
        from unittest.mock import patch
        from web_ui import server
        class Exchange:
            def __init__(self):self.closed=[]
            def require_trading_authorization(self):pass
            def cancel_all_orders(self,s=None):raise RuntimeError('cancel timeout')
            def get_positions(self,s=None):return [{'instId':k} for k in ('SOXL-USDT-SWAP','BTC-USDT-SWAP') if k not in self.closed and (s is None or s==k)]
            def close_position_instant(self,s):self.closed.append(s);return {'success':True}
        e=Exchange();old=(server.engine_pool,server.smart_router,server.risk_ledger)
        server.engine_pool=e;server.smart_router=SmartOrderRouter(e);server.risk_ledger=IsolatedRiskLedger()
        async def inline(fn,*a,**kw):return fn(*a,**kw)
        def finish(coro):
            try:coro.send(None)
            except StopIteration as done:return done.value
        try:
            with patch('asyncio.to_thread',inline):result=finish(server.handle_command({'action':'FLATTEN_ALL'}))
            self.assertEqual(set(e.closed),{'SOXL-USDT-SWAP','BTC-USDT-SWAP'})
            self.assertFalse(result['success'])
        finally:server.engine_pool,server.smart_router,server.risk_ledger=old


class SizingMatrix(unittest.TestCase):
    def test_all_instruments_arbitrary_ratios_and_funds(self):
        from core.position_calculator import PositionCalculator
        from config.instruments_catalog import list_active_instruments
        from decimal import Decimal
        count=0
        for spec in list_active_instruments():
            for equity in (.000001,.01,1.46,100,10000):
                for ratio in (.000001,.0001,.05,.12345,1/3,.5,.98765,1):
                    for leverage in (1,spec.max_leverage):
                        count+=1
                        try:size,margin=PositionCalculator.calculate_order_size(spec,100,leverage,equity,ratio)
                        except ValueError:
                            self.assertGreater(spec.min_sz*spec.ct_val*100/leverage,equity*ratio)
                            continue
                        self.assertLessEqual(margin,equity*ratio+1e-12)
                        self.assertGreaterEqual(size,spec.min_sz)
                        self.assertEqual(Decimal(str(size))%Decimal(str(spec.lot_sz)),0)
                        self.assertAlmostEqual(margin,size*spec.ct_val*100/leverage,places=9)
        self.assertEqual(count, len(list_active_instruments()) * 80)


class PerSymbolControls(unittest.TestCase):
    def test_arbitrary_ratio_is_isolated_by_symbol(self):
        from web_ui import server
        def finish(coro):
            try:coro.send(None)
            except StopIteration as done:return done.value
        a='SOXL-USDT-SWAP';b='BTC-USDT-SWAP'
        finish(server.handle_command({'action':'SET_RATIO','symbol':a,'ratio':.12345}))
        finish(server.handle_command({'action':'SET_RATIO','symbol':b,'ratio':.87654}))
        self.assertEqual(server.runtime_state['ratio_map'][a],.12345)
        self.assertEqual(server.runtime_state['ratio_map'][b],.87654)
        for ratio in (0,-1,1.001,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):finish(server.handle_command({'action':'SET_RATIO','symbol':a,'ratio':ratio}))

if __name__=='__main__': unittest.main()
