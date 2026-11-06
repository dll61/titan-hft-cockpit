import time, hmac, hashlib, requests
from decimal import Decimal
from urllib.parse import urlencode
"""
Universal Multi-Exchange Adapter Protocol & Factory
OKX delegation plus explicit extension points. Binance/Lighter are not implemented.
Decouples all risk engines, smart routers, and Web UI from exchange-specific APIs.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger("EXCHANGE_ADAPTER")

class BaseExchangeAdapter(ABC):
    """
    统一跨交易所高频交易核心契约接口 (Abstract Base Exchange Adapter)
    任何接入的交易平台（OKX、Binance、Lighter DEX、Hyperliquid）均须严格实现以下物理接口。
    """
    name: str = "BASE"

    def position_side(self, side: str, default: str = "net") -> str:
        """根据交易所持仓模式自动推导 posSide"""
        mode = self.detect_position_mode()
        if mode == "long_short_mode":
            return "long" if side.lower() == "buy" else "short"
        return default

    @abstractmethod
    def get_ticker(self, inst_id: str) -> Optional[Dict[str, Any]]:
        """获取最新盘口价格 (last, bid, ask, rtt_ms)"""
        pass

    @abstractmethod
    def get_account_balance(self, ccy: str = "USDT") -> Dict[str, float]:
        """获取账户总权益与可用保证金 {'total_equity': float, 'available_balance': float}"""
        pass

    @abstractmethod
    def get_positions(self, inst_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取实时持仓列表"""
        pass

    @abstractmethod
    def get_order(self, inst_id: str, ord_id: str) -> Optional[Dict[str, Any]]:
        """查询指定订单状态 (state, accFillSz, fillPx 等)"""
        pass

    @abstractmethod
    def place_order(
        self,
        inst_id: str,
        side: str,
        pos_side: str,
        ord_type: str,
        sz: str,
        px: Optional[str] = None
    ) -> Dict[str, Any]:
        """统一发单接口 (支持市价、限价、Post-Only、IOC)"""
        pass

    @abstractmethod
    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        """撤销指定订单"""
        pass

    @abstractmethod
    def close_position_instant(self, inst_id: str) -> Dict[str, Any]:
        """特权通道一键瞬间平仓 (清空当前标的全部仓位)"""
        pass

    @abstractmethod
    def cancel_all_orders(self, inst_id: Optional[str] = None) -> Dict[str, Any]:
        """全量撤销挂单"""
        pass

    @abstractmethod
    def detect_position_mode(self) -> str:
        """探测当前持仓模式 ('net_mode' 或 'long_short_mode')"""
        pass

    @abstractmethod
    def audit_instruments(self, specs: List[Any]) -> List[Dict[str, Any]]:
        """批量审计标的元数据规格与交易所合规性"""
        pass


