"""Local read-only console. Exchange mutations remain disabled (LIVE=NO-GO)."""
import asyncio
import json
import logging
import hashlib
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from config.credentials_loader import CredentialsLoader
from config.instruments_catalog import CORE_INSTRUMENTS, list_active_instruments, get_instrument_spec
from core.physical_engine_pool import PhysicalLimitEnginePool
from core.exchange_adapter import BinanceFuturesAdapter
from config.platform_credentials import load_platform

try:
    _bn_creds = load_platform('BINANCE').values
    binance_adapter = BinanceFuturesAdapter(
        api_key=_bn_creds.get('api_key', ''),
        secret_key=_bn_creds.get('secret_key', '')
    )
except Exception:
    binance_adapter = None
from core.isolated_risk_ledger import IsolatedRiskLedger
from core.smart_order_router import SmartOrderRouter
from core.position_calculator import PositionCalculator
from core.okx_ws_client import OKXPublicWS
from core.platform_registry import PlatformRegistry, PLATFORMS, DEFAULT_PROTECTION

engine_pool=None
smart_router=None
risk_ledger=IsolatedRiskLedger()
API_POSITIONS_PATH = ROOT / 'state/api_opened_positions.json'
api_opened_positions = {}
if API_POSITIONS_PATH.exists():
    try:
        api_opened_positions = json.loads(API_POSITIONS_PATH.read_text(encoding='utf-8'))
    except Exception:
        api_opened_positions = {}

