from config.instruments_catalog import get_instrument_spec
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
import math

logger = logging.getLogger("RISK_LEDGER")

@dataclass
class PositionLedger:
    inst_id: str
    side: str                        # "buy" or "sell"
    size: float                      # 当前持仓张数
    entry_price: float               # 当前加权综合开仓均价
    initial_margin: float            # 当前累计占用保证金本金 (USDT)
    leverage: int                    # 杠杆倍数
    stop_loss_pct: float = 0.05      # 斩杀线阈值 (默认 5%, 可按键/滑块调为 10%, 15%)
    breakeven_active: bool = False   # 是否已开启保本锁 (净浮盈 >= 1% 开启)
    high_water_mark_usd: float = 0.0 # 整体持仓最高净浮盈峰值 (USDT)
    trailing_active: bool = False    # 是否已激活 30% 移动止盈 (净浮盈 >= 2% 开启)
    status: str = "ACTIVE"           # ACTIVE, CLOSED, MELTED_TP, MELTED_SL, MELTED_BREAKEVEN
    ct_val: float = 1.0
    stop_loss_enabled: bool = True
    breakeven_enabled: bool = True
    trailing_enabled: bool = True
    fixed_take_profit_pct: Optional[float] = None
    trailing_retrace_ratio: float = 0.30
    breakeven_activation: float = 0.01
    trailing_activation: float = 0.02
    stop_loss_amount: Optional[float] = None
    fixed_take_profit_amount: Optional[float] = None
    trailing_activation_amount: Optional[float] = None
    trailing_retrace_amount: Optional[float] = None
    opened_at: float = field(default_factory=time.time)

