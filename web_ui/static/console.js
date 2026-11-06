// OKX-Titan HFT Console Frontend Driver

// Catalogues and leverage limits belong to the selected venue only.
let SYMBOLS = [];
let currentPlatform='OKX';
const PLATFORM_NAMES={OKX:'OKX',BINANCE:'Binance Futures',LIGHTER:'Lighter',HYPERLIQUID:'Hyperliquid'};

let ws=null, heartbeat=null, reconnectAttempt=0, lastPong=0, sequence=0;
let currentSymbol='',activeSlot='slot_a',currentLeverage=1,currentRatio=.20,currentStopLoss=.05;
let lastRenderedSymbol=null;
let latestState=null, latestPositions={}, pendingFocus=null, localMessage='正在连接后台';
const protectionBySymbol={};
const defaultProtection=()=>({stop_loss_enabled:false,stop_loss_pct:.05,breakeven_enabled:false,
 trailing_enabled:false,fixed_take_profit_pct:null,trailing_retrace_ratio:.30,breakeven_activation:.01,trailing_activation:.02,stop_loss_amount:null,fixed_take_profit_amount:null,trailing_activation_amount:null,trailing_retrace_amount:null});
const el=id=>document.getElementById(id);
const all=selector=>typeof document.querySelectorAll==='function'?Array.from(document.querySelectorAll(selector)):[];
const text=(id,value)=>{if(el(id))el(id).textContent=value;};
const setValue=(id,value)=>{if(el(id)&&document.activeElement!==el(id))el(id).value=value;};
const connected=()=>!!ws&&ws.readyState===WebSocket.OPEN;
const protection=()=>protectionBySymbol[currentSymbol]||=defaultProtection();
function feedback(message,error=false){localMessage=message;text('command-status',message);if(el('command-status'))el('command-status').dataset.tone=error?'error':'info';}
function freshTicker(state,symbol){
 const q=state?.tickers?.[symbol];
 if(q?.platform&&q.platform!==currentPlatform)return null;
 if(!q||q.instId!==symbol||state.feed_connected!==true)return null;
 if(![q.last,q.bid,q.ask].every(x=>Number.isFinite(x)&&x>0)||q.bid>q.ask)return null;
 const observed=q.received_ts||q.exchange_ts;
 const maxAge=q.transport==='REST_read_only'?20000:5000;
 if(!Number.isFinite(observed)||Math.abs(Date.now()-observed)>maxAge)return null;
 return q;
}
function actionBlockReason(action){
 if(!connected())return '后台已断线，指令未发送';
 const caps=latestState?.capabilities;
 const entry=action==='BUY_LONG'||action==='SELL_SHORT';
 if(!(entry?caps?.open_allowed:caps?.close_allowed))return caps?.reason||'只读验收模式：实盘开仓、平仓与撤单均未启用';
 if(entry&&!freshTicker(latestState,currentSymbol))return '当前标的行情缺失或过期，禁止开仓';
 if(entry&&latestState.reconciliation_required)return '成交状态未对账，禁止继续开仓';
 // Account breaker removed per user instruction
 return '';
}
function updateActionButtons(){
 for(const b of all('[data-trade-action]')){
  const reason=actionBlockReason(b.dataset.tradeAction);b.disabled=!!reason;b.title=reason||'按下发送指令，最终结果以交易所成交回报为准';
 }
 text('mode-status',latestState?.mode==='LIVE'?'实盘模式 (LIVE)':'只读验收 · 实盘开仓/平仓/撤单未启用');
}
function sendAction(action,payload={}){
 if(!connected()){feedback('后台已断线，指令未发送',true);return false;}
 ws.send(JSON.stringify({action,symbol:currentSymbol,request_id:`ui-${Date.now()}-${++sequence}`,...payload,platform:currentPlatform}));
 return true;
}
function switchPlatform(platform){
 if(!Object.hasOwn(PLATFORM_NAMES,platform))return;
 currentPlatform=platform;SYMBOLS=[];currentSymbol='';activeSlot='slot_a';
 latestState=null;latestPositions={};pendingFocus=null;lastRenderedSymbol=null;currentRatio=.2;currentLeverage=1;
 for(const key of Object.keys(protectionBySymbol))delete protectionBySymbol[key];
 renderState(null,{},true);sendAction('SWITCH_PLATFORM');
 feedback(`${PLATFORM_NAMES[platform]}：已切换；连接/刷新只读取账户与原生市场，不修改交易所委托`);
}
function connectPlatform(){sendAction('CONNECT_PLATFORM');}
function executeTrade(action){
 const reason=actionBlockReason(action);if(reason){feedback(reason,true);return false;}
 const sent=sendAction(action);if(sent)feedback(`${currentSymbol}：指令已发送，等待后台确认`);return sent;
}
const SYMBOL_ALIASES = {
  'SPCX': ['SPACEX', 'SPACE', '太空探索', '星舰', '马斯克火箭', 'SPCX', 'SPACEXUSDT', 'SPCXUSDT', '太空', '火箭'],
  'SPACEX': ['SPCX', 'SPCXUSDT', 'SPACEX', 'SPACE', '太空探索', '星舰', '马斯克火箭'],
  'SPACE': ['SPACE ID', 'SPACEID', 'WEB3域名', '域名币', '非SPACEX'],
  'TSLA': ['TESLA', '特斯拉', '马斯克', 'MUSK', 'ELON', '特斯'],
  'DOGE': ['DOGECOIN', '狗狗币', '狗币', '马斯克', 'ELON', 'MUSK'],
  'BE': ['BLOOM', 'BLOOM ENERGY', '布鲁姆', '布鲁姆能源', '燃料电池'],
  'SKHYNIX': ['HYNIX', '海力士', 'SK海力士', 'SK', '韩国', 'KOR', 'KOREA'],
  'KOR': ['韩国', 'KOREA', 'SK海力士', 'SKHYNIX', '海力士'],
  'SNDK': ['SANDIA', 'SANDISK', '闪迪', '桑迪亚', '闪迪存储'],
  'INTC': ['INTEL', '英特尔'],
  'AMD': ['AMD', 'ADVANCED MICRO DEVICES', '超威', '超威半导体', '苏妈'],
  'GOOGL': ['GOOGLE', 'ALPHABET', '谷歌', '谷歌A'],
  'GOOG': ['GOOGLE', 'ALPHABET', '谷歌'],
  'MUSK': ['马斯克', 'ELON', 'TSLA', 'DOGE', '特斯拉', 'SPCX', 'SPACEX'],
  'BABA': ['阿里巴巴', 'ALIBABA', '马云', '阿里'],
  'AAPL': ['苹果', 'APPLE'],
  'PLTR': ['PALANTIR', '帕兰提尔', '帕兰'],
  'COIN': ['COINBASE'],
  'MSTR': ['微策略', 'MICROSTRATEGY'],
  'SOXL': ['SEMICONDUCTOR', '半导体', '三倍半导体', '半导体3X', '三倍做多半导体'],
  'QQQ': ['NASDAQ', '纳指', '纳斯达克'],
  'NVDA': ['NVIDIA', '英伟达'],
  'ASML': ['ASML', '阿斯麦', '光刻机'],
  'NOK': ['NOKIA', '诺基亚'],
  'MSTU': ['MICROSTRATEGY', '微策略'],
  'BTC': ['BITCOIN', '比特币', '大饼'],
  'ETH': ['ETHEREUM', '以太坊', '以太', '二饼'],
  'SOL': ['SOLANA', '索拉纳', 'SOL'],
  'BNB': ['BINANCE COIN', '币安币'],
  'PEPE': ['1000PEPE', '佩佩', '青蛙'],
  'XRP': ['RIPPLE', '瑞波币', '瑞波'],
  'SUI': ['SUI'],
  'AVAX': ['AVALANCHE', '雪崩'],
  'ADA': ['CARDANO', '艾达币'],
  'NEAR': ['NEAR PROTOCOL'],
  'SHIB': ['1000SHIB', '柴犬币'],
  'FLOKI': ['1000FLOKI'],
  'BONK': ['1000BONK'],
  'WIF': ['DOGWIFHAT', '帽子狗'],
  'BOME': ['BOOK OF MEME'],
  'NEIRO': ['FIRST NEIRO'],
  'TURBO': ['TURBO'],
  'RENDER': ['RENDER', '渲染', 'GPU算力'],
  'TAO': ['BITTENSOR', 'AI龙头'],
  'FET': ['ARTIFICIAL SUPERINTELLIGENCE', '超级人工智能', 'ASI'],
  'WLD': ['WORLDCOIN', '世界币', '奥特曼'],
  'OPENAI': ['OPENAI', 'CHATGPT', '奥特曼'],
  'ANTHROPIC': ['ANTHROPIC', 'CLAUDE']
};