def save_api_opened_positions():
    try:
        API_POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        API_POSITIONS_PATH.write_text(json.dumps(api_opened_positions, indent=2, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass

def prevent_windows_sleep():
    if sys.platform == 'win32':
        try:
            import ctypes
            # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001) | ES_AWAYMODE_REQUIRED (0x00000040)
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        except Exception:
            pass

def restore_active_positions_into_risk_ledger():
    if not api_opened_positions:
        return
    for sym, pos_data in list(api_opened_positions.items()):
        if sym not in risk_ledger.ledgers:
            try:
                fill_sz = float(pos_data.get('sz', 0))
                fill_px = float(pos_data.get('fill_px', 0))
                side = 'buy' if pos_data.get('side') == 'long' else 'sell'
                lever = 20
                if 'BINANCE' in platform_registry.contexts:
                    lever = platform_registry.get('BINANCE').prefs(sym).get('leverage', 20)
                init_margin = fill_sz * fill_px / lever if lever > 0 else 0
                prot = platform_registry.get('BINANCE').prefs(sym).get('protection', DEFAULT_PROTECTION.copy()) if 'BINANCE' in platform_registry.contexts else DEFAULT_PROTECTION.copy()
                if fill_sz > 0 and fill_px > 0 and init_margin > 0:
                    risk_ledger.register_entry(sym, side, fill_sz, fill_px, init_margin, lever, prot.get('stop_loss_pct', 0.05))
                    risk_ledger.configure(sym, **prot)
                    add_log(f"[灾备恢复] 成功将开机前未平API持仓挂入高频风控状态机: {sym} 数量:{fill_sz}")
            except Exception as e:
                add_log(f"[灾备恢复异常] {sym}: {e}")

feed=OKXPublicWS([s.inst_id for s in list_active_instruments()])
active_connections=[]
connection_platforms={}
connection_catalog_versions={}
platform_registry=PlatformRegistry(state_path=ROOT/'state/preferences.json')
command_lock=asyncio.Lock()
runtime_state=dict(focused_symbol='SOXL-USDT-SWAP',active_slot='slot_a',
    slots={'slot_a':{'symbol':'SOXL-USDT-SWAP'},'slot_b':{'symbol':'TSLA-USDT-SWAP'}},
    leverage_map={s.inst_id:1 for s in list_active_instruments()},ratio_map={s.inst_id:.2 for s in list_active_instruments()},position_ratio=.2,stop_loss_pct=.05,
    protection={s.inst_id:dict(DEFAULT_PROTECTION) for s in list_active_instruments()},
    execution_mode='balanced',max_slippage_bps=10,
    account_breaker=dict(enabled=False,drawdown_pct=.08,high_water=0.,triggered=False),
    account=dict(total_equity=0.,available_balance=0.),account_valid=False,authenticated=False,
    positions_verified=False,exchange_positions=[],tickers={},logs=[],mode='LIVE=NO-GO',feed_connected=False)

def add_log(message):
    runtime_state['logs'].insert(0,time.strftime('%H:%M:%S')+' '+message)
    del runtime_state['logs'][50:]

async def refresh_balance_once(now):
    last=runtime_state.get('balance_polled_at',float('-inf'))
    if engine_pool is not None and now-last>=2.0:
        runtime_state['balance_polled_at']=now
        runtime_state['account']=await asyncio.to_thread(engine_pool.get_account_balance)
        runtime_state['account_valid']=True
        check_account_breaker(runtime_state['account'].get('total_equity',0))

from core.build_identity import build_identity
BUILD_ID=build_identity()

def public_runtime_status():
    is_live = PhysicalLimitEnginePool.is_live_authorized()
    return {'capabilities':{'open_allowed':is_live,'close_allowed':is_live,
            'reason':'' if is_live else '只读安全模式：实盘开平仓未解锁（启动加 --live 或设 ALLOW_LIVE_TRADING=1 解锁）'},
        'mode':'LIVE' if is_live else 'LIVE=NO-GO',
        'account_valid':runtime_state.get('account_valid',False),
        'authenticated':runtime_state.get('authenticated',False),
        'positions_verified':runtime_state.get('positions_verified',False),
        'feed_error':feed.last_error,'build_id':BUILD_ID}

def position_view(row):
    size=float(row.get('pos') or 0)
    inst=row['instId']
    is_api=inst in api_opened_positions
    return dict(inst_id=inst,size=abs(size),side='sell' if row.get('posSide')=='short' or size<0 else 'buy',
        entry_price=float(row.get('avgPx') or 0),leverage=float(row.get('lever') or 1),
        margin=float(row.get('imr') or row.get('margin') or 0),ct_val=None,
        is_api_managed=is_api,
        source='api_managed' if is_api else 'external_protected',
        status='API开仓(风控追踪)' if is_api else '外部仓位(严密保护·API不干预)')

async def refresh_positions_once(now):
    if engine_pool is None or now-runtime_state.get('positions_polled_at',float('-inf'))<2: return
    runtime_state['positions_polled_at']=now
    rows=await asyncio.to_thread(engine_pool.get_positions)
    runtime_state['exchange_positions']=[position_view(row) for row in rows]
    runtime_state['positions_verified']=True

def check_account_breaker(equity):
    breaker=runtime_state['account_breaker']
    if equity<=0 or not math.isfinite(equity): return False
    breaker['high_water']=max(breaker['high_water'],equity)
    if breaker['enabled'] and equity<=breaker['high_water']*(1-breaker['drawdown_pct']):
        breaker['triggered']=True
        runtime_state['risk_alert']='账户净值熔断触发；LIVE=NO-GO 模式仅告警'
    return breaker['triggered']

async def flatten_symbol(symbol):
    try:
        if binance_adapter and (symbol.endswith('USDT') and '-' not in symbol):
            if symbol not in api_opened_positions:
                add_log(f"[安全拦截] 拒绝平仓外部仓位: {symbol} (非API开仓，严密保护用户手动持仓)")
                return {'success': False, 'error': f'外部仓位受保护: {symbol}'}
            binance_adapter.require_trading_authorization()
            res = await asyncio.to_thread(binance_adapter.close_position_instant, symbol)
            await asyncio.to_thread(binance_adapter.cancel_all_orders, symbol)
            result = {'success': True, 'exchange_response': res}
            api_opened_positions.pop(symbol, None)
            save_api_opened_positions()
        else:
            result = await asyncio.to_thread(smart_router.execute_emergency_flatten, symbol)
    except Exception as exc:
        result = {'success': False, 'error': type(exc).__name__}
    if result.get('success'):
        risk_ledger.close_ledger(symbol)
    else:
        add_log('平仓未确认，保留监控: ' + symbol)
    return result

async def cancel_before_flatten(symbol=None):
    try:
        cancel=getattr(engine_pool,'cancel_entry_orders',None) or engine_pool.cancel_all_orders
        await asyncio.to_thread(cancel,symbol)
        return True
    except Exception as exc:
        runtime_state['reconciliation_required']=True
        add_log('撤单未确认，继续尝试降低持仓风险: '+type(exc).__name__)
        return False

async def cleanup_after_flatten(symbol=None):
    # Preserve exchange-side protection until the position is confirmed flat.
    cleanup=getattr(engine_pool,'cancel_protective_orders',None)
    if cleanup is None: return True
    try:
        await asyncio.to_thread(cleanup,symbol)
        return True
    except Exception as exc:
        runtime_state['reconciliation_required']=True
        add_log('残留保护单清理未确认: '+type(exc).__name__)
        return False

async def handle_command(cmd):
    action=cmd.get('action')
    symbol=cmd.get('symbol',runtime_state['focused_symbol'])
    if not isinstance(symbol,str) or symbol not in CORE_INSTRUMENTS: raise ValueError('Unknown symbol')
    spec=get_instrument_spec(symbol)
    if action=='PING': return {'type':'PONG','sent_at':cmd.get('sent_at')}
    if action=='SWITCH_SYMBOL':
        runtime_state['focused_symbol']=symbol
        runtime_state['slots'][runtime_state['active_slot']]['symbol']=symbol
    elif action=='SWITCH_SLOT':
        slot=cmd.get('slot')
        if slot not in runtime_state['slots']: raise ValueError('Unknown slot')
        runtime_state['active_slot']=slot
        runtime_state['focused_symbol']=runtime_state['slots'][slot]['symbol']
    elif action=='SET_RATIO':
        if type(cmd.get('ratio')) not in (int,float): raise ValueError('Ratio must be a finite number')
        value=float(cmd['ratio'])
        if not math.isfinite(value) or not 0<value<=1: raise ValueError('Invalid ratio')
        runtime_state['position_ratio']=value
        runtime_state['ratio_map'][symbol]=value
    elif action=='SET_EXECUTION':
        mode=cmd.get('mode'); bps=float(cmd.get('bps',10))
        if mode not in ('balanced','speed','maker_only') or not math.isfinite(bps) or not 0<bps<=100:
            raise ValueError('Invalid execution policy')
        runtime_state.update(execution_mode=mode,max_slippage_bps=bps)
    elif action=='SET_ACCOUNT_BREAKER':
        enabled=cmd.get('enabled')
        pct=float(cmd.get('pct',.08))
        if type(enabled) is not bool or not math.isfinite(pct) or not 0<pct<1:
            raise ValueError('Invalid account breaker')
        breaker=runtime_state['account_breaker']
        breaker.update(enabled=enabled,drawdown_pct=pct,triggered=False,
            high_water=runtime_state['account'].get('total_equity',0))
    elif action=='SET_RISK':
        settings=cmd.get('settings')
        if not isinstance(settings,dict): raise ValueError('Invalid protection settings')
        # Validate atomically using the same domain validator even while flat.
        probe=IsolatedRiskLedger()
        probe.register_entry(symbol,'buy',1,1,1,1)
        probe.configure(symbol,**settings)
        runtime_state['protection'][symbol].update(settings)
        if symbol in risk_ledger.ledgers: risk_ledger.configure(symbol,**settings)
        add_log('保护设置已更新: '+symbol+' '+','.join(settings))
    elif action=='SET_STOP_LOSS':
        value=float(cmd['pct'])
        IsolatedRiskLedger.validate_settings({'stop_loss_pct':value})
        runtime_state['stop_loss_pct']=value
        runtime_state['protection'][symbol]['stop_loss_pct']=value
        if symbol in risk_ledger.ledgers: risk_ledger.ledgers[symbol].stop_loss_pct=value
    elif action=='SET_LEVERAGE':
        runtime_state['leverage_map'][symbol]=PositionCalculator.sanitize_leverage(int(cmd['leverage']),spec.max_leverage)
    elif action in ('BUY_LONG','SELL_SHORT','FLATTEN_CURRENT','FLATTEN_ALL','PARTIAL_CLOSE','CANCEL_ALL'):
        if engine_pool is None: raise RuntimeError('Exchange not initialized')
        engine_pool.require_trading_authorization()
        if action=='FLATTEN_CURRENT':
            canceled=await cancel_before_flatten(symbol)
            result=await flatten_symbol(symbol)
            clean=await cleanup_after_flatten(symbol) if result.get('success') else False
            result['success']=bool(result.get('success') and canceled and clean)
            return result
        if action=='FLATTEN_ALL':
            canceled=await cancel_before_flatten()
            inventory_ok=True
            try: positions=await asyncio.to_thread(engine_pool.get_positions)
            except Exception:
                positions=[];inventory_ok=False
                runtime_state['reconciliation_required']=True
            symbols=set(p['instId'] for p in positions)|set(risk_ledger.ledgers)
            results={}
            for sym in sorted(symbols): results[sym]=await flatten_symbol(sym)
            closed=all(r.get('success') for r in results.values())
            clean=await cleanup_after_flatten() if closed and inventory_ok else False
            return {'success':bool(canceled and inventory_ok and closed and clean),'results':results}
        if action=='CANCEL_ALL':
            await asyncio.to_thread(engine_pool.cancel_all_orders)
            return {'success':True}
        if action=='PARTIAL_CLOSE':
            p=risk_ledger.ledgers.get(symbol)
            if not p: return {'success':True}
            step=Decimal(str(spec.lot_sz))
            half=(Decimal(str(p.size))/2/step).to_integral_value(rounding=ROUND_FLOOR)*step
            if half<Decimal(str(spec.min_sz)): raise ValueError('Half position below minimum lot')
            result=await asyncio.to_thread(smart_router.execute_partial_close,spec,p.side,float(half))
            if not result.get('success') or not result.get('ordId'): return result
            order=await asyncio.to_thread(engine_pool.get_order,symbol,result['ordId'])
            if not order or order.get('state') not in ('filled','canceled'):
                raise RuntimeError('Partial close awaiting confirmation; ledger retained')
            qty=float(order.get('accFillSz') or 0)
            if not 0<=qty<=p.size: raise RuntimeError('Invalid close fill quantity')
            fraction=(p.size-qty)/p.size
            p.size-=qty; p.initial_margin*=fraction; p.high_water_mark_usd*=fraction
            if p.size==0: risk_ledger.close_ledger(symbol)
            return result
        if action in ('BUY_LONG','SELL_SHORT'):
            if runtime_state.get('reconciliation_required'): raise RuntimeError('Unresolved fills; reconcile before new entries')
            side='buy' if action=='BUY_LONG' else 'sell'
            old=risk_ledger.ledgers.get(symbol)
            if old and old.side!=side: raise ValueError('Close opposing position before opening new direction')
            quote=feed.get(symbol)
            if not quote and engine_pool is not None:
                quote=await asyncio.to_thread(engine_pool.get_ticker, symbol)
            if not quote: raise RuntimeError('Fresh exchange BBO required')
            balance=await asyncio.to_thread(engine_pool.get_account_balance)
            available=balance['available_balance']
            base=available if old else min(balance['total_equity'],available)
            lever=runtime_state['leverage_map'][symbol]
            if not await asyncio.to_thread(engine_pool.set_leverage,symbol,lever):
                raise RuntimeError('Exchange leverage not confirmed')
            size,_=PositionCalculator.calculate_order_size(spec,quote['last'],lever,base,runtime_state['ratio_map'][symbol],bool(old))
            if size<=0: raise ValueError('No affordable order quantity')
            result=await asyncio.to_thread(smart_router.execute_smart_entry,spec,side,size,
                execution_mode=runtime_state['execution_mode'],max_slippage_bps=runtime_state['max_slippage_bps'])
            if not result.get('success'):
                runtime_state['reconciliation_required']=True
                return result
            actual_size=result['sz']; actual_price=result['fill_px']
            position=risk_ledger.register_entry(symbol,side,actual_size,actual_price,
                actual_size*spec.ct_val*actual_price/lever,lever,runtime_state['protection'][symbol]['stop_loss_pct'])
            risk_ledger.configure(symbol,**runtime_state['protection'][symbol])
            if not position.stop_loss_enabled: return result
            stop_distance=position.initial_margin*position.stop_loss_pct/(position.size*spec.ct_val)
            stop=position.entry_price-stop_distance if side=='buy' else position.entry_price+stop_distance
            tick=Decimal(str(spec.tick_sz))
            stop=(Decimal(str(stop))/tick).to_integral_value(rounding=ROUND_CEILING if side=='buy' else ROUND_FLOOR)*tick
            cloud=await asyncio.to_thread(engine_pool.place_cloud_algo_stop,symbol,
                'sell' if side=='buy' else 'buy',format(stop,'f'),str(position.size))
            cloud_rows=cloud.get('data') or []
            if cloud.get('code')!='0' or not cloud_rows or any(str(row.get('sCode','0'))!='0' or not row.get('algoId') for row in cloud_rows):
                runtime_state['reconciliation_required']=True
                await flatten_symbol(symbol)
                raise RuntimeError('Cloud protection rejected; emergency close requested')
            return result
    else: raise ValueError('Unknown command')
    return {'type':'ACK','action':action}

def platform_snapshot(platform,include_catalog=True):
    state=platform_registry.get(platform).view(include_catalog)
    state['build_id']=BUILD_ID
    if PhysicalLimitEnginePool.is_live_authorized():
        if platform in ('OKX', 'BINANCE'):
            state['mode'] = 'LIVE'
            state['capabilities'] = dict(open_allowed=True, close_allowed=True, reason='')
    state['platforms']=[{'id':key,'name':name,'status':platform_registry.get(key).view(False)['connection_status']} for key,name in PLATFORMS.items()]
    combined_logs = list(state.get('logs') or []) + list(runtime_state.get('logs') or [])
    if not combined_logs:
        combined_logs = [f"{time.strftime('%H:%M:%S')} 控制台就绪 · 监听 {platform} 实时行情"]
    state['logs'] = combined_logs
    return state

async def connect_platform(platform):
    await asyncio.to_thread(platform_registry.refresh,platform)
    await asyncio.to_thread(platform_registry.refresh_quote,platform)
    await broadcast_state()

def platform_refresh_due(platform):
    context=platform_registry.get(platform)
    now=time.monotonic()
    return (platform in connection_platforms.values() and context.connector is not None
        and not context.loading and now-context.metadata_at>=20
        and now-context.last_refresh_attempt_at>=20)

async def handle_binance_command(cmd):
    if binance_adapter is None:
        raise RuntimeError('Binance API 凭据未加载，无法执行交易')
    binance_adapter.require_trading_authorization()
    action = cmd.get('action')
    symbol = cmd.get('symbol') or platform_registry.get('BINANCE').focused_symbol
    if symbol and '-' in symbol:
        symbol = symbol.replace('-USDT-SWAP', 'USDT').replace('-SWAP', '').replace('-', '')
    if not symbol:
        raise ValueError('未选择有效的 Binance 标的')
    
    if action in ('BUY_LONG', 'SELL_SHORT'):
        side = 'buy' if action == 'BUY_LONG' else 'sell'
        pos_side = 'long' if side == 'buy' else 'short'
        prefs = platform_registry.get('BINANCE').prefs(symbol)
        lever = prefs['leverage']
        ratio = prefs['ratio']
        
        market_spec = platform_registry.get('BINANCE').markets.get(symbol)
        lot_size = float(market_spec.lot_size) if market_spec else 0.001
        min_size = float(market_spec.min_size) if market_spec else 0.001
        min_notional = float(market_spec.min_notional) if (market_spec and market_spec.min_notional) else 5.0
        
        await asyncio.to_thread(binance_adapter.set_leverage, symbol, lever)
        bal = await asyncio.to_thread(binance_adapter.get_account_balance)
        avail = bal.get('available_balance', 0)
        tick = await asyncio.to_thread(binance_adapter.get_ticker, symbol)
        px = tick['last']
        
        if cmd.get('sz'):
            sz_float = float(cmd.get('sz'))
        else:
            margin = avail * ratio
            notional = margin * lever
            if min_notional and notional < min_notional:
                if min_notional / lever <= avail:
                    notional = min_notional
                else:
                    raise ValueError(f"可用保证金 ({avail:.4f} USDT) 不足以满足交易所最小开仓面值 ({min_notional} USDT)，请调高杠杆或充值")
            sz_float = notional / px
        step = Decimal(str(lot_size))
        min_sz = Decimal(str(min_size))
        q = (Decimal(str(sz_float)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if q < min_sz:
            q = min_sz
        sz_str = format(q, 'f').rstrip('0').rstrip('.')
        
        binance_ctx = platform_registry.get('BINANCE')
        exec_mode = getattr(binance_ctx, 'execution_mode', None) or runtime_state.get('execution_mode', 'balanced')
        res = await asyncio.to_thread(binance_adapter.execute_smart_entry, symbol, side, pos_side, sz_str, exec_mode)
        add_log(f"[Binance 智能路由开仓] 模式={res.get('execution_mode')} 均价={res.get('fill_px')} 耗时={res.get('elapsed_ms')}ms 数量={res.get('sz')}")
        fill_px = float(res.get('fill_px') or 0)
        if fill_px <= 0:
            fill_px = float(res.get('avgPx') or 0)
        if fill_px <= 0:
            fill_px = float(px or 0)
        if fill_px <= 0:
            tick = await asyncio.to_thread(binance_adapter.get_ticker, symbol)
            fill_px = float(tick.get('last') or 0)

        fill_sz = float(res.get('sz') or q)
        if fill_sz <= 0:
            fill_sz = float(q)
        init_margin = fill_sz * fill_px / lever
        prot = platform_registry.get('BINANCE').prefs(symbol).get('protection', DEFAULT_PROTECTION.copy())
        
        # 顺势加仓或首次开仓更新白名单（累积持仓并更新加权均价）
        if symbol in api_opened_positions:
            prev = api_opened_positions[symbol]
            prev_sz = float(prev.get('sz', 0))
            prev_px = float(prev.get('fill_px', fill_px))
            tot_sz = prev_sz + fill_sz
            tot_px = (prev_sz * prev_px + fill_sz * fill_px) / tot_sz if tot_sz > 0 else fill_px
            api_opened_positions[symbol] = {
                'symbol': symbol,
                'side': pos_side,
                'sz': tot_sz,
                'fill_px': tot_px,
                'opened_at': prev.get('opened_at', time.time())
            }
        else:
            api_opened_positions[symbol] = {
                'symbol': symbol,
                'side': pos_side,
                'sz': fill_sz,
                'fill_px': fill_px,
                'opened_at': time.time()
            }
        save_api_opened_positions()
        
        risk_ledger.register_entry(symbol, side, fill_sz, fill_px, init_margin, lever, prot.get('stop_loss_pct', 0.05))
        risk_ledger.configure(symbol, **prot)
        
        add_log(f"[Binance API开仓] {action} {symbol} 数量:{sz_str} 杠杆:{lever}x 状态:{res.get('state')}")
        return res
        
    elif action == 'PARTIAL_CLOSE':
        if symbol not in api_opened_positions:
            raise PermissionError(f"[安全拦截] {symbol} 是外部手动建仓，API程序禁止干预或平仓！如需平仓请在币安官方App或网页操作。")
        positions = await asyncio.to_thread(binance_adapter.get_positions, symbol)
        if not positions:
            api_opened_positions.pop(symbol, None)
            save_api_opened_positions()
            return {'success': True, 'msg': '无持仓可平'}
        market_spec = platform_registry.get('BINANCE').markets.get(symbol)
        lot_size = float(market_spec.lot_size) if market_spec else 0.001
        min_size = float(market_spec.min_size) if market_spec else 0.001
        step = Decimal(str(lot_size))
        min_sz = Decimal(str(min_size))
        
        for p in positions:
            amt = float(p['pos'])
            half = amt / 2
            q = (Decimal(str(half)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if q < min_sz:
                q = min_sz
            sz_str = format(q, 'f').rstrip('0').rstrip('.')
            close_side = 'sell' if p['posSide'] == 'long' else 'buy'
            await asyncio.to_thread(binance_adapter.place_order, symbol, close_side, p['posSide'], 'market', sz_str, reduce_only=True)
            if symbol in risk_ledger.ledgers:
                pos_entry = risk_ledger.ledgers[symbol]
                pos_entry.size -= float(q)
                pos_entry.initial_margin *= 0.5
                if pos_entry.size <= 0:
                    risk_ledger.close_ledger(symbol)
                    api_opened_positions.pop(symbol, None)
                    save_api_opened_positions()
        add_log(f"[Binance API平半仓] 完成: {symbol}")
        return {'success': True}
        
    elif action == 'FLATTEN_CURRENT':
        if symbol not in api_opened_positions:
            raise PermissionError(f"[安全拦截] {symbol} 是外部手动建仓，API程序禁止干预或平仓！如需平仓请在币安官方App或网页操作。")
        res = await asyncio.to_thread(binance_adapter.close_position_instant, symbol)
        await asyncio.to_thread(binance_adapter.cancel_all_orders, symbol)
        risk_ledger.close_ledger(symbol)
        api_opened_positions.pop(symbol, None)
        save_api_opened_positions()
        add_log(f"[Binance API平仓] 已发送: {symbol}")
        return res
        
    elif action == 'FLATTEN_ALL':
        symbols_to_close = list(api_opened_positions.keys())
        if not symbols_to_close:
            add_log("[Binance] 当前无API开出的持仓，外部仓位严密受保护，未执行任何平仓")
            return {'success': True, 'msg': '无API开出持仓，外部持仓受保护'}
        for sym in symbols_to_close:
            await asyncio.to_thread(binance_adapter.close_position_instant, sym)
            await asyncio.to_thread(binance_adapter.cancel_all_orders, sym)
            risk_ledger.close_ledger(sym)
            api_opened_positions.pop(sym, None)
        save_api_opened_positions()
        add_log(f"[Binance] API仓位全平已发送: {symbols_to_close}，外部持仓完全未动")
        return {'success': True}
        
    elif action == 'CANCEL_ALL':
        if symbol:
            await asyncio.to_thread(binance_adapter.cancel_all_orders, symbol)
        else:
            for sym in list(api_opened_positions.keys()):
                await asyncio.to_thread(binance_adapter.cancel_all_orders, sym)
        return {'success': True}
        
    return {'success': True}
async def route_platform_command(command):
    platform=command.get('platform','OKX');context=platform_registry.get(platform);action=command.get('action')
    if action=='PING':return {'type':'PONG','sent_at':command.get('sent_at')}
    if action=='SWITCH_PLATFORM':
        if not context.loading and (not context.last_refresh_attempt_at
            or time.monotonic()-context.last_refresh_attempt_at>=20):
            context.loading=True
            asyncio.create_task(connect_platform(platform))
        return {'type':'ACK','action':action,'platform':platform}
    if action=='CONNECT_PLATFORM':
        if not context.loading:
            context.loading=True
            asyncio.create_task(connect_platform(platform))
        return {'type':'ACK','action':action,'platform':platform,'message':'正在读取平台原生市场和账户状态'}
    if action in ('BUY_LONG','SELL_SHORT','FLATTEN_CURRENT','FLATTEN_ALL','PARTIAL_CLOSE','CANCEL_ALL'):
        if not PhysicalLimitEnginePool.is_live_authorized():
            raise PermissionError('LIVE=NO-GO: exchange mutations disabled in this build')
        if platform == 'OKX':
            return await handle_command(command)
        elif platform == 'BINANCE':
            return await handle_binance_command(command)
        raise PermissionError(f'{platform} 实盘交易下单接口尚未接入（当前为只读行情与账户监控模式），实盘下单请切换至 OKX 平台！')
    platform_registry.configure(platform,command)
    if action == 'SWITCH_SYMBOL':
        try:
            await asyncio.to_thread(platform_registry.refresh_quote, platform)
            await broadcast_state()
        except Exception: pass
    if action == 'SET_EXECUTION':
        mode = command.get('mode')
        if mode in ('balanced', 'speed', 'maker_only'):
            runtime_state['execution_mode'] = mode
    if platform == 'OKX' and action in ('SET_RATIO','SET_LEVERAGE','SET_STOP_LOSS','SET_RISK','SET_EXECUTION','SWITCH_SYMBOL','SWITCH_SLOT'):
        try: await handle_command(command)
        except Exception: pass
    return {'type':'ACK','action':action,'platform':platform}

async def route_connection_command(ws,command):
    """Bind every UI request to its connection's venue before configuration."""
    platform=command.get('platform',connection_platforms.get(ws,'OKX'))
    platform_registry.get(platform)
    if command.get('action')=='SWITCH_PLATFORM':
        connection_platforms[ws]=platform
        connection_catalog_versions.pop(ws,None)
    elif platform!=connection_platforms.get(ws,'OKX'):
        raise ValueError('指令平台与当前连接平台不一致，已拒绝')
    return await route_platform_command({**command,'platform':platform})

async def broadcast_state():
    for ws in active_connections[:]:
        try:
            platform=connection_platforms.get(ws,'OKX')
            context=platform_registry.get(platform)
            revision=(platform,context.catalog_revision)
            include_catalog=connection_catalog_versions.get(ws)!=revision
            data=platform_snapshot(platform,include_catalog)
            payload=json.dumps(dict(type='STATE_UPDATE',data=data,positions={}))
            await asyncio.wait_for(ws.send_text(payload),timeout=.2)
            connection_catalog_versions[ws]=revision
        except Exception:
            if ws in active_connections:active_connections.remove(ws)
            connection_platforms.pop(ws,None);connection_catalog_versions.pop(ws,None)

async def platform_quote_loop(platform):
    while True:
        context=platform_registry.get(platform)
        if platform_refresh_due(platform):
            context.loading=True
            await connect_platform(platform)
        elif context.connector is not None and not context.loading and platform in connection_platforms.values():
            await asyncio.to_thread(platform_registry.refresh_quote,platform)
        await asyncio.sleep(0.4)

async def risk_and_market_loop():
    while True:
        runtime_state['tickers']={s:row for s in feed.symbols if (row:=feed.get(s))}
        runtime_state['feed_connected']=feed.connected
        # In NO-GO mode show risk decisions without mutating exchange state.
        for symbol in list(risk_ledger.ledgers):
            # 严格保护：若非本API程序开出的币安持仓，绝不纳入自动风控熔断，立即清理状态机
            if symbol.endswith('USDT') and '-' not in symbol and symbol not in api_opened_positions:
                risk_ledger.close_ledger(symbol)
                continue
            quote = feed.get(symbol) or (platform_registry.get('BINANCE').tickers.get(symbol) if 'BINANCE' in platform_registry.contexts else None)
            if not quote and binance_adapter and symbol.endswith('USDT') and '-' not in symbol:
                try:
                    quote = await asyncio.to_thread(binance_adapter.get_ticker, symbol)
                    if quote and 'BINANCE' in platform_registry.contexts:
                        platform_registry.get('BINANCE').tickers[symbol] = quote
                except Exception:
                    pass
            if quote:
                p=risk_ledger.ledgers[symbol]
                mark_px = quote['bid'] if p.side=='buy' else quote['ask']
                decision, audit_info = risk_ledger.evaluate_risk(symbol, mark_px)
                if decision.startswith('TRIGGER'):
                    runtime_state['risk_alert']=symbol+': '+decision
                    add_log(f"[{decision}] 触发自动熔断: {symbol} ROE: {audit_info.get('roe_pct')}%")
                    try:
                        if PhysicalLimitEnginePool.is_live_authorized():
                            async with command_lock: await flatten_symbol(symbol)
                    except PermissionError: pass
        await asyncio.sleep(.1)

async def broadcast_loop():
    while True:
        await broadcast_state()
        await asyncio.sleep(.1)

async def balance_loop():
    while True:
        try:
            try: await refresh_balance_once(time.monotonic())
            except Exception:
                runtime_state['account_valid']=False
            try: await refresh_positions_once(time.monotonic())
            except Exception:
                runtime_state['positions_verified']=False
            if binance_adapter:
                try:
                    bn_positions = await asyncio.to_thread(binance_adapter.get_positions)
                    active_bn_syms = {p['instId'] for p in bn_positions if float(p.get('pos', 0)) != 0}
                    # 仅同步本API程序开仓白名单中的状态；绝不自动收录外部持仓进风控
                    for s in list(api_opened_positions.keys()):
                        if s not in active_bn_syms:
                            api_opened_positions.pop(s, None)
                            risk_ledger.close_ledger(s)
                            save_api_opened_positions()
                        elif s not in risk_ledger.ledgers:
                            restore_active_positions_into_risk_ledger()
                except Exception:
                    pass
        except Exception as exc: add_log('账户刷新失败: '+type(exc).__name__)
        await asyncio.sleep(.2)

@asynccontextmanager
async def lifespan(app):
    global engine_pool,smart_router
    tasks=[]
    try:
        creds=CredentialsLoader.load()
        engine_pool=PhysicalLimitEnginePool(creds['apiKey'],creds['secretKey'],creds['passphrase'])
        engine_pool.market_cache=feed
        platform_registry.okx_engine=engine_pool
        smart_router=SmartOrderRouter(engine_pool)
        try:
            runtime_state['pos_mode']=await asyncio.to_thread(engine_pool.detect_position_mode)
            runtime_state['authenticated']=True
        except Exception as exc: add_log('只读鉴权未通过: '+type(exc).__name__)
    except Exception as exc: add_log('凭据加载未通过: '+type(exc).__name__)
    prevent_windows_sleep()
    restore_active_positions_into_risk_ledger()
    tasks=[asyncio.create_task(f()) for f in (feed.run,risk_and_market_loop,balance_loop,broadcast_loop)]
    tasks += [asyncio.create_task(platform_quote_loop(platform)) for platform in PLATFORMS]
    tasks.append(asyncio.create_task(connect_platform('OKX')))
    add_log('LIVE=NO-GO：实盘交易禁用，行情/界面可验收')
    try: yield
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for platform,context in platform_registry.contexts.items():
            close=getattr(context.connector,'close',None)
            if close: close()
        if engine_pool: engine_pool.close()

app=FastAPI(title='OKX-Titan Console',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'web_ui/static')),name='static')
@app.get('/',response_class=HTMLResponse)
async def index():
    return HTMLResponse((ROOT/'web_ui/templates/index.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store'})
@app.get('/health')
async def health(): return {'mode':'LIVE=NO-GO','feed_connected':feed.connected,**public_runtime_status()}
@app.websocket('/ws')
async def websocket_endpoint(ws:WebSocket):
    origin=ws.headers.get('origin')
    if origin not in ('http://127.0.0.1:57890','http://localhost:57890'):
        await ws.close(code=1008); return
    await ws.accept(); active_connections.append(ws)
    connection_platforms[ws]='OKX'
    await broadcast_state()
    try:
        while True:
            cmd={}
            try:
                cmd=json.loads(await ws.receive_text())
                if not isinstance(cmd,dict): raise ValueError('Invalid command')
                async with command_lock:
                    result=await route_connection_command(ws,cmd)
                if result:
                    try: await ws.send_json(result)
                    except Exception: break
                if cmd.get('action')!='PING': await broadcast_state()
            except WebSocketDisconnect:
                break
            except (ValueError,KeyError,TypeError,PermissionError,RuntimeError) as exc:
                disabled=isinstance(exc,PermissionError)
                message='只读验收版本：实盘建仓、加仓、平仓、撤单尚未启用' if disabled else str(exc)
                add_log('操作未完成: '+message)
                try:
                    await ws.send_json({'type':'ERROR','code':'TRADING_DISABLED' if disabled else 'COMMAND_REJECTED','message':message,'platform':cmd.get('platform',connection_platforms.get(ws,'OKX'))})
                except Exception:
                    break
            except Exception as exc:
                import traceback; traceback.print_exc()
                add_log(f'指令异常，未确认完成: {type(exc).__name__}: {exc}')
                try: await ws.send_json({'type':'ERROR','code':'UNCONFIRMED','platform':connection_platforms.get(ws,'OKX'),'message':'指令结果未确认，请检查账户状态后再操作'})
                except Exception: break
    except WebSocketDisconnect: pass
    finally:
        if ws in active_connections: active_connections.remove(ws)
        connection_platforms.pop(ws,None);connection_catalog_versions.pop(ws,None)