class IsolatedRiskLedger:
    """
    独立逐仓风控状态机中枢 (严格对齐用户最新三阶参数)：
    1. 噪音区间 (ROE < 1%)：不触发移动止盈，只防 -5% 硬斩杀线；
    2. 保本区间 (ROE >= 1%)：自动锁定保本防线，若后续行情回落跌破开仓成本线(含双边手续费)，瞬间平仓保本；
    3. 暴利移动止盈区间 (ROE >= 2%)：正式武装 30% 浮盈回撤熔断器，记录整体仓位最高浮盈峰值 HWM，回撤达 30% 瞬间全平；
    4. 顺势加仓（方案 A：加权均价重置法）：加仓后自动合并底仓，重置均价与总保证金本金，整体统一接力执行上述三阶风控！
    """
    def __init__(self):
        self.ledgers: Dict[str, PositionLedger] = {}

    def register_entry(
        self,
        inst_id: str,
        side: str,
        size: float,
        price: float,
        margin: float,
        leverage: int,
        stop_loss_pct: Optional[float] = None
    ) -> PositionLedger:
        """
        登记新开仓或在已有持仓上执行【方案 A：加权均价重置】
        """
        old_position = self.ledgers.get(inst_id)
        if stop_loss_pct is None:
            stop_loss_pct = old_position.stop_loss_pct if old_position else .05
        if not all(math.isfinite(x) for x in (size, price, margin, leverage, stop_loss_pct)) or stop_loss_pct<0:
            raise ValueError('Invalid numerical entry')
        if side not in ('buy', 'sell') or min(size, price, margin, leverage) <= 0:
            raise ValueError('Invalid entry')
        spec = get_instrument_spec(inst_id)
        if spec is None:
            raise ValueError('Unknown instrument')
        if inst_id in self.ledgers and self.ledgers[inst_id].status == "ACTIVE":
            # 顺势加仓：执行方案 A 均价重置合并
            old = self.ledgers[inst_id]
            if old.side != side:
                raise ValueError('Opposite side cannot be merged into the same ledger')
            total_sz = old.size + size
            new_weighted_px = (old.size * old.entry_price + size * price) / total_sz if total_sz > 0 else price
            new_total_margin = old.initial_margin + margin

            old.size = total_sz
            old.entry_price = new_weighted_px
            old.initial_margin = new_total_margin
            old.leverage = leverage
            old.stop_loss_pct = stop_loss_pct
            # 加仓后整体重置：最高浮盈重新从新加权均价起算，重新经历噪音->保本->暴利三阶
            old.high_water_mark_usd = 0.0
            old.trailing_active = False
            old.breakeven_active = False
            logger.info(f"[{inst_id}] ADD-ON EXECUTED: Aggregate New AvgPx={old.entry_price}, TotalMargin={old.initial_margin}, HWM Reset")
            return old
        else:
            # 首次建仓
            ledger = PositionLedger(
                inst_id=inst_id,
                side=side,
                size=size,
                entry_price=price,
                initial_margin=margin,
                leverage=leverage,
                ct_val=spec.ct_val,
                stop_loss_pct=stop_loss_pct
            )
            self.ledgers[inst_id] = ledger
            logger.info(f"[{inst_id}] NEW POSITION REGISTERED: Side={side}, Size={size}, Price={price}, Margin={margin}")
            return ledger

    def evaluate_risk(self, inst_id: str, current_price: float) -> Tuple[str, Dict[str, Any]]:
        """
        高频风控状态机判定 (100ms 轮询调用)
        """
        if inst_id not in self.ledgers or self.ledgers[inst_id].status != "ACTIVE":
            return "NO_POSITION", {}

        p = self.ledgers[inst_id]
        if not math.isfinite(current_price) or current_price <= 0 or p.size <= 0:
            return "HOLD", {}

        # 1. 计算整体未实现毛盈亏（与币安/OKX App 保持一致）
        if p.side == "buy":
            unrealized_pnl = (current_price - p.entry_price) * p.size * p.ct_val
        else:
            unrealized_pnl = (p.entry_price - current_price) * p.size * p.ct_val

        # 2. 相对整体仓位保证金的未实现收益率 ROE (100% 对齐交易所官方 App 显示的 ROE)
        roe_pct = (unrealized_pnl / p.initial_margin) if p.initial_margin > 0 else 0.0

        # 3. 三阶状态流转：
        # 阶梯 2：净收益率 ROE >= 1% 时，武装成本保本锁
        if p.breakeven_enabled and roe_pct >= p.breakeven_activation and not p.breakeven_active:
            p.breakeven_active = True
            logger.info(f"[{inst_id}] BREAKEVEN LOCKED: ROE={roe_pct*100:.2f}%, threshold={p.breakeven_activation*100:.2f}%")

        # 阶梯 3：净收益率 ROE >= 2% 时，武装动态回撤移动止盈
        activation_met=(unrealized_pnl >= p.trailing_activation_amount if p.trailing_activation_amount is not None else roe_pct >= p.trailing_activation)
        if p.trailing_enabled and activation_met and not p.trailing_active:
            p.trailing_active = True
            logger.info(f"[{inst_id}] TRAILING TP ARMED: ROE={roe_pct*100:.2f}%, amount_threshold={p.trailing_activation_amount}, roe_threshold={p.trailing_activation}")

        # 动态刷新最高浮盈峰值 (High Water Mark, HWM)
        if p.trailing_active and unrealized_pnl > p.high_water_mark_usd:
            p.high_water_mark_usd = unrealized_pnl

        audit_info = {
            "current_price": current_price,
            "entry_price": p.entry_price,
            "size": p.size,
            "margin": p.initial_margin,
            "net_pnl": round(unrealized_pnl, 4),
            "roe_pct": round(roe_pct * 100, 2),
            "hwm_usd": round(p.high_water_mark_usd, 4),
            "stop_loss_pct": round(p.stop_loss_pct * 100, 1),
            "breakeven_active": p.breakeven_active,
            "trailing_active": p.trailing_active,
            "drawdown_pct": 0.0
        }

        # 判定 A：-5% (或用户自定义) 硬斩杀止损线 (第一安全底线)
        stop_met=(unrealized_pnl <= -p.stop_loss_amount if p.stop_loss_amount is not None else roe_pct <= -p.stop_loss_pct)
        if p.stop_loss_enabled and stop_met:
            threshold=f"amount={p.stop_loss_amount}" if p.stop_loss_amount is not None else f"ROE=-{p.stop_loss_pct*100:.2f}%"
            logger.warning(f"[{inst_id}] HARD STOP TRIGGERED: PNL={unrealized_pnl:.4f}, ROE={roe_pct*100:.2f}%, {threshold}")
            return "TRIGGER_STOP_LOSS", audit_info

        profit_met=(unrealized_pnl >= p.fixed_take_profit_amount if p.fixed_take_profit_amount is not None else
                    p.fixed_take_profit_pct is not None and roe_pct >= p.fixed_take_profit_pct)
        if profit_met:
            return 'TRIGGER_FIXED_TP', audit_info

        # 判定 B：成本线保本平仓 (曾盈利 >= 1%，现回踩抹平利润触及开仓均价成本)
        if p.breakeven_enabled and p.breakeven_active and unrealized_pnl <= 0.0:
            logger.warning(f"🛡️ [{inst_id}] BREAKEVEN TRIGGERED! Profit returned to cost basis.")
            return "TRIGGER_BREAKEVEN", audit_info

        # 判定 C：动态最高利润回撤熔断 (峰值浮盈回撤达设定比例 30%/10% 等，立刻把利润锁死)
        if p.trailing_enabled and p.trailing_active and p.high_water_mark_usd > 0.0:
            drawdown = (p.high_water_mark_usd - unrealized_pnl) / p.high_water_mark_usd
            audit_info["drawdown_pct"] = round(drawdown * 100, 2)
            retrace_met=(p.high_water_mark_usd-unrealized_pnl >= p.trailing_retrace_amount if p.trailing_retrace_amount is not None else drawdown >= p.trailing_retrace_ratio)
            if retrace_met:
                logger.warning(f"💰 [{inst_id}] TRAILING TAKE-PROFIT TRIGGERED! Retraced {drawdown*100:.1f}% from peak ${p.high_water_mark_usd:.4f}")
                return "TRIGGER_TRAILING_TP", audit_info

        return "HOLD", audit_info

    def close_ledger(self, inst_id: str):
        """仓位已平，注销账本"""
        if inst_id in self.ledgers:
            self.ledgers[inst_id].status = "CLOSED"
            del self.ledgers[inst_id]
            logger.info(f"[{inst_id}] Position closed and ledger cleared.")

    @staticmethod
    def validate_settings(settings):
        flags={'stop_loss_enabled','breakeven_enabled','trailing_enabled'}
        numeric={'stop_loss_pct','fixed_take_profit_pct','trailing_retrace_ratio','breakeven_activation','trailing_activation',
                 'stop_loss_amount','fixed_take_profit_amount','trailing_activation_amount','trailing_retrace_amount'}
        optional={'fixed_take_profit_pct','stop_loss_amount','fixed_take_profit_amount','trailing_activation_amount','trailing_retrace_amount'}
        signed={'fixed_take_profit_pct','fixed_take_profit_amount'}
        for key,value in settings.items():
            if key in flags:
                if type(value) is not bool: raise ValueError('Protection switch must be boolean')
            elif key in numeric:
                if key in optional and value is None: continue
                if type(value) not in (int,float) or not math.isfinite(value) or (key not in signed and value<0):
                    raise ValueError('Invalid protection threshold: '+key)
            else: raise ValueError('Unknown protection setting')

    def configure(self, inst_id, **settings):
        self.validate_settings(settings)
        p=self.ledgers[inst_id]
        for key,value in settings.items(): setattr(p,key,value)
        if settings.get('breakeven_enabled') is False: p.breakeven_active=False
        if settings.get('trailing_enabled') is False: p.trailing_active=False
