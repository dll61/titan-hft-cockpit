"""Official HTTP read transports. Connecting never places/cancels orders or changes leverage."""
import hashlib,hmac,time
from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlencode
import requests
from core.platform_models import parse_okx,parse_binance,parse_hyperliquid,parse_lighter

HOSTS={'BINANCE':'https://fapi.binance.com','HYPERLIQUID':'https://api.hyperliquid.xyz','LIGHTER':'https://mainnet.zklighter.elliot.ai'}

class APIReadError(RuntimeError):pass

def finite(value):
    try:number=Decimal(str(value))
    except Exception:raise APIReadError('Malformed numeric response') from None
    if not number.is_finite():raise APIReadError('Non-finite API response')
    return float(number)

def quote(platform,market,last,bid,ask,stamp=None):
    last,bid,ask=map(finite,(last,bid,ask))
    if min(last,bid,ask)<=0 or bid>ask:raise APIReadError('Invalid order book quote')
    return dict(platform=platform,instId=market,last=last,bid=bid,ask=ask,
        exchange_ts=int(stamp) if stamp else None,received_ts=int(time.time()*1000),
        timestamp_source='exchange' if stamp else 'local_receive_time',transport='REST_read_only')

def snapshot():return dict(markets={},account=None,accounts_by_scope={},positions=[],authenticated=False,errors=[])

class ReadConnector:
    def __init__(self,platform,credentials=None,session=None):
        self.platform=platform;self.credentials=credentials or {};self.session=session or requests.Session();self.host=HOSTS[platform]
    def call(self,method,path,**kwargs):
        try:
            kwargs['allow_redirects']=False
            response=self.session.request(method,self.host+path,timeout=4,**kwargs)
            if 300 <= getattr(response,'status_code',200) < 400:
                raise APIReadError('Redirect refused')
            response.raise_for_status();data=response.json()
        except Exception as exc:raise APIReadError(self.platform+': '+type(exc).__name__) from None
        if isinstance(data,dict) and isinstance(data.get('code'),int) and data['code']<0:
            raise APIReadError(self.platform+' API code '+str(data['code']))
        return data
    def close(self):self.session.close()
    def require_trading_authorization(self):raise PermissionError('LIVE=NO-GO: transport connected read-only; no trading approval')

class BinanceReadConnector(ReadConnector):
    def __init__(self,credentials=None,session=None):
        super().__init__('BINANCE',credentials,session)
        self.offset_ms=0
        self._sync_time()

    def _sync_time(self):
        try:
            t0 = time.time()
            remote = self.call('GET', '/fapi/v1/time')
            t1 = time.time()
            server_time = int(remote['serverTime'])
            local_time = int((t0 + t1) / 2 * 1000)
            self.offset_ms = (server_time - local_time) - 1000
        except Exception:
            pass

    def signed(self,path,params=None):
        if not all(self.credentials.get(k) for k in ('api_key','secret_key')):raise APIReadError('BINANCE: CREDENTIALS_MISSING')
        if self.offset_ms == 0:
            self._sync_time()
        for attempt in range(2):
            values=dict(params or {},timestamp=int(time.time()*1000)+self.offset_ms,recvWindow=10000)
            query=urlencode(values);signature=hmac.new(self.credentials['secret_key'].encode(),query.encode(),hashlib.sha256).hexdigest()
            try:
                return self.call('GET',path,params=query+'&signature='+signature,headers={'X-MBX-APIKEY':self.credentials['api_key']})
            except Exception as e:
                if '-1021' in str(e) and attempt == 0:
                    self._sync_time()
                    continue
                raise
    def collect(self):
        result=snapshot();info=self.call('GET','/fapi/v1/exchangeInfo');brackets={}
        try:
            remote=self.call('GET','/fapi/v1/time');self.offset_ms=int(remote['serverTime'])-int(time.time()*1000)
            limits=self.signed('/fapi/v1/leverageBracket')
            if isinstance(limits,dict):limits=[limits]
            brackets={row['symbol']:row['brackets'] for row in limits}
            account=self.signed('/fapi/v3/account')
            result['account']={'total_equity':finite(account['totalMarginBalance']),'available_balance':finite(account['availableBalance']),
                'currency':'USD' if account.get('multiAssetsMargin') else 'USDT'}
            positions=self.signed('/fapi/v3/positionRisk')
            for p in positions:
                size=finite(p['positionAmt'])
                if not size:continue
                result['positions'].append(dict(inst_id=p['symbol'],size=abs(size),side='sell' if p.get('positionSide')=='SHORT' or size<0 else 'buy',
                    entry_price=finite(p['entryPrice']),margin=finite(p.get('positionInitialMargin') or 0),leverage=finite(p.get('leverage') or 0),
                    ct_val=1.,notional=abs(finite(p.get('notional') or 0)),source='exchange_read_only'))
            result['authenticated']=True
        except (APIReadError,KeyError,TypeError,ValueError) as exc:result['errors'].append(str(exc) if isinstance(exc,APIReadError) else 'BINANCE: malformed private API response')
        for row in info.get('symbols',[]):
            try:market=parse_binance(row,brackets.get(row.get('symbol'),[]));result['markets'][market.native_id]=market
            except (ValueError,KeyError,TypeError):continue
        return result
    def get_quote(self,market):
        book=self.call('GET','/fapi/v1/ticker/bookTicker',params={'symbol':market})
        last=self.call('GET','/fapi/v2/ticker/price',params={'symbol':market})
        if book.get('symbol')!=market or last.get('symbol')!=market:raise APIReadError('BINANCE: wrong market response')
        return quote(self.platform,market,last['price'],book['bidPrice'],book['askPrice'],book.get('time'))

