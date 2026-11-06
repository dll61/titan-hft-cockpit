from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math
from typing import Dict, List, Tuple
from config.instruments_catalog import InstrumentSpec, get_instrument_spec

class PositionCalculator:
    """
    仓位与杠杆换算中枢
    - 动态四档杠杆离散化
    - 六档仓位与剩余保证金加仓换算
    - 严格遵循 OKX 物理规格对齐 (minSz, lotSz, tickSz)
    - 方案 A：加权均价重置算法
    """

    @staticmethod
    def get_leverage_tiers(max_lever: int) -> Dict[str, int]:
        """计算四档杠杆 (低、中、高、顶格)"""
        return {
            "low": max(1, round(max_lever * 0.25)),
            "mid": max(1, round(max_lever * 0.50)),
            "high": max(1, round(max_lever * 0.75)),
            "max": max_lever
        }

    @staticmethod
    def sanitize_leverage(requested: int, max_lever: int) -> int:
        """限制在合法区间 [1, max_lever]"""
        return max(1, min(max_lever, int(requested)))

    @staticmethod
    def calculate_order_size(
        spec: InstrumentSpec,
        price: float,
        leverage: int,
        equity: float,
        ratio: float,
        is_addon: bool = False
    ) -> Tuple[float, float]:
        """
        计算下单张数 (sz) 与实际占用保证金 (margin)
        - is_addon 为 True 时，equity 应传入当前剩余可用资金 availBal
        - 自动进行 minSz 防御对齐
        """
        if not all(math.isfinite(x) for x in (price, leverage, equity, ratio, spec.ct_val, spec.lot_sz, spec.min_sz, spec.tick_sz)):
            raise ValueError('Non-finite sizing input')
        if min(price, leverage, equity, spec.ct_val, spec.lot_sz) <= 0 or not 0 < ratio <= 1:
            return 0.0, 0.0
        D = lambda x: Decimal(str(x))
        step = D(spec.lot_sz)
        raw = D(equity) * D(ratio) * D(leverage) / (D(price) * D(spec.ct_val))
        lots = (raw / step).to_integral_value(rounding=ROUND_FLOOR)
        minimum = (D(spec.min_sz) / step).to_integral_value(rounding=ROUND_CEILING)
        if lots < minimum:
            raise ValueError('Selected allocation is below exchange minimum; no automatic uplift')
        size = lots * step
        margin = size * D(price) * D(spec.ct_val) / D(leverage)
        if margin > D(equity)*D(ratio):
            raise ValueError('Order exceeds selected allocation')
        return float(size), float(margin)

    @staticmethod
    def compute_weighted_average(
        pos_old: float,
        px_old: float,
        pos_add: float,
        px_add: float
    ) -> Tuple[float, float]:
        """
        方案 A：均价重置算法
        合并旧持仓与新加仓，计算全新的加权持仓均价与总持仓量
        """
        total_pos = pos_old + pos_add
        if total_pos <= 0:
            return 0.0, 0.0

        weighted_price = (pos_old * px_old + pos_add * px_add) / total_pos
        return weighted_price, total_pos