const PLATFORM_FAVORITES = {
  OKX: [
    { id: 'SPCX-USDT-SWAP', name: 'SPCX (SpaceX)', max: 75 },
    { id: 'SOXL-USDT-SWAP', name: 'SOXL (半导体)', max: 20 },
    { id: 'BE-USDT-SWAP', name: 'BE (Bloom)', max: 25 },
    { id: 'SKHYNIX-USDT-SWAP', name: 'SKHYNIX (海力士)', max: 50 },
    { id: 'SNDK-USDT-SWAP', name: 'SNDK (Sandia)', max: 75 },
    { id: 'TSLA-USDT-SWAP', name: 'TSLA (特斯拉)', max: 25 },
    { id: 'GOOGL-USDT-SWAP', name: 'GOOGL (谷歌)', max: 50 },
    { id: 'INTC-USDT-SWAP', name: 'INTC (英特尔)', max: 25 },
    { id: 'AMD-USDT-SWAP', name: 'AMD (超威)', max: 25 },
    { id: 'MSTU-USDT-SWAP', name: 'MSTU (微策略)', max: 10 },
    { id: 'ASML-USDT-SWAP', name: 'ASML (阿斯麦)', max: 20 },
    { id: 'NOK-USDT-SWAP', name: 'NOK (诺基亚)', max: 20 },
    { id: 'BTC-USDT-SWAP', name: 'BTC (比特币)', max: 100 },
    { id: 'ETH-USDT-SWAP', name: 'ETH (以太坊)', max: 100 },
    { id: 'SOL-USDT-SWAP', name: 'SOL (索拉纳)', max: 50 },
    { id: 'DOGE-USDT-SWAP', name: 'DOGE (狗狗币)', max: 75 }
  ],
  BINANCE: [
    { id: 'SPCXUSDT', name: 'SPCX (SpaceX)', max: 75 },
    { id: 'TSLAUSDT', name: 'TSLA (特斯拉)', max: 25 },
    { id: 'NVDAUSDT', name: 'NVDA (英伟达)', max: 50 },
    { id: 'AMDUSDT', name: 'AMD (超威)', max: 25 },
    { id: 'SOXLUSDT', name: 'SOXL (半导体)', max: 50 },
    { id: 'BEUSDT', name: 'BE (Bloom)', max: 25 },
    { id: 'SKHYNIXUSDT', name: 'SKHYNIX (海力士)', max: 50 },
    { id: 'SNDKUSDT', name: 'SNDK (Sandia)', max: 75 },
    { id: 'GOOGLUSDT', name: 'GOOGL (谷歌)', max: 50 },
    { id: 'INTCUSDT', name: 'INTC (英特尔)', max: 25 },
    { id: 'BTCUSDT', name: 'BTC (比特币)', max: 150 },
    { id: 'ETHUSDT', name: 'ETH (以太坊)', max: 150 },
    { id: 'SOLUSDT', name: 'SOL (索拉纳)', max: 100 },
    { id: 'DOGEUSDT', name: 'DOGE (狗狗币)', max: 75 },
    { id: '1000PEPEUSDT', name: 'PEPE (佩佩)', max: 75 },
    { id: 'RENDERUSDT', name: 'RENDER (算力)', max: 50 }
  ],
  LIGHTER: [],
  HYPERLIQUID: []
};

function getUserStarred() {
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(`hft_starred_${currentPlatform}`);
      return raw ? JSON.parse(raw) : [];
    }
  } catch {}
  return [];
}
function isStarred(symbolId) {
  return getUserStarred().includes(symbolId);
}
function toggleStar(symbolId, event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  let list = getUserStarred();
  if (list.includes(symbolId)) {
    list = list.filter(id => id !== symbolId);
  } else {
    list.push(symbolId);
  }
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(`hft_starred_${currentPlatform}`, JSON.stringify(list));
    }
  } catch {}
  renderSymbolBar();
  renderMarketDrawer();
  feedback(`自选列表已更新: ${symbolId}`);
}