class HyperliquidReadConnector(ReadConnector):
    ALLOWED={'perpDexs','meta','spotMeta','clearinghouseState','l2Book','userFees','openOrders','orderStatus'}
    def __init__(self,credentials=None,session=None):super().__init__('HYPERLIQUID',credentials,session)
    def info(self,kind,**params):
        if kind not in self.ALLOWED:raise APIReadError('HYPERLIQUID: unapproved info operation')
        return self.call('POST','/info',json={'type':kind,**params})
    def collect(self):
        result=snapshot();dexs=self.info('perpDexs');scopes=['']+[row['name'] for row in dexs if row and row.get('name')]
        tokens={}
        try:tokens={row['index']:row['name'] for row in self.info('spotMeta').get('tokens',[])}
        except (APIReadError,KeyError,TypeError):result['errors'].append('HYPERLIQUID: collateral token metadata unavailable')
        address=self.credentials.get('address')
        for dex in dict.fromkeys(scopes):
            try:
                meta=self.info('meta',dex=dex);currency=tokens.get(meta.get('collateralToken'),'USDC' if not dex else 'UNVERIFIED')
                for row in meta.get('universe',[]):
                    try:
                        market=replace(parse_hyperliquid(row,meta.get('marginTables',[])),quote_currency=currency)
                        result['markets'][market.native_id]=market
                    except (ValueError,KeyError,TypeError):continue
                if address:
                    account=self.info('clearinghouseState',user=address,dex=dex)
                    summary=account.get('marginSummary',{})
                    funds={'total_equity':finite(summary['accountValue']),'available_balance':finite(account['withdrawable']),'currency':currency}
                    scope_positions=[]
                    inventory=account['assetPositions']
                    if not isinstance(inventory,list):raise APIReadError('HYPERLIQUID: missing complete position inventory')
                    for wrapped in inventory:
                        p=wrapped['position'];size=finite(p['szi'])
                        if not size:continue
                        name=p['coin'];name=name if name in result['markets'] else dex+':'+name
                        scope_positions.append(dict(inst_id=name,size=abs(size),side='buy' if size>0 else 'sell',
                            entry_price=finite(p['entryPx']),margin=finite(p['marginUsed']),leverage=finite(p.get('leverage',{}).get('value',0)),
                            ct_val=1.,notional=abs(finite(p['positionValue'])),scope=dex,source='exchange_read_only'))
                    # Publish a scope only when its full position inventory parsed.
                    result['accounts_by_scope'][dex]=funds
                    result['positions'].extend(scope_positions)
                    if not dex:result['account']=funds;result['authenticated']=True
            except (APIReadError,KeyError,TypeError,ValueError):result['errors'].append('HYPERLIQUID: scope unavailable '+dex)
        if not address:result['errors'].append('HYPERLIQUID: ACCOUNT_ADDRESS_MISSING')
        # Account identity read is not proof that a signing key may trade.
        result['authentication_kind']='public_account_read'
        return result
    def get_quote(self,market):
        book=self.info('l2Book',coin=market)
        if book.get('coin')!=market:raise APIReadError('HYPERLIQUID: wrong market response')
        bid,ask=book['levels'][0][0]['px'],book['levels'][1][0]['px']
        # Midpoint is labeled as such; no fabricated last trade.
        result=quote(self.platform,market,(finite(bid)+finite(ask))/2,bid,ask,book.get('time'));result['price_kind']='midpoint';return result

