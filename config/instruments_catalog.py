from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class InstrumentSpec:
    inst_id: str
    name_cn: str
    symbol_display: str
    max_leverage: int
    ct_val: float        # 合约面值 (例如 1 股, 或 0.01 BTC)
    min_sz: float        # 单笔最小下单张数
    lot_sz: float        # 下单步进
    tick_sz: float       # 价格步进
    category: str        # US_STOCK, CRYPTO, KOREA, EUROPE, ETF
    status: str = "LIVE" # LIVE, SUSPENDED, PENDING_LISTING

# 12 大王牌核心标的元数据表 (严格核实 OKX 真实规格)
CORE_INSTRUMENTS: Dict[str, InstrumentSpec] = {
    "TSLA-USDT-SWAP": InstrumentSpec(
        inst_id="TSLA-USDT-SWAP",
        name_cn="特斯拉 (Tesla)",
        symbol_display="TSLA",
        max_leverage=25,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "SPCX-USDT-SWAP": InstrumentSpec(
        inst_id="SPCX-USDT-SWAP",
        name_cn="SpaceX (太空探索 · 概念代币化)",
        symbol_display="SPCX",
        max_leverage=75,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "SPCXUSDT": InstrumentSpec(
        inst_id="SPCXUSDT",
        name_cn="SpaceX (太空探索 · 美股代币化)",
        symbol_display="SPCX",
        max_leverage=75,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "GOOGL-USDT-SWAP": InstrumentSpec(
        inst_id="GOOGL-USDT-SWAP",
        name_cn="谷歌 (Alphabet)",
        symbol_display="GOOGL",
        max_leverage=50,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "BE-USDT-SWAP": InstrumentSpec(
        inst_id="BE-USDT-SWAP",
        name_cn="Bloom Energy (燃料电池)",
        symbol_display="BE",
        max_leverage=25,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "BTC-USDT-SWAP": InstrumentSpec(
        inst_id="BTC-USDT-SWAP",
        name_cn="比特币 (Bitcoin)",
        symbol_display="BTC",
        max_leverage=100,
        ct_val=0.01,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.1,
        category="CRYPTO"
    ),
    "SKHYNIX-USDT-SWAP": InstrumentSpec(
        inst_id="SKHYNIX-USDT-SWAP",
        name_cn="SK 海力士 (韩国半导体巨头)",
        symbol_display="SKHYNIX",
        max_leverage=50,
        ct_val=1.0,
        min_sz=0.001,
        lot_sz=0.001,
        tick_sz=0.1,
        category="KOREA"
    ),
    "SNDK-USDT-SWAP": InstrumentSpec(
        inst_id="SNDK-USDT-SWAP",
        name_cn="闪迪存储 (SanDisk)",
        symbol_display="SNDK",
        max_leverage=75,
        ct_val=1.0,
        min_sz=0.001,
        lot_sz=0.001,
        tick_sz=0.1,
        category="US_STOCK"
    ),
    "INTC-USDT-SWAP": InstrumentSpec(
        inst_id="INTC-USDT-SWAP",
        name_cn="英特尔 (Intel)",
        symbol_display="INTC",
        max_leverage=25,
        ct_val=1.0,
        min_sz=0.1,
        lot_sz=0.1,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "NOK-USDT-SWAP": InstrumentSpec(
        inst_id="NOK-USDT-SWAP",
        name_cn="诺基亚 (Nokia 欧股电信)",
        symbol_display="NOK",
        max_leverage=20,
        ct_val=1.0,
        min_sz=0.1,
        lot_sz=0.1,
        tick_sz=0.001,
        category="EUROPE"
    ),
    "ASML-USDT-SWAP": InstrumentSpec(
        inst_id="ASML-USDT-SWAP",
        name_cn="阿斯麦 (ASML 欧洲光刻机)",
        symbol_display="ASML",
        max_leverage=20,
        ct_val=1.0,
        min_sz=0.001,
        lot_sz=0.001,
        tick_sz=0.1,
        category="EUROPE"
    ),
    "SOXL-USDT-SWAP": InstrumentSpec(
        inst_id="SOXL-USDT-SWAP",
        name_cn="半导体 3x 多头 (👑 STM 最佳补位)",
        symbol_display="SOXL",
        max_leverage=20,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="ETF"
    ),
    "MSTU-USDT-SWAP": InstrumentSpec(
        inst_id="MSTU-USDT-SWAP",
        name_cn="微策略 2x 多头 (6% 超大波幅王)",
        symbol_display="MSTU",
        max_leverage=10,
        ct_val=1.0,
        min_sz=0.1,
        lot_sz=0.1,
        tick_sz=0.001,
        category="ETF"
    ),
    # 扩展槽位：STM 预留位 (OKX 待支持)
    "STM-USDT-SWAP": InstrumentSpec(
        inst_id="STM-USDT-SWAP",
        name_cn="意法半导体 (OKX待上线)",
        symbol_display="STM",
        max_leverage=20,
        ct_val=1.0,
        min_sz=0.1,
        lot_sz=0.1,
        tick_sz=0.01,
        category="EUROPE",
        status="PENDING_LISTING"
    ),

    "QQQ-USDT-SWAP": InstrumentSpec(
        inst_id="QQQ-USDT-SWAP",
        name_cn="纳斯达克 100 (Invesco QQQ)",
        symbol_display="QQQ",
        max_leverage=50,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="ETF"
    ),
    "NVDA-USDT-SWAP": InstrumentSpec(
        inst_id="NVDA-USDT-SWAP",
        name_cn="英伟达 (NVIDIA)",
        symbol_display="NVDA",
        max_leverage=50,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "AMD-USDT-SWAP": InstrumentSpec(
        inst_id="AMD-USDT-SWAP",
        name_cn="超威半导体 (AMD)",
        symbol_display="AMD",
        max_leverage=25,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "AMDUSDT": InstrumentSpec(
        inst_id="AMDUSDT",
        name_cn="超威半导体 (AMD)",
        symbol_display="AMD",
        max_leverage=25,
        ct_val=1.0,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="US_STOCK"
    ),
    "ETH-USDT-SWAP": InstrumentSpec(
        inst_id="ETH-USDT-SWAP",
        name_cn="以太坊 (Ethereum)",
        symbol_display="ETH",
        max_leverage=100,
        ct_val=0.1,
        min_sz=0.01,
        lot_sz=0.01,
        tick_sz=0.01,
        category="CRYPTO"
    ),
}

def get_instrument_spec(inst_id: str) -> Optional[InstrumentSpec]:
    if inst_id in CORE_INSTRUMENTS:
        return CORE_INSTRUMENTS[inst_id]
    if inst_id.endswith('USDT') and '-' not in inst_id:
        base = inst_id[:-4]
        equiv = f"{base}-USDT-SWAP"
        if equiv in CORE_INSTRUMENTS:
            matched = CORE_INSTRUMENTS[equiv]
            return InstrumentSpec(
                inst_id=inst_id,
                name_cn=matched.name_cn,
                symbol_display=matched.symbol_display,
                max_leverage=matched.max_leverage,
                ct_val=1.0,
                min_sz=matched.min_sz,
                lot_sz=matched.lot_sz,
                tick_sz=matched.tick_sz,
                category=matched.category,
                status=matched.status
            )
        try:
            from core.platform_registry import platform_registry
            m = platform_registry.get('BINANCE').markets.get(inst_id)
            if m:
                return InstrumentSpec(
                    inst_id=inst_id,
                    name_cn=inst_id,
                    symbol_display=base,
                    max_leverage=int(m.max_leverage or 20),
                    ct_val=1.0,
                    min_sz=float(m.min_size or 0.001),
                    lot_sz=float(m.lot_size or 0.001),
                    tick_sz=float(m.tick_size or 0.01),
                    category="CRYPTO",
                    status="LIVE"
                )
        except Exception:
            pass
        return InstrumentSpec(
            inst_id=inst_id,
            name_cn=inst_id,
            symbol_display=base,
            max_leverage=50,
            ct_val=1.0,
            min_sz=0.001,
            lot_sz=0.001,
            tick_sz=0.01,
            category="CRYPTO",
            status="LIVE"
        )
    return None

def list_active_instruments() -> List[InstrumentSpec]:
    return [spec for spec in CORE_INSTRUMENTS.values() if spec.status == "LIVE"]
