import copy,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_ui import server
from core.smart_order_router import SmartOrderRouter
from core.isolated_risk_ledger import IsolatedRiskLedger
from config.instruments_catalog import get_instrument_spec

class FixtureExchange:
    """In-memory exchange fixture. Not an OKX session, never uses real credentials."""
    def __init__(self):self.orders={};self.positions={};self.levers={};self.cloud=[];self.reject_cloud=False
    def require_trading_authorization(self):pass
    def position_side(self,side):return 'net'
    def get_ticker(self,s):return {'instId':s,'bid':100.,'ask':100.,'last':100.,'exchange_ts':int(time.time()*1000)}
    def get_account_balance(self):
        used=sum(p['margin'] for p in self.positions.values())
        return {'total_equity':100.,'available_balance':100.-used}
    def set_leverage(self,s,value):self.levers[s]=value;return True
    def place_order(self,inst_id,side,pos_side,ord_type,sz,px=None,reduce_only=False):
        size=float(sz);spec=get_instrument_spec(inst_id);price=100.
        old=self.positions.get(inst_id)
        if reduce_only:
            if not old or old['side']==side or size>old['size']:return {'success':False}
            remainder=old['size']-size;old['margin']*=remainder/old['size'];old['size']=remainder
            if not remainder:self.positions.pop(inst_id)
        else:
            margin=size*spec.ct_val*price/self.levers[inst_id]
            if old:old['size']+=size;old['margin']+=margin
            else:self.positions[inst_id]={'side':side,'size':size,'margin':margin}
        oid=str(len(self.orders)+1);self.orders[oid]={'state':'filled','accFillSz':str(size),'avgPx':str(price)}
        return {'success':True,'ordId':oid}
    def get_order(self,s,oid):return self.orders[oid]
    def get_positions(self,s=None):return [{'instId':k,'pos':str(v['size'] if v['side']=='buy' else -v['size'])} for k,v in self.positions.items() if s is None or s==k]
    def close_position_instant(self,s):self.positions.pop(s,None);return {'success':True}
    def cancel_all_orders(self,s=None):pass
    def place_cloud_algo_stop(self,*a):
        self.cloud.append(a)
        return {'code':'0','data':[{'sCode':'51000' if self.reject_cloud else '0','algoId':'algo1'}]}

class CommandFlow(unittest.TestCase):
    def setUp(self):
        self.original=(server.engine_pool,server.smart_router,server.risk_ledger,server.feed,copy.deepcopy(server.runtime_state))
        self.exchange=FixtureExchange();server.engine_pool=self.exchange;server.smart_router=SmartOrderRouter(self.exchange);server.risk_ledger=IsolatedRiskLedger()
        class Feed:
            def get(inner,s):return self.exchange.get_ticker(s)
        server.feed=Feed()
        server.runtime_state['reconciliation_required']=False
        server.runtime_state['account_breaker']['triggered']=False
        server.runtime_state['protection']={s:dict(server.DEFAULT_PROTECTION) for s in server.runtime_state['protection']}
        async def inline(fn,*a,**kw):return fn(*a,**kw)
        self.thread_patch=patch('asyncio.to_thread',inline);self.thread_patch.start()
    def tearDown(self):
        self.thread_patch.stop();server.engine_pool,server.smart_router,server.risk_ledger,server.feed,state=self.original
        server.runtime_state.clear();server.runtime_state.update(state)
    def cmd(self,action,symbol='SOXL-USDT-SWAP',**values):
        coroutine=server.handle_command({'action':action,'symbol':symbol,**values})
        try:coroutine.send(None)
        except StopIteration as end:return end.value
        raise AssertionError('Unexpected suspension in in-memory command fixture')
    def configure(self,symbol='SOXL-USDT-SWAP',ratio=.2):
        self.cmd('SET_LEVERAGE',symbol,leverage=10);self.cmd('SET_RATIO',symbol,ratio=ratio)
    def test_buy_add_half_close_and_current_flatten(self):
        self.configure();r=self.cmd('BUY_LONG');self.assertTrue(r['success'])
        self.assertEqual(server.risk_ledger.ledgers['SOXL-USDT-SWAP'].size,2.)
        self.cmd('SET_RATIO',ratio=.25);r=self.cmd('BUY_LONG');self.assertTrue(r['success'])
        p=server.risk_ledger.ledgers['SOXL-USDT-SWAP'];self.assertEqual(p.size,4.)
        # available 80 * .25 = 20 margin -> another 2 contracts; not 100*.25.
        self.assertEqual(p.initial_margin,40.)
        self.assertTrue(self.cmd('PARTIAL_CLOSE')['success']);self.assertEqual(p.size,2.)
        self.assertTrue(self.cmd('FLATTEN_CURRENT')['success']);self.assertFalse(self.exchange.positions)
    def test_sell_short_then_half_and_flatten(self):
        self.configure();self.assertTrue(self.cmd('SELL_SHORT')['success'])
        p=server.risk_ledger.ledgers['SOXL-USDT-SWAP'];self.assertEqual(p.side,'sell')
        self.assertTrue(self.cmd('PARTIAL_CLOSE')['success']);self.assertEqual(p.size,1.)
        self.assertTrue(self.cmd('FLATTEN_CURRENT')['success']);self.assertFalse(self.exchange.positions)
    def test_global_flatten_independent_slots(self):
        self.configure();self.cmd('BUY_LONG')
        self.configure('TSLA-USDT-SWAP',.125);self.cmd('SELL_SHORT','TSLA-USDT-SWAP')
        self.assertTrue(self.cmd('FLATTEN_CURRENT')['success']);self.assertIn('TSLA-USDT-SWAP',self.exchange.positions)
        self.assertTrue(self.cmd('FLATTEN_ALL')['success']);self.assertFalse(self.exchange.positions)
    def test_nested_cloud_rejection_is_not_protected_entry(self):
        self.configure();self.cmd('SET_RISK',settings={'stop_loss_enabled':True});self.exchange.reject_cloud=True
        with self.assertRaises(RuntimeError):self.cmd('BUY_LONG')
        self.assertFalse(self.exchange.positions)
        self.assertTrue(server.runtime_state['reconciliation_required'])

if __name__=='__main__':unittest.main()
