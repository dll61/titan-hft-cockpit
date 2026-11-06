"""Read-only acceptance budget assessment. Never grants permission to trade."""
from decimal import Decimal, ROUND_CEILING
from threading import Lock

CAP=Decimal('0.01')

def positive(value,allow_zero=False):
    try: number=Decimal(str(value))
    except Exception: raise ValueError('Invalid budget input') from None
    if not number.is_finite() or (number<0 if allow_zero else number<=0):
        raise ValueError('Invalid budget input')
    return number

class MicroBudget:
    """Cumulative reservation, no refunds or leverage assumptions, per assessment run."""
    def __init__(self):
        self.committed=Decimal('0');self._lock=Lock()
    def reserve(self,amount):
        value=positive(amount)
        with self._lock:
            if self.committed+value>CAP: raise ValueError('0.01 USDT total budget exceeded')
            self.committed+=value
            return CAP-self.committed

def assess_minimum(row,ask,taker_fee,slippage_bps='10'):
    if row.get('ctType')!='linear' or row.get('settleCcy')!='USDT':
        raise ValueError('Only verified linear USDT contracts supported by this assessment')
    try: minimum,lot,ct=[positive(row[key]) for key in ('minSz','lotSz','ctVal')]
    except KeyError: raise ValueError('Live instrument metadata missing') from None
    price=positive(ask);fee=positive(taker_fee,True);bps=positive(slippage_bps,True)
    size=(minimum/lot).to_integral_value(rounding=ROUND_CEILING)*lot
    notional=size*ct*price
    # A conservative planning reserve, NOT a guaranteed maximum realized loss.
    reserve=notional*(1+2*fee+2*bps/10000)
    return dict(min_size=format(size,'f'),notional_usdt=format(notional,'f'),
        margin_at_1x_usdt=format(notional,'f'),reserved_usdt=format(reserve,'f'),
        cap_usdt=str(CAP),within_budget=reserve<=CAP,
        basis='1x notional plus estimated round-trip fee/slippage reserve; no leverage uplift')
