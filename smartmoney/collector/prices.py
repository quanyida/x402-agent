"""价格模块：CoinGecko 简单价格 API（免费、免 key），带缓存

用途：把各资产余额净变动折算成 USD，触发阈值信号。
缓存：data/prices_cache.json，默认 10 分钟有效，避免频繁调用。
"""
import json
import os
import sys
import time
import urllib.request

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from config import HTTP_HEADERS

DATA_DIR = os.path.join(_SM_ROOT, "data")
PRICES_CACHE = os.path.join(DATA_DIR, "prices_cache.json")
CACHE_TTL = 600  # 秒

CG_ENDPOINT = "https://api.coingecko.com/api/v3/simple/price"


def get_prices(cg_ids: list) -> dict:
    """返回 {cg_id: usd价格}。先查缓存，失效则请求 CoinGecko。"""
    ids = sorted(set(cg_ids))
    # 读缓存
    cache = {}
    if os.path.exists(PRICES_CACHE):
        try:
            with open(PRICES_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    now = time.time()
    need = [i for i in ids if i not in cache.get("prices", {}) or
            now - cache.get("ts", 0) > CACHE_TTL]

    if need:
        url = f"{CG_ENDPOINT}?ids={','.join(need)}&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": HTTP_HEADERS.get("User-Agent", "Mozilla/5.0")})
        data = None
        # 直连优先，失败回退本地代理（与 rpc.py 一致）
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e1:
            try:
                proxy = urllib.request.ProxyHandler({"http": "http://127.0.0.1:10809",
                                                     "https": "http://127.0.0.1:10809"})
                opener = urllib.request.build_opener(proxy)
                with opener.open(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode())
            except Exception as e2:
                print(f"  [warn] CoinGecko 请求失败(直连:{e1}, 代理:{e2})")
        if data:
            prices = {k: v.get("usd", 0) for k, v in data.items() if isinstance(v, dict)}
        else:
            prices = {}
        cache.setdefault("prices", {}).update(prices)
        cache["ts"] = now
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(PRICES_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return {i: cache["prices"].get(i, 0) for i in ids}


if __name__ == "__main__":
    print(get_prices(["ethereum", "usd-coin", "tether", "dai", "bitcoin", "solana"]))
