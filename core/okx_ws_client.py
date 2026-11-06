"""Public market feed with receipt-time freshness and bounded reconnects."""
import asyncio
import json
import math
import random
import time
import websockets

class OKXPublicWS:
    def __init__(self, symbols, url='wss://ws.okx.com:8443/ws/v5/public', max_age=2.0):
        self.symbols=list(symbols); self.url=url; self.max_age=max_age
        self.cache={}; self.connected=False; self.last_error=None

    def ingest(self, message):
        packet=json.loads(message)
        if packet.get('event') == 'error':
            raise RuntimeError('OKX subscription rejected: '+str(packet.get('code')))
        for row in packet.get('data',[]):
            sym=row.get('instId')
            if sym not in self.symbols: continue
            last,bid,ask = [float(row[k]) for k in ('last','bidPx','askPx')]
            if not all(math.isfinite(x) and x>0 for x in (last,bid,ask)) or bid>ask:
                continue
            stamp=int(row.get('ts','0'))
            old=self.cache.get(sym)
            if old and stamp<old['exchange_ts']: continue
            self.cache[sym]=dict(instId=sym,last=last,bid=bid,ask=ask,
                exchange_ts=stamp,received_at=time.monotonic())

    def get(self,symbol):
        row=self.cache.get(symbol)
        if not self.connected or not row or time.monotonic()-row['received_at']>self.max_age:
            return None
        if abs(time.time()*1000-row['exchange_ts']) > self.max_age*1000:
            return None
        return dict(row)

    async def run(self):
        attempt=0
        while True:
            try:
                async with websockets.connect(self.url,open_timeout=5,ping_interval=None) as ws:
                    await ws.send(json.dumps({'op':'subscribe','args':[
                        {'channel':'tickers','instId':s} for s in self.symbols]}))
                    self.connected=True; attempt=0
                    while True:
                        try: message=await asyncio.wait_for(ws.recv(),timeout=15)
                        except asyncio.TimeoutError:
                            await ws.send('ping')
                            message=await asyncio.wait_for(ws.recv(),timeout=10)
                        if message != 'pong': self.ingest(message)
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.last_error=type(exc).__name__
            finally:
                self.connected=False
            await asyncio.sleep(min(30,0.5*2**min(attempt,6))+random.uniform(0,0.3))
            attempt+=1
