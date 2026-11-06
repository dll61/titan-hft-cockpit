"""Order-scoped execution. Unknown state must never cause a duplicate order."""
import time
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

class SmartOrderRouter:
    def __init__(self, engine_pool):
        self.engine = engine_pool

    def execute_smart_entry(self, spec, side, sz, timeout_ms=150, max_drift_ticks=2, execution_mode="balanced", max_slippage_bps=10):
        try:
            return self._execute_entry(spec,side,sz,timeout_ms,max_drift_ticks,execution_mode,max_slippage_bps)
        except Exception as exc:
            # A transport exception after submit may hide an accepted order.
            # Return an explicit reconciliation latch, never exception text/headers.
            return dict(success=False,reconciliation_required=True,error_type=type(exc).__name__,
                msg='Order result unconfirmed; reconcile before retry')

    def _execute_entry(self, spec, side, sz, timeout_ms, max_drift_ticks, execution_mode, max_slippage_bps):
        if side not in ('buy','sell') or not math.isfinite(sz) or sz<=0:
            return dict(success=False,msg='Invalid order side or size')
        ticker = self.engine.get_ticker(spec.inst_id)
        if (not ticker or not all(math.isfinite(float(ticker.get(k,0))) and float(ticker.get(k,0))>0 for k in ('bid','ask'))
            or float(ticker['bid'])>float(ticker['ask'])):
            return dict(success=False, msg='Fresh BBO unavailable; entry blocked')
        if execution_mode not in ('balanced','speed','maker_only') or not 0<max_slippage_bps<=100:
            return dict(success=False,msg='Invalid execution policy')
        if execution_mode=='speed':
            return self._execute_ioc(spec,side,sz,ticker,max_slippage_bps)
        pos_side = self.engine.position_side(side)
        tick = Decimal(str(spec.tick_sz))
        px = Decimal(str(ticker['bid'] if side == 'buy' else ticker['ask']))
        px = (px/tick).to_integral_value(rounding=ROUND_FLOOR if side=='buy' else ROUND_CEILING)*tick
        started = time.monotonic()
        result = self.engine.place_order(inst_id=spec.inst_id, side=side, pos_side=pos_side,
            ord_type='post_only', sz=str(sz), px=format(px,'f'))
        if not result.get('success') or not result.get('ordId'):
            return dict(success=False, msg='Maker rejected or status unknown; reconcile before retry', raw=result)
        order_id = result['ordId']
        order = None
        while (time.monotonic()-started)*1000 < timeout_ms:
            order = self.engine.get_order(spec.inst_id, order_id)
            if order and order['state'] in ('filled','canceled','mmp_canceled'):
                break
            time.sleep(0.05)
        if not order or order['state'] not in ('filled','canceled','mmp_canceled'):
            self.engine.cancel_order(spec.inst_id, order_id)
            # Cancel ACK is not finality: re-read cumulative fills after cancellation.
            order = self.engine.get_order(spec.inst_id, order_id)
        if not order or order['state'] not in ('filled','canceled','mmp_canceled'):
            return dict(success=False, msg='Maker cancellation unresolved; no chase', ordId=order_id)
        filled = Decimal(str(order.get('accFillSz','0')))
        remaining = Decimal(str(sz)) - filled
        if not filled.is_finite() or not remaining.is_finite() or filled < 0 or remaining < 0:
            return dict(success=False, msg='Invalid cumulative fill quantity')
        maker_price=Decimal(str(order.get('avgPx') or 0))
        if not maker_price.is_finite() or (filled>0 and maker_price<=0):
            return dict(success=False,msg='Fill price missing; reconciliation required')
        cost = filled * maker_price
        if remaining > 0 and execution_mode != 'maker_only':
            cap = self._ioc_price(spec,side,ticker,max_slippage_bps)
            chase = self.engine.place_order(inst_id=spec.inst_id, side=side, pos_side=pos_side,
                ord_type='ioc', sz=format(remaining,'f'),px=cap)
            if not chase.get('success') or not chase.get('ordId'):
                return dict(success=False, msg='Chase unresolved; reconcile exchange fills', filled_sz=float(filled))
            tail = self.engine.get_order(spec.inst_id, chase['ordId'])
            if not tail or tail['state'] not in ('filled','canceled'):
                return dict(success=False, msg='Chase awaiting confirmation; do not resubmit', ordId=chase['ordId'])
            qty = Decimal(str(tail.get('accFillSz','0')))
            if not qty.is_finite() or qty < 0 or qty > remaining:
                return dict(success=False, msg='Invalid chase fill quantity')
            tail_price=Decimal(str(tail.get('avgPx') or 0))
            if qty>0 and (not tail_price.is_finite() or tail_price<=0):
                return dict(success=False,msg='Chase fill price missing; reconcile',filled_sz=float(filled))
            filled += qty
            cost += qty * tail_price
        return dict(success=filled>0, sz=float(filled), fill_px=float(cost/filled) if filled else 0,
                    mode='CONFIRMED_FILLS', elapsed_ms=round((time.monotonic()-started)*1000,2))

    @staticmethod
    def _ioc_price(spec,side,ticker,bps):
        tick=Decimal(str(spec.tick_sz))
        ref=Decimal(str(ticker['ask'] if side=='buy' else ticker['bid']))
        cap=ref*(Decimal(1)+(Decimal(str(bps))/10000)*(1 if side=='buy' else -1))
        return format((cap/tick).to_integral_value(rounding=ROUND_FLOOR if side=='buy' else ROUND_CEILING)*tick,'f')

    def _execute_ioc(self,spec,side,sz,ticker,bps):
        result=self.engine.place_order(inst_id=spec.inst_id,side=side,pos_side=self.engine.position_side(side),
            ord_type='ioc',sz=str(sz),px=self._ioc_price(spec,side,ticker,bps))
        if not result.get('success') or not result.get('ordId'): return result
        order=self.engine.get_order(spec.inst_id,result['ordId'])
        if not order or order['state'] not in ('filled','canceled'):
            return dict(success=False,msg='IOC awaiting confirmation; reconcile before retry')
        qty=float(order.get('accFillSz') or 0)
        if not math.isfinite(qty) or qty<0 or qty>sz: return dict(success=False,msg='Invalid IOC fill quantity')
        fill_price=float(order.get('avgPx') or 0)
        if qty>0 and (not math.isfinite(fill_price) or fill_price<=0):
            return dict(success=False,msg='IOC fill price missing; reconciliation required')
        return dict(success=qty>0,sz=qty,fill_px=fill_price,mode='SPEED_BOUNDED_IOC')

    def execute_emergency_flatten(self, inst_id):
        result = self.engine.close_position_instant(inst_id)
        if result.get('success'):
            cleared = not self.engine.get_positions(inst_id)
            if not cleared:
                for _ in range(4):
                    time.sleep(0.05)
                    if not self.engine.get_positions(inst_id):
                        cleared = True
                        break
            result['success'] = cleared
            if not result['success']:
                result['msg'] = 'Close accepted; position not yet zero'
        return result

    def execute_partial_close(self, spec, side, sz):
        return self.engine.place_order(inst_id=spec.inst_id, side='sell' if side=='buy' else 'buy',
            pos_side=self.engine.position_side(side), ord_type='market', sz=str(sz), reduce_only=True)
