"""
OKX Credentials Loader for Titan HFT Cockpit.
Priority:
  1. Environment Variables: OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE
  2. config/credentials.json or credentials.json
  3. Safe fallback for simulation/read-only testing
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger("TITAN_AUTH")

class CredentialsLoader:
    """OKX Credential Loader with multi-source fallback."""

    @staticmethod
    def load() -> Dict[str, str]:
        # 1. Environment variables
        api_key = os.environ.get("OKX_API_KEY", "")
        secret_key = os.environ.get("OKX_SECRET_KEY", "") or os.environ.get("OKX_API_SECRET", "")
        passphrase = os.environ.get("OKX_PASSPHRASE", "")

        if api_key and secret_key and passphrase:
            return {
                "apiKey": api_key,
                "secretKey": secret_key,
                "passphrase": passphrase
            }

        # 2. JSON credentials file
        root_dir = Path(__file__).resolve().parents[1]
        for candidate in [root_dir / "credentials.json", root_dir / "config" / "credentials.json", Path.home() / ".titan" / "credentials.json"]:
            if candidate.exists():
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    okx_cfg = data.get("OKX", {})
                    if isinstance(okx_cfg, dict) and okx_cfg.get("api_key"):
                        return {
                            "apiKey": okx_cfg.get("api_key", ""),
                            "secretKey": okx_cfg.get("secret_key", ""),
                            "passphrase": okx_cfg.get("passphrase", "")
                        }
                except Exception:
                    pass

        # 3. Check custom path if provided
        custom_file = os.environ.get("OKX_CREDENTIALS_FILE")
        if custom_file and Path(custom_file).exists():
            file_path = Path(custom_file)
            raw_text = file_path.read_text(encoding="utf-8-sig").strip()
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            try:
                data = json.loads(raw_text)
                return {
                    "apiKey": data.get("apiKey", ""),
                    "secretKey": data.get("secretKey", ""),
                    "passphrase": data.get("passphrase", "")
                }
            except Exception:
                for line in lines:
                    if ":" in line or "=" in line:
                        delim = ":" if ":" in line else "="
                        k, v = line.split(delim, 1)
                        k_clean = k.strip().lower()
                        v_clean = v.strip()
                        if "api" in k_clean:
                            api_key = v_clean
                        elif "secret" in k_clean:
                            secret_key = v_clean
                        elif "pass" in k_clean or "口令" in k_clean or "密码" in k_clean:
                            passphrase = v_clean
                if api_key and secret_key:
                    return {
                        "apiKey": api_key,
                        "secretKey": secret_key,
                        "passphrase": passphrase
                    }

        # Safe fallback for simulation
        logger.warning("No live OKX credentials found. Running in simulation/paper mode.")
        return {
            "apiKey": "",
            "secretKey": "",
            "passphrase": ""
        }
