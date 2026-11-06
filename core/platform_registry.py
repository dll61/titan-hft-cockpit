"""Per-platform state. Platform switching never changes exchange state."""
import copy,threading,time,json,os,math,tempfile
from pathlib import Path
from dataclasses import dataclass,field
from core.platform_connectors import BinanceReadConnector,HyperliquidReadConnector,LighterReadConnector,APIReadError,quote,finite,snapshot
from core.platform_models import parse_okx
from config.platform_credentials import load_platform
from core.isolated_risk_ledger import IsolatedRiskLedger

PLATFORMS={'OKX':'OKX','BINANCE':'Binance Futures','LIGHTER':'Lighter','HYPERLIQUID':'Hyperliquid'}
DEFAULT_PROTECTION=dict(stop_loss_enabled=False,stop_loss_pct=.05,breakeven_enabled=False,trailing_enabled=False,
    fixed_take_profit_pct=None,trailing_retrace_ratio=.30,breakeven_activation=.035,trailing_activation=.02,
    stop_loss_amount=None,fixed_take_profit_amount=None,trailing_activation_amount=None,trailing_retrace_amount=None)
TRADE_ACTIONS={'BUY_LONG','SELL_SHORT','PARTIAL_CLOSE','FLATTEN_CURRENT','FLATTEN_ALL','CANCEL_ALL'}
ACCOUNT_MAX_AGE=25.

class OKXReadConnector:
    def __init__(self,engine):
        self.engine=engine
        self._cached_instruments=None
    def collect(self):
        result=snapshot()
        if not self._cached_instruments:
            self._cached_instruments=list(self.engine.read_api('/api/v5/public/instruments',{'instType':'SWAP'},False))
        for row in self._cached_instruments:
            try:m=parse_okx(row);result['markets'][m.native_id]=m
            except (ValueError,KeyError,TypeError):continue
        try:
            self.engine.detect_position_mode();result['account']=self.engine.get_account_balance();result['account']['currency']='USDT'
            for row in self.engine.get_positions():
                size=finite(row['pos']);market=result['markets'].get(row['instId'])
                result['positions'].append(dict(inst_id=row['instId'],size=abs(size),side='sell' if row.get('posSide')=='short' or size<0 else 'buy',
                    entry_price=finite(row.get('avgPx') or 0),margin=finite(row.get('imr') or row.get('margin') or 0),leverage=finite(row.get('lever') or 0),
                    ct_val=float(market.contract_value) if market else None,notional=abs(finite(row.get('notionalUsd') or 0)),source='exchange_read_only'))
            result['authenticated']=True
        except Exception as exc:result['errors'].append('OKX account unavailable: '+type(exc).__name__)
        return result
    def get_quote(self,market):
        cache=getattr(self.engine,'market_cache',None)
        cached=cache.get(market) if cache is not None else None
        if cached:
            return {**cached,'platform':'OKX','transport':'public_WS','timestamp_source':'exchange'}
        rows=self.engine.read_api('/api/v5/market/ticker',{'instId':market},False)
        if not rows or rows[0].get('instId')!=market:raise APIReadError('OKX: wrong market response')
        q=rows[0];return quote('OKX',market,q['last'],q['bidPx'],q['askPx'],q.get('ts'))

