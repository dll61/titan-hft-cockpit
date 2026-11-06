import sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.platform_registry import PlatformRegistry
from core.platform_models import parse_okx,parse_binance,parse_lighter
from config.platform_credentials import parse_text

def market(platform):
    if platform=='OKX':return parse_okx({'instId':'BTC-USDT-SWAP','state':'live','ctType':'linear','settleCcy':'USDT','ctVal':'0.01','minSz':'0.01','lotSz':'0.01','tickSz':'0.1','lever':'100'})
    if platform=='BINANCE':return parse_binance({'symbol':'BTCUSDT','status':'TRADING','contractType':'PERPETUAL','marginAsset':'USDT','filters':[{'filterType':'LOT_SIZE','minQty':'0.001','stepSize':'0.001'},{'filterType':'PRICE_FILTER','tickSize':'0.1'}]},[{'notionalFloor':0,'notionalCap':500,'initialLeverage':50},{'notionalFloor':500,'notionalCap':10000,'initialLeverage':20}])
    return parse_lighter({'market_id':1,'symbol':'BTC','status':'active','min_initial_margin_fraction':500,'min_base_amount':'.001','min_quote_amount':5,'supported_size_decimals':3,'supported_price_decimals':1})

class Connector:
    def __init__(self,platform):self.platform=platform
    def collect(self):
        m=market(self.platform)
        return dict(markets={m.native_id:m},account={'total_equity':1.,'available_balance':1.,'currency':'USDT' if self.platform!='LIGHTER' else 'USDC'},positions=[],authenticated=True,errors=[])
    def get_quote(self,s):return dict(platform=self.platform,instId=s,last=100.,bid=99.,ask=101.,exchange_ts=int(time.time()*1000))

class PlatformIsolation(unittest.TestCase):
    def setUp(self):
        self.registry=PlatformRegistry();self.registry.make_connector=lambda name:Connector(name)
        for name in ('OKX','BINANCE','LIGHTER'):self.registry.refresh(name)
    def test_native_identity_and_maxima_are_not_copied(self):
        a=self.registry.get('OKX').view();b=self.registry.get('BINANCE').view();c=self.registry.get('LIGHTER').view()
        self.assertEqual((a['markets'][0]['id'],b['markets'][0]['id'],c['markets'][0]['id']),('BTC-USDT-SWAP','BTCUSDT','1'))
        self.assertEqual([r['markets'][0]['max_leverage'] for r in (a,b,c)],[None,50,20])
        self.assertEqual(a['markets'][0]['market_max_leverage'],100)
    def test_ratio_leverage_and_protection_stay_with_owner(self):
        self.registry.configure('BINANCE',{'action':'SET_RATIO','symbol':'BTCUSDT','ratio':.12345})
        self.registry.configure('BINANCE',{'action':'SET_LEVERAGE','symbol':'BTCUSDT','leverage':30})
        self.registry.configure('BINANCE',{'action':'SET_RISK','symbol':'BTCUSDT','settings':{'stop_loss_enabled':True}})
        self.assertEqual(self.registry.get('BINANCE').prefs('BTCUSDT')['leverage'],30)
        self.assertEqual(self.registry.get('OKX').prefs('BTC-USDT-SWAP')['leverage'],1)
        self.assertFalse(self.registry.get('OKX').prefs('BTC-USDT-SWAP')['protection']['stop_loss_enabled'])
    def test_account_tier_changes_with_planned_notional(self):
        b=self.registry.get('BINANCE');b.account['available_balance']=100
        self.assertEqual(b.allowed_max('BTCUSDT'),24)
        with self.assertRaises(ValueError):self.registry.configure('BINANCE',{'action':'SET_LEVERAGE','symbol':'BTCUSDT','leverage':50})
    def test_foreign_or_unknown_instrument_rejected(self):
        with self.assertRaises(ValueError):self.registry.configure('BINANCE',{'action':'SWITCH_SYMBOL','symbol':'BTC-USDT-SWAP'})
    def test_quotes_and_failed_refresh_do_not_cross_accounts(self):
        self.registry.refresh_quote('LIGHTER');self.assertFalse(self.registry.get('BINANCE').tickers)
        self.assertEqual(self.registry.get('LIGHTER').tickers['1']['platform'],'LIGHTER')
    def test_all_live_writes_remain_explicitly_blocked(self):
        for name in ('OKX','BINANCE','LIGHTER'):
            with self.assertRaises(PermissionError):self.registry.configure(name,{'action':'BUY_LONG'})
    def test_missing_or_expired_account_never_becomes_zero_funds_cap(self):
        b=self.registry.get('BINANCE');b.account=None
        self.assertIsNone(b.allowed_max('BTCUSDT'))
        b.account={'total_equity':100,'available_balance':100,'currency':'USDT'}
        self.assertEqual(b.allowed_max('BTCUSDT'),24)
        b.metadata_at-=6
        self.assertTrue(b.view()['account_valid'])
        self.assertEqual(b.allowed_max('BTCUSDT'),24)
        b.metadata_at-=20
        state=b.view()
        self.assertIsNone(state['selected_market']['max_leverage'])
        self.assertFalse(state['account_valid']);self.assertFalse(state['positions_verified'])

    def test_identical_refresh_preserves_catalog_revision(self):
        context=self.registry.get('BINANCE');revision=context.catalog_revision
        self.registry.refresh('BINANCE')
        self.assertEqual(context.catalog_revision,revision)

class CredentialSchema(unittest.TestCase):
    def test_labeled_file_and_json_are_redacted(self):
        c=parse_text('API Key\nfixture-key\n私钥：fixture-secret','BINANCE')
        self.assertEqual(c.values['api_key'],'fixture-key');self.assertEqual(c.values['secret_key'],'fixture-secret')
        self.assertNotIn('fixture-secret',repr(c))
        c=parse_text('{"account_index":12,"api_key_index":5,"private_key":"fixture"}','LIGHTER')
        self.assertEqual(c.values['account_index'],'12')

if __name__=='__main__':unittest.main()
