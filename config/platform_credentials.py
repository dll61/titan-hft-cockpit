"""
Universal Platform Credential Loader for Titan HFT Cockpit.
Priority:
  1. Environment Variables (e.g. BINANCE_API_KEY, BINANCE_API_SECRET)
  2. Local credentials.json or config/credentials.json (gitignored)
  3. Custom directory configured via TITAN_CREDENTIALS_DIR
Values never leak to public logs, repr, or exception tracebacks.
"""
import os
import json
import re
from pathlib import Path
from dataclasses import dataclass, field

@dataclass(repr=False)
class PlatformCredentials:
    values: dict = field(default_factory=dict, repr=False)
    def __repr__(self): return 'PlatformCredentials(<redacted>)'

def parse_text(raw: str, platform: str) -> PlatformCredentials:
    values = {}
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    def field_name(label):
        key = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', str(label).lower())
        if ('索引' in key or 'index' in key) and ('api' in key or '密钥' in key): return 'api_key_index'
        if ('account' in key or '账户' in key or '帐户' in key) and ('index' in key or '索引' in key or '序号' in key): return 'account_index'
        if key in ('address', 'wallet', 'walletaddress', 'accountaddress') or '钱包地址' in key: return 'address'
        if 'private' in key or '私钥' in key or 'secret' in key or (platform == 'BINANCE' and key == '密钥'):
            return 'secret_key' if platform == 'BINANCE' else 'private_key'
        if key in ('apikey', 'apikeyid', 'api公钥', '公钥', 'api密钥'): return 'api_key'
        return None

    for candidate in (raw, *lines):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                for key, value in obj.items():
                    name = field_name(key)
                    if name: values[name] = str(value).strip()
        except (ValueError, TypeError): pass

    for i, line in enumerate(lines):
        parts = re.split(r'[:：=]', line, maxsplit=1)
        name = field_name(parts[0])
        if not name: continue
        value = parts[1].strip() if len(parts) > 1 else ''
        if not value and i + 1 < len(lines):
            following = lines[i + 1]
            next_parts = re.split(r'[:：=]', following, maxsplit=1)
            normalized = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', following.lower())
            is_label = bool(field_name(next_parts[0])) and (len(next_parts) > 1 or normalized in (
                'apikey', 'apikeyid', 'api密钥', 'api公钥', '公钥', '密钥', '私钥', 'secret', 'secretkey', 'apisecret', 'apisecretkey',
                'privatekey', 'apiprivatekey', 'address', 'walletaddress', 'accountaddress', '钱包地址', 'accountindex', 'apikeyindex'))
            if not is_label: value = following
        if value: values.setdefault(name, value)

    if platform == 'HYPERLIQUID':
        acc_m = re.search(r'(?:账号地址|账户地址|account\s*address|wallet|account)[^0-9a-fA-Fx]*(0x[0-9a-fA-F]{40})', raw, re.I)
        if acc_m: values.setdefault('address', acc_m.group(1))
        addresses = re.findall(r'(?<![0-9a-fA-F])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])', raw)
        private = re.findall(r'(?<![0-9a-fA-F])0x[0-9a-fA-F]{64}(?![0-9a-fA-F])', raw)
        if len(set(addresses)) == 1: values.setdefault('address', addresses[0])
        if len(set(private)) == 1: values.setdefault('private_key', private[0])

    return PlatformCredentials(values)

def _find_credentials_file() -> Path | None:
    # 1. Configured via env var
    env_path = os.environ.get("TITAN_CREDENTIALS_FILE")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    
    # 2. Project root or config dir credentials.json
    root_dir = Path(__file__).resolve().parents[1]
    candidates = [
        root_dir / "credentials.json",
        root_dir / "config" / "credentials.json",
        Path.home() / ".titan" / "credentials.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None

def load_platform(platform: str) -> PlatformCredentials:
    """Load credentials for given platform (BINANCE, OKX, HYPERLIQUID, LIGHTER)."""
    p = platform.upper()
    values = {}

    # 1. Environment variables (highest priority, cloud & CI friendly)
    if p == 'BINANCE':
        k = os.environ.get('BINANCE_API_KEY')
        s = os.environ.get('BINANCE_API_SECRET') or os.environ.get('BINANCE_SECRET_KEY')
        if k: values['api_key'] = k
        if s: values['secret_key'] = s
    elif p == 'OKX':
        k = os.environ.get('OKX_API_KEY')
        s = os.environ.get('OKX_SECRET_KEY') or os.environ.get('OKX_API_SECRET')
        passphrase = os.environ.get('OKX_PASSPHRASE')
        if k: values['api_key'] = k
        if s: values['secret_key'] = s
        if passphrase: values['passphrase'] = passphrase
    elif p == 'HYPERLIQUID':
        addr = os.environ.get('HYPERLIQUID_ADDRESS')
        pk = os.environ.get('HYPERLIQUID_PRIVATE_KEY')
        if addr: values['address'] = addr
        if pk: values['private_key'] = pk
    elif p == 'LIGHTER':
        k = os.environ.get('LIGHTER_API_KEY')
        pk = os.environ.get('LIGHTER_PRIVATE_KEY')
        if k: values['api_key'] = k
        if pk: values['private_key'] = pk

    # 2. Local credentials.json file
    if not values:
        cfg_file = _find_credentials_file()
        if cfg_file:
            try:
                data = json.loads(cfg_file.read_text(encoding='utf-8'))
                if p in data and isinstance(data[p], dict):
                    values.update(data[p])
            except Exception:
                pass

    # 3. Custom local text directory (if TITAN_CREDENTIALS_DIR is set)
    custom_dir = os.environ.get("TITAN_CREDENTIALS_DIR")
    if not values and custom_dir and Path(custom_dir).exists():
        folder = Path(custom_dir)
        paths = {
            'BINANCE': folder / 'binance_api.txt',
            'OKX': folder / 'okx_api.txt',
            'LIGHTER': folder / 'lighter_api.txt',
            'HYPERLIQUID': folder / 'hyperliquid_api.txt'
        }
        if p in paths and paths[p].exists():
            try:
                raw = paths[p].read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:
                raw = paths[p].read_text(encoding='gbk')
            return parse_text(raw, p)

    return PlatformCredentials(values)