class LighterReadConnector(ReadConnector):
    def __init__(self,credentials=None,session=None):super().__init__('LIGHTER',credentials,session)
    def checked(self,path,params=None):
        data=self.call('GET',path,params=params)
        if data.get('code')!=200:raise APIReadError('LIGHTER API code '+str(data.get('code')))
        return data
    def collect(self):
        result=snapshot();details=self.checked('/api/v1/orderBookDetails')
        assets={}
        try:
            response=self.checked('/api/v1/assetDetails')
            assets={row['asset_id']:row['symbol'] for row in response.get('asset_details',[])}
        except (APIReadError,KeyError,TypeError):result['errors'].append('LIGHTER: quote-asset metadata unavailable')
        for row in details.get('order_book_details',[]):
            try:
                market=replace(parse_lighter(row),quote_currency=assets.get(row.get('quote_asset_id'),'UNVERIFIED'))
                result['markets'][market.native_id]=market
            except (ValueError,KeyError,TypeError):continue
        index=self.credentials.get('account_index')
        if index is None:result['errors'].append('LIGHTER: ACCOUNT_INDEX_MISSING');return result
        try:
            account=self.checked('/api/v1/account',{'by':'index','value':str(int(index))})['accounts'][0]
            if int(account.get('account_index',account.get('index',-1)))!=int(index):raise APIReadError('LIGHTER: wrong account response')
            result['account']={'total_equity':finite(account['total_asset_value']),'available_balance':finite(account['available_balance']),'currency':'USDC'}
            inventory=account['positions']
            if not isinstance(inventory,list):raise APIReadError('LIGHTER: missing complete position inventory')
            for p in inventory:
                size=finite(p['position'])
                if not size:continue
                imf=finite(p.get('initial_margin_fraction') or 0)
                result['positions'].append(dict(inst_id=str(p['market_id']),size=abs(size),side='sell' if int(p['sign'])<0 else 'buy',
                    entry_price=finite(p['avg_entry_price']),margin=finite(p.get('allocated_margin') or 0),leverage=10000/imf if imf>0 else 0,
                    ct_val=1.,notional=abs(finite(p['position_value'])),source='exchange_read_only'))
            result['authenticated']=True;result['authentication_kind']='public_account_read'
        except (APIReadError,KeyError,TypeError,ValueError):result['errors'].append('LIGHTER: account unavailable')
        return result
    def get_quote(self,market):
        book=self.checked('/api/v1/orderBookOrders',{'market_id':int(market),'limit':1})
        details=self.checked('/api/v1/orderBookDetails',{'market_id':int(market)})['order_book_details']
        matching=[row for row in details if str(row['market_id'])==str(market)]
        if not matching:raise APIReadError('LIGHTER: wrong market response')
        return quote(self.platform,str(market),matching[0]['last_trade_price'],book['bids'][0]['price'],book['asks'][0]['price'])