function getSymbolKeywords(symbolId, symbolName) {
  const upperId = (symbolId || '').toUpperCase();
  const upperName = (symbolName || '').toUpperCase();
  const keywords = [upperId, upperName];
  const baseMatch = upperId.match(/^([A-Z0-9]+?)(?:-?USDT|-?USD|-?SWAP|-?PERP)?$/);
  const base = baseMatch ? baseMatch[1] : upperId;
  keywords.push(base);
  for (const [key, aliasList] of Object.entries(SYMBOL_ALIASES)) {
    if (base === key || upperId.startsWith(key + 'USDT') || upperId.startsWith(key + '-')) {
      keywords.push(...aliasList.map(a => a.toUpperCase()));
    }
  }
  return keywords;
}

let drawerCategory = 'FAV';
let symbolSearchFilter = '';

const CATEGORY_MAP = {
  TRADIFI: ['BE', 'SKHYNIX', 'SNDK', 'TSLA', 'GOOGL', 'GOOG', 'INTC', 'SOXL', 'SPCX', 'QQQ', 'NVDA', 'ASML', 'NOK', 'MSTU', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'META', 'COIN', 'PLTR', 'MSTR', 'BABA'],
  MAJOR: ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'SUI', 'ADA', 'AVAX', 'DOGE', 'DOT', 'LINK', 'NEAR', 'TRX', 'TON', 'APT', 'LTC', 'BCH'],
  MEME: ['DOGE', 'PEPE', '1000PEPE', 'SHIB', '1000SHIB', 'FLOKI', '1000FLOKI', 'BONK', '1000BONK', 'WIF', 'BOME', 'NEIRO', 'TURBO', 'POPCAT', 'MEW', 'MEME', 'PEOPLE'],
  AI: ['NVDA', 'SOXL', 'INTC', 'GOOGL', 'NEAR', 'RENDER', 'TAO', 'FET', 'WLD', 'ARKM', 'IO', 'ATH', 'AI', 'AGIX', 'OCEAN', 'PHB']
};

function toggleMarketDrawer(forceState) {
  const drawer = el('market-drawer');
  if (!drawer) return;
  const isCurrentlyHidden = drawer.classList ? drawer.classList.contains('hidden') : true;
  const shouldOpen = typeof forceState === 'boolean' ? forceState : isCurrentlyHidden;
  if (drawer.classList) {
    if (shouldOpen) drawer.classList.remove('hidden');
    else drawer.classList.add('hidden');
  }
  if (shouldOpen) renderMarketDrawer();
}

function setDrawerCategory(cat) {
  drawerCategory = cat;
  const buttons = all('#drawer-categories button');
  for (const b of buttons) {
    const isCur = b.dataset && b.dataset.cat === cat;
    b.className = isCur
      ? 'px-2.5 py-1 rounded text-xs font-black bg-cyan-500 text-slate-950 shadow cursor-pointer'
      : 'px-2.5 py-1 rounded text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 cursor-pointer';
  }
  renderMarketDrawer();
}

function renderMarketDrawer() {
  const container = el('drawer-items-container');
  if (!container) return;
  const starred = getUserStarred();
  const favList = PLATFORM_FAVORITES[currentPlatform] || [];
  const favIds = new Set([...starred, ...favList.map(f => f.id)]);
  
  let list = SYMBOLS.slice();
  if (drawerCategory === 'FAV') {
    list = list.filter(m => favIds.has(m.id));
  } else if (drawerCategory === 'ALL') {
    // all
  } else if (CATEGORY_MAP[drawerCategory]) {
    const keys = CATEGORY_MAP[drawerCategory];
    list = list.filter(m => {
      const u = m.id.toUpperCase();
      return keys.some(k => u.startsWith(k + 'USDT') || u.startsWith(k + '-') || u === k);
    });
  }

  if (symbolSearchFilter) {
    list = list.filter(m => {
      const keywords = getSymbolKeywords(m.id, m.name);
      return keywords.some(k => k.includes(symbolSearchFilter));
    });
  }

  text('drawer-count-badge', `${list.length} 个标的`);
  if (typeof container.replaceChildren === 'function') container.replaceChildren();

  for (const m of list) {
    const isCur = m.id === currentSymbol;
    const isSt = isStarred(m.id);
    const liveSpec = SYMBOLS.find(s => s.id === m.id);
    const maxLev = liveSpec && Number.isInteger(liveSpec.maxLever) ? `${liveSpec.maxLever}x` : '—';
    
    let aliasStr = '';
    for (const [key, aliasList] of Object.entries(SYMBOL_ALIASES)) {
      if (m.id.toUpperCase().includes(key)) {
        aliasStr = ` (${aliasList[0]})`;
        break;
      }
    }

    const card = (typeof document.createElement === 'function') ? document.createElement('div') : { style: {} };
    card.className = `p-2 rounded border flex flex-col justify-between cursor-pointer transition select-none ${
      isCur
        ? 'bg-cyan-950 border-cyan-400 ring-1 ring-cyan-400'
        : 'bg-slate-900 border-slate-800 hover:border-slate-600 hover:bg-slate-800'
    }`;

    const doSelect = (e) => {
      if (e && typeof e.preventDefault === 'function') e.preventDefault();
      selectSymbol(m.id);
    };
    card.onpointerdown = doSelect;
    card.onclick = doSelect;

    const topRow = (typeof document.createElement === 'function') ? document.createElement('div') : {};
    topRow.className = 'flex items-center justify-between text-xs font-bold';
    
    const starBtn = (typeof document.createElement === 'function') ? document.createElement('span') : {};
    starBtn.className = `cursor-pointer text-sm px-1 hover:scale-125 transition ${isSt ? 'text-amber-400' : 'text-slate-600 hover:text-slate-400'}`;
    starBtn.textContent = isSt ? '★' : '☆';
    starBtn.title = isSt ? '点击取消自选' : '点击加入我的自选';
    starBtn.onpointerdown = (e) => {
      if (e) {
        if (typeof e.preventDefault === 'function') e.preventDefault();
        if (typeof e.stopPropagation === 'function') e.stopPropagation();
      }
      toggleStar(m.id, e);
    };

    const symName = (typeof document.createElement === 'function') ? document.createElement('span') : {};
    symName.className = isCur ? 'text-cyan-300 truncate' : 'text-slate-200 truncate';
    symName.textContent = m.id.replace('-USDT-SWAP', '').replace('USDT', '') + aliasStr;

    if (typeof topRow.appendChild === 'function') {
      topRow.appendChild(symName);
      topRow.appendChild(starBtn);
    }
    if (typeof card.appendChild === 'function') card.appendChild(topRow);

    const btmRow = (typeof document.createElement === 'function') ? document.createElement('div') : {};
    btmRow.className = 'flex items-center justify-between text-[10px] text-slate-400 mt-1';
    
    const levSpan = (typeof document.createElement === 'function') ? document.createElement('span') : {};
    levSpan.className = 'px-1 rounded bg-slate-800 text-cyan-400 border border-slate-700 font-mono';
    levSpan.textContent = maxLev;

    const tickerQ = latestState?.tickers?.[m.id];
    const pxSpan = (typeof document.createElement === 'function') ? document.createElement('span') : {};
    pxSpan.className = 'font-mono text-slate-300';
    pxSpan.textContent = tickerQ?.last ? `$${tickerQ.last}` : '';

    if (typeof btmRow.appendChild === 'function') {
      btmRow.appendChild(levSpan);
      btmRow.appendChild(pxSpan);
    }
    if (typeof card.appendChild === 'function') card.appendChild(btmRow);

    if (typeof container.appendChild === 'function') container.appendChild(card);
  }
}