@dataclass
class PlatformContext:
    platform:str
    connector:object=None
    markets:dict=field(default_factory=dict)
    account:object=None
    accounts_by_scope:dict=field(default_factory=dict)
    positions:list=field(default_factory=list)
    tickers:dict=field(default_factory=dict)
    preferences:dict=field(default_factory=dict)
    slots:dict=field(default_factory=lambda:{'slot_a':{'symbol':''},'slot_b':{'symbol':''}})
    focused_symbol:str=''
    active_slot:str='slot_a'
    errors:list=field(default_factory=list)
    authenticated:bool=False
    loading:bool=False
    catalog_revision:int=0
    metadata_at:float=0
    last_refresh_attempt_at:float=0
    authentication_kind:str='not_verified'
    execution_mode:str='balanced'
    max_slippage_bps:float=10
    account_breaker:dict=field(default_factory=lambda:dict(enabled=False,drawdown_pct=.08,high_water=0.,triggered=False))
    lock:object=field(default_factory=threading.RLock,repr=False)

    def prefs(self,symbol):
        return self.preferences.setdefault(symbol,dict(leverage=1,ratio=.2,protection=dict(DEFAULT_PROTECTION)))

    @property
    def catalog(self):
        return self.markets

    @property
    def protection(self):
        return {s: p.get('protection', dict(DEFAULT_PROTECTION)) for s, p in self.preferences.items()}
    def selected_account(self,symbol):
        if self.platform=='HYPERLIQUID':return self.accounts_by_scope.get(symbol.split(':',1)[0] if ':' in symbol else '',self.account if ':' not in symbol else None)
        return self.account
    def allowed_max(self,symbol,ratio=None):
        market=self.markets.get(symbol)
        if not market or time.monotonic()-self.metadata_at>300:return None
        maximum=market.max_leverage
        if maximum is None:return None
        prefs=self.prefs(symbol);funds=self.selected_account(symbol)
        if not funds or not self.authenticated or time.monotonic()-self.metadata_at>ACCOUNT_MAX_AGE:return None
        # Public instrument lever is only a reference, not an account-tier proof.
        if self.platform=='OKX' and not market.tiers:return None
        base=max(0.,funds['available_balance'])
        fraction=prefs['ratio'] if ratio is None else ratio
        exposure=sum(float(p.get('notional') or 0) for p in self.positions if p.get('inst_id')==symbol)
        valid=[level for level in range(1,maximum+1) if (cap:=market.leverage_limit(exposure+base*fraction*level)) is not None and level<=cap]
        return max(valid) if valid else None
    def view(self,include_catalog=True):
        with self.lock:
            funds=self.selected_account(self.focused_symbol);fresh=time.monotonic()-self.metadata_at<=300
            account_fresh=self.metadata_at>0 and time.monotonic()-self.metadata_at<=ACCOUNT_MAX_AGE
            markets=[]
            if include_catalog:
                for market in self.markets.values():
                    row=market.view();row['max_leverage']=self.allowed_max(market.native_id) if fresh else None;row['metadata_verified']=fresh;markets.append(row)
            selected_market=self.markets.get(self.focused_symbol)
            selected_view=selected_market.view() if selected_market else None
            if selected_view is not None:
                selected_view['max_leverage']=self.allowed_max(self.focused_symbol)
                selected_view['metadata_verified']=fresh
            selected=self.prefs(self.focused_symbol) if self.focused_symbol else dict(leverage=1,ratio=.2,protection=dict(DEFAULT_PROTECTION))
            visible={self.focused_symbol,*[v['symbol'] for v in self.slots.values()],*[p['inst_id'] for p in self.positions]}
            is_live = os.environ.get('ALLOW_LIVE_TRADING', '').lower() in ('1', 'true', 'yes')
            can_trade = is_live and self.platform in ('OKX', 'BINANCE')
            caps = dict(open_allowed=can_trade, close_allowed=can_trade,
                        reason='' if can_trade else ('平台只读安全模式：实盘开平仓未解锁（启动加 --live 或设 ALLOW_LIVE_TRADING=1 解锁）' if not is_live else f'{self.platform} 实盘交易尚未接入'))
            mode = 'LIVE' if is_live else 'LIVE=NO-GO'
            result=dict(selected_market=selected_view,platform=self.platform,mode=mode,capabilities=caps,
                focused_symbol=self.focused_symbol,active_slot=self.active_slot,slots=copy.deepcopy(self.slots),catalog_revision=self.catalog_revision,
                metadata_at=self.metadata_at,account=funds or dict(total_equity=0.,available_balance=0.,currency='UNVERIFIED'),
                snapshot_age_seconds=max(0.,time.monotonic()-self.metadata_at) if self.metadata_at else None,authentication_kind=self.authentication_kind,
                account_valid=bool(funds) and account_fresh,authenticated=self.authenticated,positions_verified=bool(funds) and self.authenticated and account_fresh,
                exchange_positions=copy.deepcopy(self.positions),tickers=copy.deepcopy(self.tickers),feed_connected=bool(self.tickers),
                leverage_map={s:p['leverage'] for s,p in self.preferences.items() if s in visible},ratio_map={s:p['ratio'] for s,p in self.preferences.items() if s in visible},
                protection={s:copy.deepcopy(p['protection']) for s,p in self.preferences.items() if s in visible},position_ratio=selected['ratio'],
                execution_mode=self.execution_mode,max_slippage_bps=self.max_slippage_bps,account_breaker=dict(self.account_breaker),logs=list(self.errors),
                connection_status='CONNECTING' if self.loading else 'ACCOUNT_READ_READY' if self.authenticated else 'PUBLIC_DATA_ONLY' if self.markets else 'NOT_CONNECTED')
            if include_catalog:result['markets']=markets
            return result