class OKXAdapter(BaseExchangeAdapter):
    """OKX 官方物理极限引擎适配器"""
    name = "OKX"

    def __init__(self, engine_pool: Any):
        self.engine = engine_pool

    def position_side(self, side: str, default: str = "net") -> str:
        if hasattr(self.engine, "position_side"):
            return self.engine.position_side(side)
        return super().position_side(side, default)

    def get_ticker(self, inst_id: str) -> Optional[Dict[str, Any]]:
        return self.engine.get_ticker(inst_id)

    def get_account_balance(self, ccy: str = "USDT") -> Dict[str, float]:
        return self.engine.get_account_balance(ccy)

    def get_positions(self, inst_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.engine.get_positions(inst_id)

    def get_order(self, inst_id: str, ord_id: str) -> Optional[Dict[str, Any]]:
        return self.engine.get_order(inst_id, ord_id)

    def place_order(self,inst_id,side,pos_side,ord_type,sz,px=None,reduce_only=False):
        return self.engine.place_order(inst_id,side,pos_side,ord_type,sz,px,reduce_only=reduce_only)

    def require_trading_authorization(self):
        return self.engine.require_trading_authorization()

    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        return self.engine.cancel_order(inst_id, ord_id)

    def close_position_instant(self, inst_id: str) -> Dict[str, Any]:
        return self.engine.close_position_instant(inst_id)

    def cancel_all_orders(self, inst_id: Optional[str] = None) -> Dict[str, Any]:
        return self.engine.cancel_all_orders(inst_id)

    def detect_position_mode(self) -> str:
        return self.engine.detect_position_mode()

    def audit_instruments(self, specs: List[Any]) -> List[Dict[str, Any]]:
        return self.engine.audit_instruments(specs)


class UnimplementedExchangeAdapter(BaseExchangeAdapter):
    """Reserved interface. It must never return fabricated quotes, balances or fills."""
    implemented=False
    def unavailable(self,*args,**kwargs):
        raise NotImplementedError(self.name+' transport is not implemented; no real order sent')
    get_ticker=unavailable
    get_account_balance=unavailable
    get_positions=unavailable
    get_order=unavailable
    place_order=unavailable
    cancel_order=unavailable
    close_position_instant=unavailable
    cancel_all_orders=unavailable
    detect_position_mode=unavailable
    audit_instruments=unavailable
    require_trading_authorization=unavailable

class BinanceFuturesAdapter(BaseExchangeAdapter):
    name = 'BINANCE_FUTURES'
    def __init__(self, api_key='', secret_key='', base_url='https://fapi.binance.com'):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.offset_ms = 0
        self._sync_time()

    def _sync_time(self):
        try:
            t0 = time.time()
            r = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=3).json()
            t1 = time.time()
            server_time = int(r['serverTime'])
            local_time = int((t0 + t1) / 2 * 1000)
            # 扣除 1000ms 作为安全裕量，确保发往币安的 timestamp 绝不会由于本地时钟偏快而触碰 (+1000ms) 超前报错
            # 在 recvWindow=10000ms 宽容度下，提前 1000ms 处于完全合法的接收时间窗口内
            self.offset_ms = (server_time - local_time) - 1000
        except Exception:
            pass

    def _require_creds(self):
        if not self.api_key or not self.secret_key:
            raise NotImplementedError('BINANCE_FUTURES transport is not implemented; no real order sent')

    def require_trading_authorization(self):
        self._require_creds()
        from core.physical_engine_pool import PhysicalLimitEnginePool
        if not PhysicalLimitEnginePool.is_live_authorized():
            raise PermissionError('LIVE=NO-GO: exchange mutations disabled in this build')

    def _signed(self, method, path, params=None):
        self._require_creds()
        if self.offset_ms == 0:
            self._sync_time()
        for attempt in range(2):
            data = dict(params or {})
            data['timestamp'] = int(time.time() * 1000) + self.offset_ms
            data['recvWindow'] = 10000
            query = urlencode(data)
            signature = hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
            full_query = query + '&signature=' + signature
            headers = {'X-MBX-APIKEY': self.api_key}
            if method == 'GET':
                resp = self.session.get(f"{self.base_url}{path}?{full_query}", headers=headers, timeout=5)
            elif method == 'POST':
                resp = self.session.post(f"{self.base_url}{path}?{full_query}", headers=headers, timeout=5)
            elif method == 'DELETE':
                resp = self.session.delete(f"{self.base_url}{path}?{full_query}", headers=headers, timeout=5)
            else:
                raise ValueError(f"Unsupported method {method}")
            res = resp.json()
            if isinstance(res, dict) and res.get('code') == -1021 and attempt == 0:
                self._sync_time()
                continue
            if isinstance(res, dict) and res.get('code') and res['code'] < 0:
                raise RuntimeError(f"Binance API error [{res['code']}]: {res.get('msg')}")
            return res

    def _clean_symbol(self, inst_id: Optional[str]) -> str:
        if not inst_id:
            return ""
        s = str(inst_id).upper().strip()
        if '-' in s:
            s = s.replace('-USDT-SWAP', 'USDT').replace('-SWAP', '').replace('-', '')
        return s

    def get_ticker(self, inst_id: str):
        self._require_creds()
        inst_id = self._clean_symbol(inst_id)
        t0 = time.monotonic()
        resp = self.session.get(f"{self.base_url}/fapi/v1/ticker/bookTicker", params={'symbol': inst_id}, timeout=3).json()
        rtt = (time.monotonic() - t0) * 1000
        bid = float(resp['bidPrice'])
        ask = float(resp['askPrice'])
        return {'instId': inst_id, 'bid': bid, 'ask': ask, 'last': (bid + ask) / 2, 'rtt_ms': rtt}

    def get_account_balance(self, ccy: str = 'USDT'):
        self._require_creds()
        acc = self._signed('GET', '/fapi/v3/account')
        return {
            'total_equity': float(acc.get('totalMarginBalance', 0)),
            'available_balance': float(acc.get('availableBalance', 0))
        }

    def get_positions(self, inst_id: Optional[str] = None):
        self._require_creds()
        positions = self._signed('GET', '/fapi/v3/positionRisk')
        results = []
        for p in positions:
            if inst_id and p['symbol'] != inst_id:
                continue
            amt = float(p.get('positionAmt', 0))
            if amt != 0:
                results.append({
                    'instId': p['symbol'],
                    'pos': str(abs(amt)),
                    'posSide': 'long' if p.get('positionSide') == 'LONG' or amt > 0 else 'short',
                    'margin': float(p.get('positionInitialMargin', 0)),
                    'avgPx': float(p.get('entryPrice', 0)),
                    'ctVal': 1.0
                })
        return results

    def get_order(self, inst_id: str, ord_id: str):
        self._require_creds()
        inst_id = self._clean_symbol(inst_id)
        res = self._signed('GET', '/fapi/v1/order', {'symbol': inst_id, 'orderId': ord_id})
        status = res.get('status', '').upper()
        state = 'filled' if status == 'FILLED' else 'canceled' if status in ('CANCELED', 'EXPIRED') else 'live'
        return {
            'instId': inst_id,
            'ordId': str(res.get('orderId')),
            'state': state,
            'accFillSz': str(res.get('executedQty', '0')),
            'avgPx': str(res.get('avgPrice') or res.get('price') or '0')
        }

    def place_order(self, inst_id: str, side: str, pos_side: str, ord_type: str, sz: str, px: Optional[str] = None, reduce_only: bool = False):
        self.require_trading_authorization()
        inst_id = self._clean_symbol(inst_id)
        params = {
            'symbol': inst_id,
            'side': side.upper(),
            'quantity': str(sz),
        }
        mode = self.detect_position_mode()
        if mode == 'long_short_mode':
            params['positionSide'] = 'LONG' if side.lower() == 'buy' and not reduce_only else ('SHORT' if side.lower() == 'sell' and not reduce_only else ('LONG' if side.lower() == 'sell' else 'SHORT'))
        else:
            params['positionSide'] = 'BOTH'
            if reduce_only:
                params['reduceOnly'] = 'true'

        if ord_type in ('post_only', 'maker'):
            params['type'] = 'LIMIT'
            params['timeInForce'] = 'GTX'
            params['price'] = str(px)
        elif ord_type == 'limit':
            params['type'] = 'LIMIT'
            params['timeInForce'] = 'GTC'
            params['price'] = str(px)
        elif ord_type == 'ioc':
            params['type'] = 'LIMIT'
            params['timeInForce'] = 'IOC'
            params['price'] = str(px)
        elif ord_type == 'market':
            params['type'] = 'MARKET'
        
        res = self._signed('POST', '/fapi/v1/order', params)
        status = res.get('status', '').upper()
        state = 'filled' if status == 'FILLED' else 'canceled' if status in ('CANCELED', 'EXPIRED') else 'live'

        if ord_type == 'market' and status not in ('FILLED', 'CANCELED', 'EXPIRED'):
            for _ in range(5):
                time.sleep(0.04)
                try:
                    o = self.get_order(inst_id, str(res.get('orderId')))
                    if o and o.get('state') == 'filled':
                        res['status'] = 'FILLED'
                        status = 'FILLED'
                        state = 'filled'
                        res['executedQty'] = o.get('accFillSz')
                        res['avgPrice'] = o.get('avgPx')
                        break
                except Exception:
                    pass
        
        # 稳健解析真实成交均价与成交数量（币安市价单返回 avgPrice 可能为 '0.00000'）
        exec_qty = float(res.get('executedQty') or 0)
        cum_quote = float(res.get('cumQuote') or 0)
        avg_px_val = float(res.get('avgPrice') or 0)
        if avg_px_val <= 0 and exec_qty > 0 and cum_quote > 0:
            avg_px_val = cum_quote / exec_qty
        if avg_px_val <= 0:
            avg_px_val = float(res.get('price') or 0)
        if avg_px_val <= 0 and px:
            try: avg_px_val = float(px)
            except Exception: pass
        if avg_px_val <= 0:
            try:
                t = self.get_ticker(inst_id)
                avg_px_val = float(t.get('last') or 0)
            except Exception: pass
            
        final_sz = exec_qty if (state == 'filled' or exec_qty > 0) else 0.0

        return {
            'success': True,
            'ordId': str(res.get('orderId')),
            'state': state,
            'accFillSz': str(final_sz),
            'avgPx': str(avg_px_val),
            'fill_px': avg_px_val,
            'sz': final_sz
        }

    def execute_smart_entry(self, inst_id: str, side: str, pos_side: str, sz: str, execution_mode: str = 'balanced', timeout_ms: int = 250):
        self.require_trading_authorization()
        inst_id = self._clean_symbol(inst_id)
        t0 = time.monotonic()
        if execution_mode == 'speed':
            res = self.place_order(inst_id, side, pos_side, 'market', sz)
            elapsed = (time.monotonic() - t0) * 1000
            res['execution_mode'] = 'SPEED_MARKET'
            res['elapsed_ms'] = round(elapsed, 2)
            return res

        try:
            t = self.get_ticker(inst_id)
            bid = Decimal(str(t['bid']))
            ask = Decimal(str(t['ask']))
        except Exception:
            res = self.place_order(inst_id, side, pos_side, 'market', sz)
            res['execution_mode'] = 'MARKET_TICKER_FAIL'
            res['elapsed_ms'] = round((time.monotonic() - t0) * 1000, 2)
            return res

        if side.lower() == 'buy':
            target_px = (bid + Decimal('0.01')) if (ask - bid > Decimal('0.01')) else bid
        else:
            target_px = (ask - Decimal('0.01')) if (ask - bid > Decimal('0.01')) else ask
        
        px_str = f"{target_px:.2f}"
        
        maker_res = None
        try:
            res = self.place_order(inst_id, side, pos_side, 'post_only', sz, px=px_str)
            if res.get('success') and res.get('ordId'):
                maker_res = res
        except Exception:
            maker_res = None

        if maker_res:
            ord_id = maker_res['ordId']
            if maker_res.get('state') == 'filled':
                maker_res['execution_mode'] = 'MAKER_GTX_INSTANT'
                maker_res['elapsed_ms'] = round((time.monotonic() - t0) * 1000, 2)
                return maker_res

            start_wait = time.monotonic()
            while (time.monotonic() - start_wait) * 1000 < timeout_ms:
                time.sleep(0.03)
                try:
                    chk = self.get_order(inst_id, ord_id)
                    if chk.get('state') in ('filled', 'canceled', 'expired'):
                        if chk.get('state') == 'filled':
                            elapsed = (time.monotonic() - t0) * 1000
                            return {
                                'success': True,
                                'ordId': ord_id,
                                'state': 'filled',
                                'sz': float(chk.get('accFillSz') or sz),
                                'fill_px': float(chk.get('avgPx') or target_px),
                                'avgPx': str(chk.get('avgPx') or target_px),
                                'accFillSz': str(chk.get('accFillSz') or sz),
                                'execution_mode': 'MAKER_GTX_PEGGED',
                                'elapsed_ms': round(elapsed, 2)
                            }
                        break
                except Exception:
                    pass

            if execution_mode == 'maker_only':
                elapsed = (time.monotonic() - t0) * 1000
                maker_res['execution_mode'] = 'MAKER_ONLY_RESTING'
                maker_res['elapsed_ms'] = round(elapsed, 2)
                return maker_res

            try:
                self.cancel_order(inst_id, ord_id)
            except Exception:
                pass
            
            try:
                final_chk = self.get_order(inst_id, ord_id)
                filled = float(final_chk.get('accFillSz') or 0)
            except Exception:
                filled = 0.0

            remaining = float(sz) - filled
            if remaining <= 0:
                elapsed = (time.monotonic() - t0) * 1000
                return {
                    'success': True,
                    'ordId': ord_id,
                    'state': 'filled',
                    'sz': filled,
                    'fill_px': float(final_chk.get('avgPx') or target_px),
                    'avgPx': str(final_chk.get('avgPx') or target_px),
                    'accFillSz': str(filled),
                    'execution_mode': 'MAKER_GTX_FILLED',
                    'elapsed_ms': round(elapsed, 2)
                }
            
            rem_str = f"{remaining:.2f}"
            mres = self.place_order(inst_id, side, pos_side, 'market', rem_str)
            elapsed = (time.monotonic() - t0) * 1000
            mres['execution_mode'] = 'MARKET_FALLBACK'
            mres['elapsed_ms'] = round(elapsed, 2)
            return mres

        if execution_mode == 'maker_only':
            raise RuntimeError("Maker-only 挂单已被拦截：价格已穿越，为免收取吃单手续费已自动终止")

        mres = self.place_order(inst_id, side, pos_side, 'market', sz)
        elapsed = (time.monotonic() - t0) * 1000
        mres['execution_mode'] = 'MARKET_CROSS_FALLBACK'
        mres['elapsed_ms'] = round(elapsed, 2)
        return mres

    def cancel_order(self, inst_id: str, ord_id: str):
        self.require_trading_authorization()
        inst_id = self._clean_symbol(inst_id)
        res = self._signed('DELETE', '/fapi/v1/order', {'symbol': inst_id, 'orderId': ord_id})
        return {'success': True, 'ordId': str(res.get('orderId'))}

    def cancel_all_orders(self, inst_id: Optional[str] = None):
        self.require_trading_authorization()
        if inst_id:
            inst_id = self._clean_symbol(inst_id)
        if inst_id:
            res = self._signed('DELETE', '/fapi/v1/allOpenOrders', {'symbol': inst_id})
        return {'success': True}

    def close_position_instant(self, inst_id: str):
        self.require_trading_authorization()
        inst_id = self._clean_symbol(inst_id)
        positions = self.get_positions(inst_id)
        if not positions:
            return {'success': True, 'msg': 'No open position to close'}
        for p in positions:
            amt = float(p['pos'])
            close_side = 'sell' if p['posSide'] == 'long' else 'buy'
            self.place_order(inst_id, close_side, p['posSide'], 'market', str(amt), reduce_only=True)
        return {'success': True}

    def detect_position_mode(self):
        self._require_creds()
        res = self._signed('GET', '/fapi/v1/positionSide/dual')
        return 'long_short_mode' if res.get('dualSidePosition') else 'net_mode'

    def audit_instruments(self, specs):
        return []

    def set_leverage(self, inst_id: str, leverage: int):
        self.require_trading_authorization()
        inst_id = self._clean_symbol(inst_id)
        res = self._signed('POST', '/fapi/v1/leverage', {'symbol': inst_id, 'leverage': int(leverage)})
        return bool(res.get('leverage') == int(leverage))

class LighterExchangeAdapter(UnimplementedExchangeAdapter):
    name='LIGHTER_DEX'
    def __init__(self,private_key='',api_auth='',base_url='https://api.lighter.xyz'):
        self.base_url=base_url

class ExchangeFactory:
    """
    多交易所工厂中枢 (Exchange Factory)
    支持统一通过配置名称 'OKX', 'BINANCE', 'LIGHTER' 动态实例化对应适配器
    """
    _registry: Dict[str, type] = {
        "OKX": OKXAdapter,
        "BINANCE": BinanceFuturesAdapter,
        "LIGHTER": LighterExchangeAdapter
    }

    @classmethod
    def register_adapter(cls, name: str, adapter_cls: type) -> None:
        cls._registry[name.upper()] = adapter_cls

    @classmethod
    def create_adapter(cls, exchange_name: str, **kwargs) -> BaseExchangeAdapter:
        key = exchange_name.upper()
        if key not in cls._registry:
            raise ValueError(f"Unknown exchange: {exchange_name}. Available: {list(cls._registry.keys())}")
        adapter_cls = cls._registry[key]
        return adapter_cls(**kwargs)