function onSymbolSearch(query) {
    symbolSearchFilter = (query || '').trim().toUpperCase();
    const selector = el('native-market');
    if (selector) {
      const filtered = SYMBOLS.filter(m => {
          if (!symbolSearchFilter) return true;
          const keywords = getSymbolKeywords(m.id, m.name);
          return keywords.some(k => k.includes(symbolSearchFilter));
      });
      if (symbolSearchFilter) {
          filtered.sort((a, b) => {
              const aSpc = a.id.startsWith('SPCX') || a.id.includes('TSLA') || a.id.includes('NVDA');
              const bSpc = b.id.startsWith('SPCX') || b.id.includes('TSLA') || b.id.includes('NVDA');
              if (aSpc && !bSpc) return -1;
              if (!aSpc && bSpc) return 1;
              const aCore = CORE_SYMS.includes(a.id);
              const bCore = CORE_SYMS.includes(b.id);
              if (aCore && !bCore) return -1;
              if (!aCore && bCore) return 1;
              return a.id.localeCompare(b.id);
          });
      }
      if (typeof selector.replaceChildren === 'function') selector.replaceChildren();
      for (const m of filtered) {
          const o = (typeof document.createElement === 'function') ? document.createElement('option') : {};
          o.value = m.id;
          const lev = Number.isInteger(m.maxLever) ? `${m.maxLever}x` : '—';
          let displayExtra = '';
          for (const [key, aliasList] of Object.entries(SYMBOL_ALIASES)) {
              if (m.id.toUpperCase().includes(key)) {
                  displayExtra = ` (${aliasList[0]})`;
                  break;
              }
          }
          o.textContent = `${m.name}${displayExtra} [${lev}]`;
          if (m.id === currentSymbol) o.selected = true;
          if (typeof selector.appendChild === 'function') selector.appendChild(o);
      }
      if (symbolSearchFilter && filtered.length > 0 && !filtered.some(m => m.id === currentSymbol)) {
          selectSymbol(filtered[0].id);
      }
    }
    const drawer = el('market-drawer');
    if (drawer && drawer.classList && !drawer.classList.contains('hidden')) {
      renderMarketDrawer();
    }
}

function onSymbolSearchKeyDown(e) {
    if (e.key === 'Enter') {
        const selector = el('native-market');
        if (selector && selector.options && selector.options.length > 0) {
            selectSymbol(selector.options[0].value);
            feedback(`已通过搜索选中标的: ${selector.options[0].value}`);
        }
    }
}

function getResolvedMaxLeverage() {
  const spec = SYMBOLS.find(s => s.id === currentSymbol);
  if (spec && Number.isInteger(spec.maxLever)) {
    return spec.maxLever;
  }
  if (currentPlatform === 'OKX') {
    if (spec && Number.isInteger(spec.market_max_leverage)) {
      return spec.market_max_leverage;
    }
    const favObj = (PLATFORM_FAVORITES[currentPlatform] || []).find(f => f.id === currentSymbol);
    if (favObj && Number.isInteger(favObj.max)) {
      return favObj.max;
    }
  }
  return null;
}
function updateLeverageTierLabels() {
  const spec = SYMBOLS.find(s => s.id === currentSymbol);
  const knownMax = getResolvedMaxLeverage();
  const max = knownMax ? knownMax : 1;
  const tiers = {
    low: { pct: '25%', val: Math.max(1, roundHalfEven(max * 0.25)) },
    mid: { pct: '50%', val: Math.max(1, roundHalfEven(max * 0.50)) },
    high: { pct: '75%', val: Math.max(1, roundHalfEven(max * 0.75)) },
    max: { pct: 'MAX', val: max }
  };
  for (const b of all('[data-lever-tier]')) {
    const tier = b.dataset && b.dataset.leverTier;
    const t = tiers[tier];
    if (t) {
      b.textContent = (tier === "max" ? "顶格 MAX" : tier === "low" ? "低" : tier === "mid" ? "中" : "高") + " (" + (knownMax ? t.val + "x" : t.pct) + ")";
    }
  }
}

function getActiveFavorites() {
  const baseFavs = PLATFORM_FAVORITES[currentPlatform] || [];
  const starred = getUserStarred();
  const seen = new Set();
  const result = [];
  for (const sid of starred) {
    if (!seen.has(sid)) {
      seen.add(sid);
      const spec = SYMBOLS.find(s => s.id === sid);
      let shortName = sid.replace('-USDT-SWAP', '').replace('USDT', '');
      for (const [key, aliasList] of Object.entries(SYMBOL_ALIASES)) {
        if (sid.toUpperCase().includes(key)) {
          shortName = `${key} (${aliasList[0]})`;
          break;
        }
      }
      result.push({
        id: sid,
        name: shortName,
        max: spec && Number.isInteger(spec.maxLever) ? spec.maxLever : 20
      });
    }
  }
  for (const f of baseFavs) {
    if (!seen.has(f.id)) {
      seen.add(f.id);
      result.push(f);
    }
  }
  return result;
}

