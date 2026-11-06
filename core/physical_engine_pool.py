import os
import base64
import hmac
import json
import logging
import socket
from urllib.parse import urlencode, urlsplit
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("OKX_ENGINE_POOL")

class OKXSession(requests.Session):
    """Never forward custom authentication headers through HTTP redirects."""
    def request(self, method, url, **kwargs):
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!='www.okx.com'
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None,443)):
            raise ValueError('Untrusted OKX request destination')
        kwargs['allow_redirects']=False
        response=super().request(method,url,**kwargs)
        if 300<=response.status_code<400:
            response.close()
            raise RuntimeError('OKX redirect rejected; authentication was not forwarded')
        return response

class PhysicalLimitEnginePool:
    """
    物理极限引擎池 (Physical Limit Engine Pool)
    - 针对 Windows TCP 协议栈启用 TCP_NODELAY
    - 50-slot lazy HTTP keep-alive pool (not 50 prewarmed sockets)
    - 预签名上下文缓存
    - Measured network latency is required; no nanosecond or fill guarantee
    """
    def __init__(self, api_key: str, secret_key: str, passphrase: str, base_url: str = "https://www.okx.com"):
        if base_url.rstrip('/')!='https://www.okx.com':
            raise ValueError('Only the verified OKX HTTPS origin is allowed')
        self.pos_mode = None
        self.market_cache = None
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")

        # 初始化 HTTP 极限连接池
        self.session = OKXSession()
        
        # 自定义底层 Socket 配置开启 TCP_NODELAY (同时支持直连与代理通道)
        class TCPNoDelayAdapter(HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                super().init_poolmanager(*args, **kwargs)
                if hasattr(self.poolmanager, 'connection_pool_kw'):
                    self.poolmanager.connection_pool_kw['socket_options'] = [
                        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    ]

            def proxy_manager_for(self, proxy, **proxy_kwargs):
                proxy_kwargs['socket_options'] = [
                    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                ]
                return super().proxy_manager_for(proxy, **proxy_kwargs)

        retries = Retry(total=0)
        adapter = TCPNoDelayAdapter(
            pool_connections=50,
            pool_maxsize=50,
            max_retries=retries
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": "OKX-Titan-HFT-Console/2.0",
            "Accept": "application/json"
        })

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        msg = f"{timestamp}{method.upper()}{request_path}{body}"
        return base64.b64encode(
            hmac.new(self.secret_key.encode("utf-8"), msg.encode("utf-8"), "sha256").digest()
        ).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = self._sign(ts, method, path, body)
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    # ==================== 市场数据 API ====================
    def get_ticker(self, inst_id: str) -> Optional[Dict[str, Any]]:
        """获取最新盘口 (bid, ask, last)"""
        if self.market_cache is not None:
            cached = self.market_cache.get(inst_id)
            if cached is not None:
                return cached
        path = f"/api/v5/market/ticker?instId={inst_id}"
        t0 = time.perf_counter()
        try:
            r = self.session.get(self.base_url + path, timeout=3)
            rtt = (time.perf_counter() - t0) * 1000
            data = r.json()
            if data.get("code") == "0" and data.get("data"):
                d = data["data"][0]
                return {
                    "instId": inst_id,
                    "last": float(d.get("last", 0)),
                    "bid": float(d.get("bidPx", 0)),
                    "ask": float(d.get("askPx", 0)),
                    "bidSz": float(d.get("bidSz", 0)),
                    "askSz": float(d.get("askSz", 0)),
                    "rtt_ms": round(rtt, 2)
                }
        except Exception as e:
            logger.error(f"get_ticker error for {inst_id}: {e}")
        return None

    # ==================== 账户资产 API ====================
    def get_account_balance(self, ccy='USDT'):
        rows=self.read_api('/api/v5/account/balance')
        if not rows: raise RuntimeError('Account balance response missing')
        for detail in rows[0].get('details',[]):
            if detail.get('ccy')==ccy:
                result={
                    'total_equity':float(detail.get('eq') or 0),
                    'available_balance':float(detail.get('availBal') or 0),
                    'frozen':float(detail.get('frozenBal') or 0),
                    'upl':float(detail.get('upl') or 0)}
                if not all(__import__('math').isfinite(v) for v in result.values()):
                    raise RuntimeError('Invalid account values')
                return result
        raise RuntimeError('Requested balance currency unavailable')

    # ==================== 杠杆调节 API ====================
    def set_leverage(self, inst_id: str, lever: int, mgn_mode: str = "cross") -> bool:
        """为指定品种设置杠杆倍数 (全仓 cross 模式)"""
        self.require_trading_authorization()
        path = "/api/v5/account/set-leverage"
        body = json.dumps({
            "instId": inst_id,
            "lever": str(lever),
            "mgnMode": mgn_mode
        })
        headers = self._headers("POST", path, body)
        try:
            r = self.session.post(self.base_url + path, headers=headers, data=body, timeout=3)
            data = r.json()
            return data.get("code") == "0"
        except Exception as e:
            logger.error(f"set_leverage error: {e}")
            return False

    # ==================== 极速发单 API ====================
    def place_order(
        self,
        inst_id: str,
        side: str,          # "buy" or "sell"
        pos_side: str,      # "net" for cross
        ord_type: str,      # "limit", "market", "post_only", "ioc"
        sz: str,
        px: Optional[str] = None,
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """下达订单 (支持极速市价与买一卖一挂单)"""
        self.require_trading_authorization()
        path = "/api/v5/trade/order"
        payload = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side,
            "posSide": pos_side,
            "ordType": ord_type,
            "sz": str(sz),
            "reduceOnly": reduce_only
        }
        if pos_side != 'net':
            # Hedge-mode close semantics use side + posSide; reduceOnly is net-only on OKX.
            payload.pop('reduceOnly', None)
        if px and ord_type != "market":
            payload["px"] = str(px)

        body = json.dumps(payload)
        headers = self._headers("POST", path, body)
        t0 = time.perf_counter()
        try:
            r = self.session.post(self.base_url + path, headers=headers, data=body, timeout=3)
            lat_ms = (time.perf_counter() - t0) * 1000
            data = r.json()
            if data.get("code") == "0" and data.get("data"):
                ord_info = data["data"][0]
                return {
                    "success": str(ord_info.get("sCode", "-1")) == "0",
                    "ordId": ord_info.get("ordId"),
                    "clOrdId": ord_info.get("clOrdId"),
                    "latency_ms": round(lat_ms, 2),
                    "raw": ord_info
                }
            else:
                return {
                    "success": False,
                    "code": data.get("code"),
                    "msg": data.get("msg"),
                    "latency_ms": round(lat_ms, 2)
                }
        except Exception as e:
            return {"success": False, "msg": str(e), "latency_ms": 999.0}

    # ==================== 极速撤单 API ====================
    def cancel_order(self, inst_id: str, ord_id: str) -> bool:
        self.require_trading_authorization()
        path = "/api/v5/trade/cancel-order"
        body = json.dumps({"instId": inst_id, "ordId": ord_id})
        headers = self._headers("POST", path, body)
        try:
            r = self.session.post(self.base_url + path, headers=headers, data=body, timeout=2)
            packet=r.json()
            return packet.get('code')=='0' and all(str(row.get('sCode','0'))=='0' for row in packet.get('data',[]))
        except Exception:
            return False

    # ==================== 极速一键强平 API ====================
    def close_position_instant(self, inst_id, mgn_mode='cross'):
        self.require_trading_authorization()
        for position in self.get_positions(inst_id):
            pos_side=position.get('posSide','net')
            side='buy' if pos_side=='short' or float(position['pos'])<0 else 'sell'
            result=self.place_order(inst_id,side,pos_side,'market',str(abs(float(position['pos']))),reduce_only=True)
            if not result.get('success'): return result
        return {'success':True,'msg':'Close submitted; confirmation required'}

    # ==================== 当前持仓查询 API ====================
    def get_position(self, inst_id: str) -> Optional[Dict[str, Any]]:
        path = f"/api/v5/account/positions?instId={inst_id}"
        headers = self._headers("GET", path)
        try:
            r = self.session.get(self.base_url + path, headers=headers, timeout=3)
            data = r.json()
            if data.get("code") == "0" and data.get("data"):
                pos_list = data["data"]
                for p in pos_list:
                    pos_sz = float(p.get("pos", 0))
                    if abs(pos_sz) > 0:
                        return {
                            "instId": inst_id,
                            "pos": pos_sz,
                            "posSide": p.get("posSide", "net"),
                            "side": "sell" if p.get("posSide") == "short" or pos_sz < 0 else "buy",
                            "avgPx": float(p.get("avgPx", 0)),
                            "upl": float(p.get("upl", 0)),
                            "uplRatio": float(p.get("uplRatio", 0)),
                            "margin": float(p.get("margin", 0)),
                            "lever": float(p.get("lever", 1)),
                            "notionalUsd": float(p.get("notionalUsd", 0))
                        }
        except Exception as e:
            logger.error(f"get_position error: {e}")
        return None

    # ==================== 交易所云端条件单 (防断网兜底) ====================
    def place_cloud_algo_stop(self, inst_id: str, side: str, trigger_px: str, sz: str) -> Dict[str, Any]:
        """向 OKX 服务器下达底层云端止损条件单 (防断网黑天鹅)"""
        self.require_trading_authorization()
        path = "/api/v5/trade/order-algo"
        payload = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side, # 平多则 sell, 平空则 buy
            "ordType": "conditional",
            "sz": str(sz),
            "tpTriggerPx": "",
            "tpOrdPx": "",
            "slTriggerPx": str(trigger_px),
            "slOrdPx": "-1", # -1 代表市价委托触发
            "reduceOnly": True
        }
        pos_side=self.position_side('buy' if side=='sell' else 'sell')
        payload['posSide']=pos_side
        if pos_side!='net': payload.pop('reduceOnly',None)
        body = json.dumps(payload)
        headers = self._headers("POST", path, body)
        try:
            r = self.session.post(self.base_url + path, headers=headers, data=body, timeout=3)
            return r.json()
        except Exception as e:
            return {"code": "-1", "msg": str(e)}

    @classmethod
    def is_live_authorized(cls) -> bool:
        return os.environ.get('ALLOW_LIVE_TRADING', '').lower() in ('1', 'true', 'yes')

    @classmethod
    def require_trading_authorization(cls):
        if not cls.is_live_authorized():
            raise PermissionError('LIVE=NO-GO: exchange mutations disabled in this build (安全保护生效中，设置 ALLOW_LIVE_TRADING=1 解锁实盘发单)')

    def read_api(self, path, params=None, private=True):
        if params:
            path += '?' + urlencode(params)
        last_exc = None
        for attempt in range(3):
            try:
                response = self.session.get(self.base_url+path,
                    headers=self._headers('GET',path) if private else None, timeout=6)
                response.raise_for_status()
                data = response.json()
                if data.get('code') != '0':
                    raise RuntimeError('OKX read rejected: code='+str(data.get('code')))
                return data.get('data',[])
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
        raise last_exc

    def detect_position_mode(self):
        rows = self.read_api('/api/v5/account/config')
        mode = rows[0].get('posMode') if rows else None
        if mode not in ('net_mode','long_short_mode'):
            raise RuntimeError('Unrecognized position mode')
        self.pos_mode = mode
        return mode

    def position_side(self, side):
        if self.pos_mode is None:
            self.detect_position_mode()
        return 'net' if self.pos_mode == 'net_mode' else ('long' if side == 'buy' else 'short')

    def get_order(self, inst_id, ord_id):
        rows = self.read_api('/api/v5/trade/order', {'instId':inst_id,'ordId':ord_id})
        return rows[0] if rows else None

    def get_positions(self, inst_id=None):
        rows = self.read_api('/api/v5/account/positions', {'instId':inst_id} if inst_id else None)
        return [p for p in rows if float(p.get('pos') or 0) != 0]

    def audit_instruments(self, specs):
        rows = self.read_api('/api/v5/public/instruments', {'instType':'SWAP'}, private=False)
        index = {row['instId']:row for row in rows}
        result = []
        for spec in specs:
            row = index.get(spec.inst_id)
            if not row or row.get('state') != 'live':
                raise RuntimeError('Unavailable OKX instrument: '+spec.inst_id)
            for attr,key in [('ct_val','ctVal'),('min_sz','minSz'),('lot_sz','lotSz'),('tick_sz','tickSz')]:
                value = float(row[key])
                if value <= 0:
                    raise RuntimeError('Invalid metadata: '+spec.inst_id+' '+key)
                setattr(spec,attr,value)
            spec.max_leverage = int(float(row['lever']))
            result.append(row)
        return result

    def close(self):
        self.session.close()

    def write_api(self,path,payload):
        self.require_trading_authorization()
        body=json.dumps(payload)
        response=self.session.post(self.base_url+path,headers=self._headers('POST',path,body),data=body,timeout=4)
        response.raise_for_status()
        packet=response.json()
        if packet.get('code')!='0' or any(str(row.get('sCode','0'))!='0' for row in packet.get('data',[])):
            raise RuntimeError('OKX mutation rejected')
        return packet

    def cancel_all_orders(self, inst_id=None):
        self.require_trading_authorization()
        params={'instId':inst_id} if inst_id else None
        for order in self.read_api('/api/v5/trade/orders-pending',params):
            if not self.cancel_order(order['instId'],order['ordId']):
                raise RuntimeError('Cancel not accepted')
        for kind in ('conditional','oco','trigger','move_order_stop'):
            query={'ordType':kind}
            if inst_id: query['instId']=inst_id
            for order in self.read_api('/api/v5/trade/orders-algo-pending',query):
                self.write_api('/api/v5/trade/cancel-algos',[{'instId':order['instId'],'algoId':order['algoId']}])
        if self.read_api('/api/v5/trade/orders-pending',params):
            raise RuntimeError('Cancellation pending; inventory not empty')

    def cancel_entry_orders(self,inst_id=None):
        self.require_trading_authorization()
        params={'instId':inst_id} if inst_id else None
        for order in self.read_api('/api/v5/trade/orders-pending',params):
            if not self.cancel_order(order['instId'],order['ordId']):
                raise RuntimeError('Order cancellation not accepted')
        if self.read_api('/api/v5/trade/orders-pending',params):
            raise RuntimeError('Order cancellation awaiting finality')

    def cancel_protective_orders(self,inst_id=None):
        self.require_trading_authorization()
        for kind in ('conditional','oco','trigger','move_order_stop'):
            query={'ordType':kind}
            if inst_id:query['instId']=inst_id
            for order in self.read_api('/api/v5/trade/orders-algo-pending',query):
                self.write_api('/api/v5/trade/cancel-algos',[{'instId':order['instId'],'algoId':order['algoId']}])
            if self.read_api('/api/v5/trade/orders-algo-pending',query):
                raise RuntimeError('Protective cancellation awaiting finality')
