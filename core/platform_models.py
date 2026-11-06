"""Native market identities and verifiable platform leverage constraints."""
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Optional


def number(value,positive=False):
    try:value=Decimal(str(value))
    except Exception:raise ValueError('Invalid native market number') from None
    if not value.is_finite() or value<0 or (positive and value<=0):raise ValueError('Invalid native market number')
    return value

def leverage(value):
    if value in (None,''):return None
    result=number(value,True)
    if result!=result.to_integral_value():raise ValueError('Leverage must be an integer')
    return int(result)

@dataclass(frozen=True)
class NativeMarket:
    platform:str
    native_id:str
    label:str
    max_leverage:Optional[int]
    quantity_unit:str
    contract_value:Decimal
    min_size:Decimal
    lot_size:Decimal
    tick_size:Optional[Decimal]
    min_notional:Optional[Decimal]=None
    quote_currency:str='USDT'
    tiers:tuple=field(default_factory=tuple)
    limit_source:str='public_market_metadata'
    size_decimals:Optional[int]=None
    price_decimals:Optional[int]=None
    price_significant_digits:Optional[int]=None
    margin_mode:str='cross_or_isolated'

    def leverage_limit(self,notional=0):
        amount=number(notional)
        if self.max_leverage is None:return None
        if not self.tiers:return self.max_leverage
        match=[cap for low,high,cap in self.tiers if amount>=low and (high is None or amount<high)]
        return min(self.max_leverage,min(match)) if match else None

    def view(self,notional=0):
        return dict(id=self.native_id,native_id=self.native_id,name=self.label,platform=self.platform,
            max_leverage=self.leverage_limit(notional),market_max_leverage=self.max_leverage,
            quantity_unit=self.quantity_unit,ct_val=str(self.contract_value),min_sz=str(self.min_size),lot_sz=str(self.lot_size),
            tick_sz=str(self.tick_size) if self.tick_size is not None else None,min_notional=str(self.min_notional) if self.min_notional is not None else None,
            quote_currency=self.quote_currency,limit_source=self.limit_source,metadata_verified=True,
            size_decimals=self.size_decimals,price_decimals=self.price_decimals,price_significant_digits=self.price_significant_digits,margin_mode=self.margin_mode)

def parse_okx(row):
    if row.get('state')!='live' or row.get('ctType')!='linear' or row.get('settleCcy')!='USDT':raise ValueError('Unsupported OKX market')
    return NativeMarket('OKX',row['instId'],row['instId'],leverage(row.get('lever')),'contracts',
        number(row['ctVal'],True),number(row['minSz'],True),number(row['lotSz'],True),number(row['tickSz'],True))

def parse_binance(row,brackets):
    if row.get('status')!='TRADING' or row.get('contractType') not in ('PERPETUAL', 'TRADIFI_PERPETUAL') or row.get('marginAsset')!='USDT':raise ValueError('Unsupported Binance market')
    filters={f['filterType']:f for f in row['filters']}
    lot=filters['LOT_SIZE'];price=filters['PRICE_FILTER'];minimum=filters.get('MIN_NOTIONAL',filters.get('NOTIONAL',{}))
    tiers=tuple((number(t['notionalFloor']),number(t['notionalCap'],True),leverage(t['initialLeverage'])) for t in brackets)
    if any(t[2] is None for t in tiers):raise ValueError('Unverified Binance leverage bracket')
    return NativeMarket('BINANCE',row['symbol'],row['symbol'],max((t[2] for t in tiers),default=None),'base_quantity',
        Decimal(1),number(lot['minQty'],True),number(lot['stepSize'],True),number(price['tickSize'],True),
        number(minimum.get('notional',minimum.get('minNotional'))) if minimum else None,
        tiers=tiers,limit_source='authenticated_account_brackets' if tiers else 'unverified_account_brackets')

def parse_hyperliquid(row,tables):
    if row.get('isDelisted'):raise ValueError('Delisted Hyperliquid market')
    decimals=int(row['szDecimals'])
    if not 0<=decimals<=6:raise ValueError('Invalid Hyperliquid size precision')
    table=dict(tables).get(row.get('marginTableId'),{}).get('marginTiers',[])
    table=sorted(table,key=lambda item:number(item['lowerBound']))
    tiers=tuple((number(t['lowerBound']),number(table[i+1]['lowerBound']) if i+1<len(table) else None,leverage(t['maxLeverage'])) for i,t in enumerate(table))
    if any(t[2] is None for t in tiers):raise ValueError('Unknown Hyperliquid margin table limit')
    return NativeMarket('HYPERLIQUID',row['name'],row['name'],leverage(row.get('maxLeverage')),'base_quantity',Decimal(1),
        Decimal(10)**-decimals,Decimal(10)**-decimals,None,quote_currency='USDC',tiers=tiers,
        size_decimals=decimals,price_decimals=6-decimals,price_significant_digits=5,
        margin_mode=row.get('marginMode') or ('isolated_only' if row.get('onlyIsolated') else 'cross_or_isolated'))

def parse_lighter(row):
    if row.get('status')!='active':raise ValueError('Inactive Lighter market')
    imf=number(row.get('min_initial_margin_fraction'),True)
    if imf>10000:raise ValueError('Invalid Lighter margin fraction')
    size_decimals=int(row['supported_size_decimals']);price_decimals=int(row['supported_price_decimals'])
    if not 0<=size_decimals<=18 or not 0<=price_decimals<=18:raise ValueError('Invalid Lighter precision')
    maximum=int(Decimal(10000)//imf)
    return NativeMarket('LIGHTER',str(row['market_id']),row['symbol'],maximum,'base_quantity',Decimal(1),
        number(row['min_base_amount'],True),Decimal(10)**-size_decimals,Decimal(10)**-price_decimals,
        number(row['min_quote_amount'],True),quote_currency='USDC',size_decimals=size_decimals,price_decimals=price_decimals,
        limit_source='market_min_initial_margin_fraction')