const CORE_SYMS=['SPCX-USDT-SWAP','SPCXUSDT','TSLA-USDT-SWAP','TSLAUSDT','GOOGL-USDT-SWAP','BE-USDT-SWAP','BTC-USDT-SWAP','SKHYNIX-USDT-SWAP','SNDK-USDT-SWAP','INTC-USDT-SWAP','AMD-USDT-SWAP','NOK-USDT-SWAP','ASML-USDT-SWAP','SOXL-USDT-SWAP','SOXLUSDT','MSTU-USDT-SWAP','AMDUSDT','BTCUSDT','ETHUSDT'];
function renderSymbolBar(){
 const favs = getActiveFavorites();
 const bars = [el('symbol-bar'), el('pinned-favorites-bar')].filter(Boolean);
 
 for (const bar of bars) {
  const isPinned = bar.id === 'pinned-favorites-bar';
  const favIds = favs.map(f => f.id);
  if (currentSymbol && !favIds.includes(currentSymbol)) {
    favIds.push(currentSymbol);
  }
  
  const existingButtons = (typeof bar.querySelectorAll === 'function') 
    ? Array.from(bar.querySelectorAll('button[data-fav-id]')) 
    : (Array.isArray(bar.children) ? Array.from(bar.children) : []);
  const existingIds = existingButtons.map(b => (b.dataset ? b.dataset.favId : b['data-fav-id']));
  const needRebuild = existingIds.join(',') !== favIds.join(',');

  if (needRebuild) {
    if (typeof bar.replaceChildren === 'function') {
      bar.replaceChildren();
    } else {
      bar.innerHTML = '';
    }
    for (const fId of favIds) {
      const f = favs.find(x => x.id === fId) || { id: fId, name: fId, max: 20 };
      const liveSpec = SYMBOLS.find(s => s.id === f.id);
      const maxLever = liveSpec && Number.isInteger(liveSpec.maxLever) ? liveSpec.maxLever : f.max;
      const b = (typeof document.createElement === 'function') ? document.createElement('button') : { dataset: {} };
      b.type = 'button';
      if (!b.dataset) b.dataset = {};
      b.dataset.favId = f.id;
      if (typeof b.setAttribute === 'function') {
        b.setAttribute('data-fav-id', f.id);
        if (isPinned) b.setAttribute('data-pinned-id', f.id);
        else b.setAttribute('data-symbol-id', f.id);
      }

      b.textContent = `${f.name} · ${maxLever}x`;
      
      const doSelect = (e) => {
        if (e) {
          if (typeof e.preventDefault === 'function') e.preventDefault();
          if (typeof e.stopPropagation === 'function') e.stopPropagation();
        }
        selectSymbol(f.id);
      };
      b.onpointerdown = doSelect;
      b.onclick = doSelect;
      if (typeof bar.appendChild === 'function') bar.appendChild(b);
    }
  }

  const btnsToUpdate = (typeof bar.querySelectorAll === 'function')
    ? bar.querySelectorAll('button[data-fav-id]')
    : (Array.isArray(bar.children) ? bar.children : []);
  for (const b of btnsToUpdate) {
    if (!b) continue;
    const bid = b.dataset ? b.dataset.favId : b['data-fav-id'];
    const isSelected = bid === currentSymbol;
    b.className = isSelected
      ? 'py-1 px-2.5 rounded text-xs font-black border bg-cyan-500 text-slate-950 border-cyan-300 ring-2 ring-cyan-400 shadow-lg cursor-pointer select-none'
      : 'py-1 px-2.5 rounded text-xs font-semibold border bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700 hover:text-white cursor-pointer select-none';
  }
 }
}
function selectSymbol(symbol){
 if(!SYMBOLS.some(s=>s.id===symbol))return;
 currentSymbol=symbol;pendingFocus={symbol,slot:activeSlot};renderSymbolBar();renderState(latestState,latestPositions,true);
 if(!sendAction('SWITCH_SYMBOL',{symbol}))pendingFocus=null;
}
function switchSlot(slot){
 if(!['slot_a','slot_b'].includes(slot))return;
 activeSlot=slot;
 const symbol=latestState?.slots?.[slot]?.symbol;
 if(symbol&&SYMBOLS.some(s=>s.id===symbol))currentSymbol=symbol;
 pendingFocus={symbol:currentSymbol,slot};
 renderState(latestState,latestPositions,true);if(!sendAction('SWITCH_SLOT',{slot}))pendingFocus=null;
}
function roundHalfEven(v){const n=Math.floor(v);return v-n===.5?(n%2===0?n:n+1):Math.round(v);}
function onLeverageSliderChange(v){
 const spec=SYMBOLS.find(s=>s.id===currentSymbol),value=Number(v);
 const max=getResolvedMaxLeverage();
 if(!Number.isInteger(max)||!Number.isInteger(value)||value<1||value>max){feedback("杠杆数值超出当前标的范围",true);return;}
 if(!sendAction("SET_LEVERAGE",{leverage:value}))return;
 currentLeverage=value;text("lever-badge",value + "x（待确认）");setValue("lever-slider",value);
}
function setLeverageTier(tier){
 const spec=SYMBOLS.find(s=>s.id===currentSymbol),r={low:.25,mid:.5,high:.75,max:1}[tier];
 const max=getResolvedMaxLeverage();
 if(r&&Number.isInteger(max))onLeverageSliderChange(Math.max(1,roundHalfEven(max*r)));
}
function onRatioSliderChange(v){
 const value=Number(v);if(!Number.isFinite(value)||value<=0||value>100){feedback('仓位百分比必须大于0且不超过100',true);return;}
 if(!sendAction('SET_RATIO',{ratio:value/100}))return;
 currentRatio=value/100;setValue('ratio-slider',value);setValue('ratio-input',value);text('ratio-badge',`${value.toFixed(3)}%（待确认）`);
}
function setRatio(value){onRatioSliderChange(value*100);}
function onStopLossSliderChange(v){
 const value=Number(v);if(!Number.isFinite(value)||value<0){feedback('止损幅度必须是非负有限数值',true);return;}
 if(!sendAction('SET_STOP_LOSS',{pct:value/100}))return;
 currentStopLoss=value/100;protection().stop_loss_pct=currentStopLoss;renderProtection();
}
function setStopLoss(v){onStopLossSliderChange(v*100);}
const amountKeys=['stop_loss_amount','fixed_take_profit_amount','trailing_activation_amount','trailing_retrace_amount'];
function validProtection(key,value){
 if(['stop_loss_enabled','breakeven_enabled','trailing_enabled'].includes(key))return typeof value==='boolean';
 if((key==='fixed_take_profit_pct'||amountKeys.includes(key))&&value===null)return true;
 const keys=['stop_loss_pct','fixed_take_profit_pct','trailing_retrace_ratio','breakeven_activation','trailing_activation',...amountKeys];
 return keys.includes(key)&&Number.isFinite(value)&&(['fixed_take_profit_pct','fixed_take_profit_amount'].includes(key)||value>=0);
}
function setProtectionAmount(key,value){return setProtection(key,String(value).trim()===''?null:Number(value));}
function setProtection(key,value){
 if(!validProtection(key,value)){feedback('保护参数无效，设置未发送',true);renderProtection();return false;}
 if(!sendAction('SET_RISK',{settings:{[key]:value}}))return false;
 protection()[key]=value;renderProtection();feedback('保护设置已发送，等待后台确认');return true;
}
function setProtectionPercent(key,value){return setProtection(key,Number(value)/100);}
function toggleFixedTP(on){
 if(on)return setProtection('fixed_take_profit_pct',Number(el('risk-tp-pct').value)/100);
 const settings={fixed_take_profit_pct:null,fixed_take_profit_amount:null};
 if(!sendAction('SET_RISK',{settings}))return false;
 Object.assign(protection(),settings);renderProtection();return true;
}
function disableProtection(){
 const settings={stop_loss_enabled:false,breakeven_enabled:false,trailing_enabled:false,fixed_take_profit_pct:null,...Object.fromEntries(amountKeys.map(k=>[k,null]))};
 if(!sendAction('SET_RISK',{settings}))return;
 Object.assign(protection(),settings);renderProtection();feedback('关闭保护的设置已发送，等待后台确认');
}
function setExecution(){
 const mode=el('execution-mode').value,bps=Number(el('slippage-bps').value);
 if(!['balanced','speed','maker_only'].includes(mode)||!Number.isFinite(bps)||bps<=0||bps>100){feedback('执行偏好或追价上限无效',true);return;}
 sendAction('SET_EXECUTION',{mode,bps});
 renderExecutionModeButtons(mode);
}
function switchExecutionMode(mode){
 const sel=el('execution-mode');
 if(sel){ sel.value=mode; setExecution(); }
 renderExecutionModeButtons(mode);
}
function renderExecutionModeButtons(mode){
 const btnSpeed=el('btn-mode-speed'), btnBal=el('btn-mode-balanced');
 if(!btnSpeed || !btnBal) return;
 if(mode==='speed'){
  btnSpeed.className='px-3 py-1.5 rounded-lg font-black transition flex items-center gap-1 bg-amber-500 text-slate-950 shadow-md ring-2 ring-amber-400';
  btnBal.className='px-3 py-1.5 rounded-lg font-normal transition flex items-center gap-1 bg-slate-800 text-slate-400 border border-slate-700 hover:text-white';
 } else {
  btnBal.className='px-3 py-1.5 rounded-lg font-black transition flex items-center gap-1 bg-cyan-600 text-white shadow-md ring-2 ring-cyan-400';
  btnSpeed.className='px-3 py-1.5 rounded-lg font-normal transition flex items-center gap-1 bg-slate-800 text-slate-400 border border-slate-700 hover:text-white';
 }
}
function setAccountBreaker(){
 const enabled=!!el('account-breaker-on').checked,pct=Number(el('account-breaker-pct').value)/100;
 if(!Number.isFinite(pct)||pct<=0||pct>=1){feedback('账户熔断比例必须大于0且小于100%',true);return;}
 sendAction('SET_ACCOUNT_BREAKER',{enabled,pct});
}
function renderProtection(){
 const p=protection();currentStopLoss=p.stop_loss_pct;text('risk-symbol',currentSymbol);
 for(const [id,key] of [['risk-sl-on','stop_loss_enabled'],['risk-be-on','breakeven_enabled'],['risk-tr-on','trailing_enabled']])el(id).checked=p[key];
 el('risk-tp-on').checked=p.fixed_take_profit_pct!==null||p.fixed_take_profit_amount!=null;
 for(const key of amountKeys)setValue('risk-'+key,p[key]??'');
 const fields=[['risk-sl-pct','stop_loss_pct'],['risk-be-pct','breakeven_activation'],['risk-tr-pct','trailing_retrace_ratio'],['risk-activation-pct','trailing_activation']];
 if(p.fixed_take_profit_pct!==null)fields.push(['risk-tp-pct','fixed_take_profit_pct']);
 for(const [id,key] of fields)setValue(id,Number((p[key]*100).toFixed(6)));
 setValue('sl-slider',p.stop_loss_pct*100);
 text('sl-badge',`${p.stop_loss_enabled?'-':'未启用 · -'}${(p.stop_loss_pct*100).toFixed(2)}% ROE`);
 const unit=el('account-currency').textContent||'账户计价币';
 const threshold=(amount,pct)=>amount!=null?`${amount} ${unit}`:`${(pct*100).toFixed(2)}%`;
 const rules=[`止损：${p.stop_loss_enabled?threshold(p.stop_loss_amount,p.stop_loss_pct):'关闭'}`,
 `固定止盈/回升退出：${p.fixed_take_profit_pct===null&&p.fixed_take_profit_amount==null?'关闭':threshold(p.fixed_take_profit_amount,p.fixed_take_profit_pct)}`,
 `保本：${p.breakeven_enabled?(p.breakeven_activation*100).toFixed(2)+'% ROE激活':'关闭'}`,
 `动态止盈：${p.trailing_enabled?threshold(p.trailing_activation_amount,p.trailing_activation)+' 激活；峰值回撤 '+threshold(p.trailing_retrace_amount,p.trailing_retrace_ratio):'关闭'}`];
 text('risk-summary',rules.join('；'));
 text('risk-status',p.stop_loss_enabled||p.breakeven_enabled||p.trailing_enabled||p.fixed_take_profit_pct!==null||p.fixed_take_profit_amount!=null?'已配置；实盘保护尚未启用，请查看顶部状态':'控制台保护全部关闭；不会修改 APP 保护单');
 for(const input of all('[data-risk-slider]'))setValue(input.id,p[input.dataset.riskSlider]*100);
}
function money(value){return Number.isFinite(value)?`$${value.toFixed(4)}`:'—';}
function renderPosition(state,positions,quote){
 const card=el('position-card'),local=positions?.[currentSymbol];
 const external=(state?.exchange_positions||[]).filter(p=>p.inst_id===currentSymbol);
 const rows=local?[local]:external;
 card.replaceChildren();
 if(rows.length&&state?.positions_verified!==true){const warning=document.createElement('p');warning.textContent='以下为未完成当前账户核验的持仓快照，不代表实时持仓。';card.appendChild(warning);}
 if(!rows.length){
  const empty=document.createElement('div');empty.className='p-4 text-slate-400';
  empty.textContent=state?.positions_verified?'交易所已同步：当前标的无持仓':'当前未核验交易所持仓；不能据此认定空仓';card.appendChild(empty);return;
 }
 for(const p of rows){
  const panel=document.createElement('div');panel.className='bg-slate-950 rounded-xl p-4 border border-slate-700 mb-3';  const title=document.createElement('strong');title.textContent=`${currentSymbol} · ${p.side==='buy'?'多单 LONG':'空单 SHORT'} · ${p.size} ${SYMBOLS.find(s=>s.id===currentSymbol)?.quantity_unit==='contracts'?'张':'原生数量'}`;panel.appendChild(title);
  const details=document.createElement('p');details.textContent=`成交均价 ${money(p.entry_price)} / 保证金 ${money(p.margin)} / ${p.leverage}x`;panel.appendChild(details);
  const line=document.createElement('p');
  if(!quote||!Number.isFinite(p.ct_val)||p.ct_val<=0){line.textContent='行情或合约规格未核验，盈亏暂不可计算';}
  else{
   const mark=p.side==='buy'?quote.bid:quote.ask;
   const gross=(p.side==='buy'?mark-p.entry_price:p.entry_price-mark)*p.size*p.ct_val;
   const estimate=gross-mark*p.size*p.ct_val*.0008;
   line.textContent=`预估净浮盈 ${money(estimate)}；ROE ${p.margin>0?(estimate/p.margin*100).toFixed(2)+'%':'—'}（预估费用，非结算账单）`;
  }
  panel.appendChild(line);
  if(local){const rule=document.createElement('p');rule.textContent=`历史利润峰值 ${money(p.hwm_usd)} / ${protection().trailing_enabled?'利润回撤 '+(protection().trailing_retrace_ratio*100).toFixed(2)+'%':'回撤止盈关闭'}`;panel.appendChild(rule);}
  card.appendChild(panel);
 }
}
function renderState(state,positions={},preserveFocus=false){
 if(state?.platform&&state.platform!==currentPlatform)return;
 if(state&&!preserveFocus&&Array.isArray(state.markets)){
  SYMBOLS=state.markets.filter(m=>m.platform===currentPlatform).map(m=>({...m,maxLever:m.max_leverage}));
  lastRenderedSymbol=null;
  const selector=el('native-market');
  if(selector){selector.replaceChildren();for(const m of SYMBOLS){const o=document.createElement('option');o.value=m.id;o.textContent=`${m.name} (${m.id})`;selector.appendChild(o);}}
 }
 if(state?.selected_market?.id){
  const spec=SYMBOLS.find(s=>s.id===state.selected_market.id);
  if(spec){if(spec.maxLever!==state.selected_market.max_leverage)lastRenderedSymbol=null;Object.assign(spec,state.selected_market,{maxLever:state.selected_market.max_leverage});}
 }

 if(state&&!preserveFocus){
  latestState=state;latestPositions=positions||{};
  const focusConfirmed=!pendingFocus||(state.focused_symbol===pendingFocus.symbol&&state.active_slot===pendingFocus.slot);
  if(focusConfirmed){
   pendingFocus=null;
   if(SYMBOLS.some(s=>s.id===state.focused_symbol))currentSymbol=state.focused_symbol;
   if(['slot_a','slot_b'].includes(state.active_slot))activeSlot=state.active_slot;
  }
 }
 if(state?.protection)for(const [sym,settings] of Object.entries(state.protection))protectionBySymbol[sym]={...defaultProtection(),...settings};
 renderProtection();renderSymbolBar();text('focused-title',currentSymbol||'先连接平台并选择标的');
 const spec=SYMBOLS.find(s=>s.id===currentSymbol),knownMax=getResolvedMaxLeverage();
 text("focused-desc", spec ? (spec.name + " · " + (knownMax ? "元数据/档位参考上限 " + knownMax + "x" : "杠杆上限未核验") + " · " + (spec.quantity_unit || "原生数量")) : "当前平台尚无已核验标的元数据");
 el('lever-slider').max=knownMax?knownMax:1;el('lever-slider').disabled=!knownMax;
 for(const b of all('[data-lever-tier]'))b.disabled=!knownMax;
 setValue('native-market',currentSymbol);
 const isLiveVenue = state?.mode === 'LIVE' && ['OKX', 'BINANCE'].includes(currentPlatform);
  text('platform-status',`${PLATFORM_NAMES[currentPlatform]} · ${state?.connection_status||'NOT_CONNECTED'} · ${isLiveVenue ? '实盘就绪 · 交易已放行' : '只读连接 · 交易执行未验收'}`);
 for(const b of all('[data-platform]'))b.setAttribute('aria-pressed',String(b.dataset.platform===currentPlatform));
 text('account-currency',state?.account?.currency||'币种未核验');
 text('account-age',Number.isFinite(state?.snapshot_age_seconds)?`账户/持仓快照 ${state.snapshot_age_seconds.toFixed(1)} 秒前；连接/刷新可更新`:'账户与持仓尚未读取');
 currentLeverage=state?.leverage_map?.[currentSymbol]||1;text('lever-badge',`${currentLeverage}x`);setValue('lever-slider',currentLeverage);
 currentRatio=state?.ratio_map?.[currentSymbol]||currentRatio;text('ratio-badge',`${(currentRatio*100).toFixed(3)}%`);setValue('ratio-input',currentRatio*100);setValue('ratio-slider',currentRatio*100);
 if(state?.execution_mode){setValue('execution-mode',state.execution_mode);renderExecutionModeButtons(state.execution_mode);}
 if(Number.isFinite(state?.max_slippage_bps))setValue('slippage-bps',state.max_slippage_bps);
 if(state?.account_breaker){el('account-breaker-on').checked=state.account_breaker.enabled;setValue('account-breaker-pct',state.account_breaker.drawdown_pct*100);}
 const validAccount=connected()&&state?.account_valid===true;
 text('total-equity',validAccount?money(state.account.total_equity):'—');text('avail-bal',validAccount?money(state.account.available_balance):'—');
 const quote=connected()?freshTicker(state,currentSymbol):null;
 text('focused-price',quote?money(quote.last):'—');text('focused-bid',quote?String(quote.bid):'—');text('focused-ask',quote?String(quote.ask):'—');
 text('build-status',state?.build_id?'后台版本 '+state.build_id:'后台版本未确认，更新后需重启控制台');
 text('market-status',quote?(quote.timestamp_source==='local_receive_time'?'已收到行情；交易所时间戳未提供':`时间戳已校验 · ${quote.transport||'公共WS'}${quote.price_kind==='midpoint'?' · 中间价':''}`):'当前标的行情未核验或已过期');
 for(const slot of ['slot_a','slot_b']){const b=el(slot==='slot_a'?'btn-slot-a':'btn-slot-b');b.setAttribute?.('aria-pressed',String(activeSlot===slot));}
 for(const b of all('[data-ratio]'))b.setAttribute('aria-pressed',String(Math.abs(Number(b.dataset.ratio)-currentRatio)<1e-10));
 for(const b of all('[data-lever-tier]')){const r={low:.25,mid:.5,high:.75,max:1}[b.dataset.leverTier];b.setAttribute('aria-pressed',String(knownMax&&Math.max(1,roundHalfEven(knownMax*r))===currentLeverage));}
 renderPosition(state,positions||{},quote);updateActionButtons();
 text('connection-status',connected()?'后台已连接':'后台未连接');
 if(state?.logs){el('log-feed').replaceChildren(...state.logs.map(l=>{const row=document.createElement('div');row.textContent=l;return row;}));}
}
function initWebSocket(){
 const protocol=window.location.protocol==='https:'?'wss:':'ws:';ws=new WebSocket(`${protocol}//${window.location.host}/ws`);
 ws.onopen=()=>{reconnectAttempt=0;lastPong=Date.now();text('connection-status','后台已连接');updateActionButtons();clearInterval(heartbeat);
  if(currentPlatform!=='OKX')sendAction('SWITCH_PLATFORM');
  heartbeat=setInterval(()=>{if(Date.now()-lastPong>6500){ws.close();return;}sendAction('PING',{sent_at:performance.now()});},2000);};
 ws.onmessage=event=>{
  let msg;try{msg=JSON.parse(event.data);}catch{feedback('后台消息格式异常',true);return;}
  if(msg.platform&&msg.platform!==currentPlatform)return;
  if(msg.type==='PONG'){lastPong=Date.now();text('ping-rtt',`${(performance.now()-msg.sent_at).toFixed(1)}ms 本地`);}
  else if(msg.type==='STATE_UPDATE')renderState(msg.data,msg.positions);
  else if(msg.type==='ERROR'){if(msg.platform&&msg.platform!==currentPlatform)return;pendingFocus=null;feedback(msg.message||'指令执行失败',true);if(latestState)renderState(latestState,latestPositions);}
  else if(msg.type==='ACK'||Object.prototype.hasOwnProperty.call(msg,'success')){
   feedback(msg.success===false?'指令未完成：'+(msg.msg||'等待核验'):msg.type==='ACK'?'配置已由后台确认':'交易所结果已返回；状态以持仓同步为准',msg.success===false);
  }
 };
 ws.onerror=()=>feedback('后台连接异常；指令不会自动重放',true);
 ws.onclose=()=>{pendingFocus=null;clearInterval(heartbeat);text('ping-rtt','—');renderState(latestState,latestPositions,true);feedback('后台已断线，指令不会自动重放',true);
 setTimeout(initWebSocket,Math.min(30000,500*2**Math.min(reconnectAttempt++,6))+Math.random()*300);};
}
window.addEventListener('keydown',e=>{
 if(e.repeat||e.isComposing)return;
 if(e.ctrlKey&&!e.altKey&&!e.metaKey&&e.code==='Space'){e.preventDefault();executeTrade('FLATTEN_ALL');return;}
 if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||e.target.isContentEditable)return;
 if(e.ctrlKey||e.altKey||e.metaKey)return;
 if(e.code==='Space'){e.preventDefault();executeTrade('FLATTEN_CURRENT');return;}
 const key=e.key.toUpperCase();
 if(key==='B'||e.code==='ArrowUp'){e.preventDefault();executeTrade('BUY_LONG');}
 else if(key==='S'||e.code==='ArrowDown'){e.preventDefault();executeTrade('SELL_SHORT');}
 else if(key==='H')executeTrade('PARTIAL_CLOSE');
 else if(key==='X')switchSlot(activeSlot==='slot_a'?'slot_b':'slot_a');
 else if(key==='J')setProtection('stop_loss_enabled',!protection().stop_loss_enabled);
 else if(key==='K')toggleFixedTP(protection().fixed_take_profit_pct===null);
 else if(key==='Q')setProtection('breakeven_enabled',!protection().breakeven_enabled);
 else if(key==='G')setProtection('trailing_enabled',!protection().trailing_enabled);
 else if(key==='Z'){el('account-breaker-on').checked=!el('account-breaker-on').checked;setAccountBreaker();}
 else if(key==='L'){const max=getResolvedMaxLeverage();if(!Number.isInteger(max))return;const values=[.25,.5,.75,1].map(r=>Math.max(1,roundHalfEven(max*r)));setLeverageTier(['low','mid','high','max'][(values.indexOf(currentLeverage)+1)%4]);}
 else if(key==='F'){const values=[.03,.05,.08,.10];setStopLoss(values[(values.indexOf(currentStopLoss)+1)%4]);}
 else if(['1','2','3','4','5','6'].includes(e.key))setRatio([.05,.10,.20,.25,1/3,.5][Number(e.key)-1]);
 else if(SYMBOLS.length&&(e.code==='Tab'||key===']'||key==='[')){e.preventDefault();const i=SYMBOLS.findIndex(s=>s.id===currentSymbol);selectSymbol(SYMBOLS[(i+(key==='['?-1:1)+SYMBOLS.length)%SYMBOLS.length].id);}
});
window.onload=()=>{initWebSocket();renderState(null,{},true);};