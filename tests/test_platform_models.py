import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.platform_models import parse_okx,parse_binance,parse_hyperliquid,parse_lighter

class NativeMarketModels(unittest.TestCase):
    def test_okx_contract_units_and_cap(self):
        row=parse_okx({'instId':'BTC-USDT-SWAP','state':'live','ctType':'linear','settleCcy':'USDT','ctVal':'0.01','minSz':'0.01','lotSz':'0.01','tickSz':'0.1','lever':'100'})
        self.assertEqual(row.native_id,'BTC-USDT-SWAP');self.assertEqual(str(row.contract_value),'0.01');self.assertEqual(row.max_leverage,100)
    def test_binance_never_reuses_okx_max(self):
        native={'symbol':'BTCUSDT','status':'TRADING','contractType':'PERPETUAL','marginAsset':'USDT','filters':[{'filterType':'LOT_SIZE','minQty':'0.001','stepSize':'0.001'},{'filterType':'PRICE_FILTER','tickSize':'0.10'},{'filterType':'MIN_NOTIONAL','notional':'5'}]}
        unknown=parse_binance(native,[]);self.assertIsNone(unknown.max_leverage)
        row=parse_binance(native,[{'notionalFloor':0,'notionalCap':1000,'initialLeverage':50},{'notionalFloor':1000,'notionalCap':10000,'initialLeverage':20}])
        self.assertEqual(row.native_id,'BTCUSDT');self.assertEqual(row.leverage_limit(999),50);self.assertEqual(row.leverage_limit(1000),20)
    def test_hyper_native_dex_name_and_variable_tick(self):
        row=parse_hyperliquid({'name':'xyz:TSLA','szDecimals':3,'maxLeverage':10,'marginTableId':7},[[7,{'marginTiers':[{'lowerBound':'0','maxLeverage':10},{'lowerBound':'1000','maxLeverage':5}]}]])
        self.assertEqual(row.native_id,'xyz:TSLA');self.assertIsNone(row.tick_size);self.assertEqual(row.price_significant_digits,5)
        self.assertEqual(row.leverage_limit(1500),5)
    def test_lighter_margin_fraction_and_market_id(self):
        row=parse_lighter({'symbol':'BTC','market_id':1,'status':'active','min_initial_margin_fraction':200,'min_base_amount':'0.0001','min_quote_amount':'10','supported_size_decimals':5,'supported_price_decimals':1})
        self.assertEqual(row.native_id,'1');self.assertEqual(row.max_leverage,50);self.assertEqual(row.label,'BTC')
    def test_invalid_specs_do_not_create_fake_limits(self):
        with self.assertRaises(ValueError):parse_lighter({'symbol':'BTC','market_id':1,'status':'active','min_initial_margin_fraction':'NaN'})

if __name__=='__main__':unittest.main()