class PlatformRegistry:
    SETTINGS_FIELDS=('preferences','slots','focused_symbol','active_slot','execution_mode','max_slippage_bps','account_breaker')
    def __init__(self,state_path=None):
        self.contexts={key:PlatformContext(key) for key in PLATFORMS};self.okx_engine=None
        self.state_path=Path(state_path) if state_path else None
        self.settings_lock=threading.RLock();self.saved_settings={};self.settings_error=''
        if self.state_path and self.state_path.exists():
            try:
                data=json.loads(self.state_path.read_text(encoding='utf-8'))
                if set(data)!={'version','platforms'} or data['version']!=1:raise ValueError('schema')
                for platform,settings in data['platforms'].items():
                    if platform not in PLATFORMS:raise ValueError('platform')
                    settings.setdefault('account_breaker',dict(enabled=False,drawdown_pct=.08))
                    for prefs in settings.get('preferences',{}).values():
                        for key in ('stop_loss_amount','fixed_take_profit_amount','trailing_activation_amount','trailing_retrace_amount'):
                            prefs.get('protection',{}).setdefault(key,None)
                    self._validate_saved(settings)
                self.saved_settings=data['platforms']
                for platform,settings in self.saved_settings.items():
                    for key,value in settings.items():
                        if key=='account_breaker':self.get(platform).account_breaker.update(copy.deepcopy(value))
                        else:setattr(self.get(platform),key,copy.deepcopy(value))
            except Exception:
                self.settings_error='Saved settings invalid or unreadable; manual defaults active. Settings writes blocked.'
                for context in self.contexts.values():context.errors.append(self.settings_error)

    @classmethod
    def _validate_saved(cls,settings):
        if set(settings)!=set(cls.SETTINGS_FIELDS):raise ValueError('settings schema')
        breaker=settings['account_breaker']
        if set(breaker)!={'enabled','drawdown_pct'} or type(breaker['enabled']) is not bool:raise ValueError('breaker schema')
        pct=breaker['drawdown_pct']
        if type(pct) not in (int,float) or not math.isfinite(pct) or not 0<pct<1:raise ValueError('breaker threshold')
        for symbol,prefs in settings['preferences'].items():
            if not isinstance(symbol,str) or not symbol or len(symbol)>200:raise ValueError('symbol')
            if set(prefs)!={'ratio','leverage','protection'}:raise ValueError('preferences schema')
            if type(prefs['ratio']) not in (int,float) or not math.isfinite(prefs['ratio']) or not 0<prefs['ratio']<=1:raise ValueError('ratio')
            if type(prefs['leverage']) is not int or not 1<=prefs['leverage']<=1000:raise ValueError('leverage')
            if set(prefs['protection'])!=set(DEFAULT_PROTECTION):raise ValueError('protection schema')
            IsolatedRiskLedger.validate_settings(prefs['protection'])
        if set(settings['slots'])!={'slot_a','slot_b'} or settings['active_slot'] not in settings['slots']:raise ValueError('slots')
        for slot in settings['slots'].values():
            if set(slot)!={'symbol'} or not isinstance(slot['symbol'],str):raise ValueError('slot')
        if not isinstance(settings['focused_symbol'],str):raise ValueError('focus')
        if settings['execution_mode'] not in ('balanced','speed','maker_only'):raise ValueError('execution')
        bps=settings['max_slippage_bps']
        if type(bps) not in (int,float) or not math.isfinite(bps) or not 0<bps<=100:raise ValueError('slippage')

    def _persist(self,platform):
        if self.state_path is None:return
        if self.settings_error:raise RuntimeError('Saved settings require repair')
        settings={k:copy.deepcopy(getattr(self.get(platform),k)) for k in self.SETTINGS_FIELDS}
        settings['account_breaker']={k:settings['account_breaker'][k] for k in ('enabled','drawdown_pct')}
        self._validate_saved(settings)
        updated=copy.deepcopy(self.saved_settings);updated[platform]=settings
        self.state_path.parent.mkdir(parents=True,exist_ok=True)
        name=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.state_path.parent,delete=False) as handle:
                name=handle.name
                json.dump(dict(version=1,platforms=updated),handle,ensure_ascii=False,allow_nan=False)
                handle.flush();os.fsync(handle.fileno())
            os.replace(name,self.state_path);self.saved_settings=updated
        finally:
            if name and os.path.exists(name):os.unlink(name)
    def get(self,platform):
        if platform not in self.contexts:raise ValueError('Unknown platform')
        return self.contexts[platform]
    def make_connector(self,platform):
        if platform=='OKX':
            if self.okx_engine is None:raise APIReadError('OKX credentials/engine unavailable')
            return OKXReadConnector(self.okx_engine)
        try:credentials=load_platform(platform).values
        except (OSError,ValueError):credentials={}
        factory={'BINANCE':BinanceReadConnector,'HYPERLIQUID':HyperliquidReadConnector,'LIGHTER':LighterReadConnector}
        return factory[platform](credentials)
    def refresh(self,platform):
        context=self.get(platform)
        context.last_refresh_attempt_at=time.monotonic()
        try:
            connector=context.connector or self.make_connector(platform)
            result=connector.collect()
            with context.lock:
                changed=(context.markets!=result['markets'] or context.account!=result['account']
                    or context.accounts_by_scope!=result.get('accounts_by_scope',{}) or context.positions!=result['positions']
                    or context.authenticated!=result['authenticated'])
                context.connector=connector;context.markets=result['markets'];context.account=result['account'];context.accounts_by_scope=result.get('accounts_by_scope',{})
                context.positions=result['positions'];context.authenticated=result['authenticated'];context.errors=result['errors']+([self.settings_error] if self.settings_error else []);context.tickers={}
                context.authentication_kind=result.get('authentication_kind','signed_read' if context.authenticated else 'not_verified')
                context.metadata_at=time.monotonic()
                if changed:context.catalog_revision+=1
                for symbol in context.markets:context.prefs(symbol)
                if context.focused_symbol not in context.markets:
                    favorite=next((s for s in ('BTC-USDT-SWAP','BTCUSDT','BTC') if s in context.markets),next(iter(context.markets),''))
                    context.focused_symbol=favorite;context.slots['slot_a']['symbol']=favorite
                    context.slots['slot_b']['symbol']=next((s for s in context.markets if s!=favorite),favorite)
        except Exception as exc:
            with context.lock:context.errors=[str(exc) if isinstance(exc,APIReadError) else platform+': '+type(exc).__name__];context.authenticated=False;context.account=None;context.accounts_by_scope={};context.tickers={}
        finally:context.loading=False
    def refresh_quote(self,platform):
        context=self.get(platform)
        with context.lock:connector=context.connector;symbol=context.focused_symbol
        if connector is None or not symbol:return
        try:
            q=connector.get_quote(symbol)
            with context.lock:
                if q.get('platform')!=platform or q.get('instId')!=symbol:raise APIReadError('Quote owner mismatch')
                if context.focused_symbol!=symbol:return
                context.tickers={symbol:q}
        except Exception as exc:
            with context.lock:context.tickers={};context.errors=[platform+' quote unavailable: '+type(exc).__name__]
    def configure(self,platform,command):
        context=self.get(platform)
        with self.settings_lock,context.lock:
            keys=(*self.SETTINGS_FIELDS,'account_breaker','tickers','errors')
            before={k:copy.deepcopy(getattr(context,k)) for k in keys}
            try:
                result=self._configure(platform,command)
                try:self._persist(platform)
                except Exception:raise RuntimeError('Settings could not be saved; change rolled back') from None
                return result
            except Exception:
                for key,value in before.items():setattr(context,key,value)
                raise

    def _configure(self,platform,command):
        context=self.get(platform);action=command.get('action')
        with context.lock:
            if action in TRADE_ACTIONS:raise PermissionError('实盘执行未验收；切换平台不会自动解除交易门禁')
            symbol=command.get('symbol') or context.focused_symbol
            if action=='SWITCH_SLOT':
                slot=command.get('slot')
                if slot not in context.slots:raise ValueError('Unknown slot')
                context.active_slot=slot;context.focused_symbol=context.slots[slot]['symbol']
                return
            if symbol not in context.markets:raise ValueError('请先连接平台并选择已核验的原生标的')
            prefs=context.prefs(symbol)
            if action=='SWITCH_SYMBOL':context.focused_symbol=symbol;context.slots[context.active_slot]['symbol']=symbol;context.tickers={}
            elif action=='SET_LEVERAGE':
                value=command.get('leverage');market=context.markets.get(symbol);maximum=context.allowed_max(symbol) or (market.max_leverage if market else None)
                if type(value) is not int or maximum is None or not 1<=value<=maximum:raise ValueError('当前标的最大杠杆未核验或所选倍数越界')
                prefs['leverage']=value
            elif action=='SET_RATIO':
                value=command.get('ratio')
                if type(value) not in (int,float) or not __import__('math').isfinite(value) or not 0<value<=1:raise ValueError('Invalid ratio')
                prefs['ratio']=value;cap=context.allowed_max(symbol)
                if cap is not None and prefs['leverage']>cap:
                    prefs['leverage']=cap;context.errors.insert(0,'仓位比例改变风险档位，杠杆配置已收紧至'+str(cap)+'x')
            elif action in ('SET_RISK','SET_STOP_LOSS'):
                settings=command.get('settings') if action=='SET_RISK' else {'stop_loss_pct':command.get('pct')}
                if not isinstance(settings,dict):raise ValueError('Invalid protection settings')
                IsolatedRiskLedger.validate_settings(settings);prefs['protection'].update(settings)
            elif action=='SET_EXECUTION':
                mode=command.get('mode');bps=command.get('bps')
                if mode not in ('balanced','speed','maker_only') or type(bps) not in (float,int) or not __import__('math').isfinite(bps) or not 0<bps<=100:raise ValueError('Invalid execution preference')
                context.execution_mode=mode;context.max_slippage_bps=bps
            elif action=='SET_ACCOUNT_BREAKER':
                enabled,pct=command.get('enabled'),command.get('pct')
                if type(enabled) is not bool or type(pct) not in (float,int) or not __import__('math').isfinite(pct) or not 0<pct<1:raise ValueError('Invalid breaker')
                context.account_breaker.update(enabled=enabled,drawdown_pct=pct,triggered=False,high_water=(context.selected_account(symbol) or {}).get('total_equity',0))
            else:raise ValueError('Unknown platform command')
