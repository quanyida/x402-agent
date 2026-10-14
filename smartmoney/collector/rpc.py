"""通用 JSON-RPC 客户端：直连公共 RPC，失败自动回退代理"""
import json
import os
import sys
import urllib.request

# 确保 smartmoney 根目录在 sys.path（支持直接运行与模块运行两种方式）
_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from config import HTTP_HEADERS


class RpcError(Exception):
    pass


def rpc_call(rpc_url: str, method: str, params: list, timeout: int = 20,
             force_proxy: bool = False):
    """调用 JSON-RPC。force_proxy=True 时直接走本地代理（如 Solana 官方 RPC）；
    否则直连优先，失败回退本地代理。"""
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()

    def _do(url, use_proxy=False):
        req = urllib.request.Request(url, data=body, headers=HTTP_HEADERS)
        if use_proxy:
            proxy = urllib.request.ProxyHandler({
                "http": "http://127.0.0.1:10809",
                "https": "http://127.0.0.1:10809",
            })
            opener = urllib.request.build_opener(proxy)
            with opener.open(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
        else:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
        if "error" in result:
            raise RpcError(f"{method} error: {result['error']}")
        return result.get("result")

    if force_proxy:
        try:
            return _do(rpc_url, use_proxy=True)
        except Exception as e:
            raise RpcError(f"proxy({e})") from e

    # 尝试直连
    try:
        return _do(rpc_url, use_proxy=False)
    except Exception as direct_err:
        # 回退本地代理（仅当直连失败，如超时/被断连）
        try:
            return _do(rpc_url, use_proxy=True)
        except Exception as proxy_err:
            raise RpcError(f"direct({direct_err}) proxy({proxy_err})") from direct_err
