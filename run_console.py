import os
import sys
import time
import webbrowser
import hashlib
import json
from pathlib import Path
import uvicorn
from core.build_identity import build_identity

def probe_backend(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url+'/health',timeout=.5) as response:
            state=json.load(response)
            return state if isinstance(state,dict) else {}
    except Exception:return None

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 将当前目录与上级根目录加入 sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="OKX-Titan HFT Console")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Host to bind (e.g. 0.0.0.0 for VPS/server)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "57890")), help="Port to bind")
    parser.add_argument("--no-browser", action="store_true", help="Do not open local browser")
    parser.add_argument("--live", action="store_true", help="Unlock LIVE trading (exchange mutations enabled)")
    args, _ = parser.parse_known_args()
    if args.live:
        os.environ['ALLOW_LIVE_TRADING'] = '1'
    
    host = args.host
    port = args.port
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    expected_build=build_identity()
    existing=probe_backend(url)
    if existing is not None:
        if existing.get('build_id')!=expected_build:
            print('Existing backend differs from installed source. Close the old backend before relaunching.')
            return 1
        webbrowser.open(url)
        return 0
    print("=" * 80)
    print("  [Titan] 多平台交易控制台 · 只读验收版本 启动中...")
    print(f"  操盘控制台地址: {url}")
    print("  正在打开浏览器交互窗口...")
    print("=" * 80)

    # 延迟 1 秒在后台自动弹出浏览器
    def open_browser():
        import urllib.request
        expected_build=build_identity()
        for _ in range(80):
            try:
                with urllib.request.urlopen(url + '/health', timeout=.5) as response:
                    if response.status == 200:
                        state=json.load(response)
                        if state.get('build_id')!=expected_build:
                            print('端口上的后台版本与源码不一致。请先关闭旧控制台后台，再启动此版本；未打开旧页面。')
                            return
                        webbrowser.open(url)
                        return
            except Exception:
                time.sleep(.25)
        print('Backend did not become healthy; browser not opened. See terminal errors.')

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run("web_ui.server:app", host=host, port=port, log_level="warning")

if __name__ == "__main__":
    sys.exit(main())
