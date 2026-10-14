"""快速验证：扫最近 N 块，确认 Base USDC 大额转账数据链路可用"""
import os
import sys
import time

_SM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SM_ROOT not in sys.path:
    sys.path.insert(0, _SM_ROOT)
from collector.base_collector import collect_usdc_transfers, get_latest_block

if __name__ == "__main__":
    t0 = time.time()
    latest = get_latest_block()
    print(f"Base latest block = {latest}")
    # 扫最近 300 块（约 10 分钟）
    transfers = collect_usdc_transfers(latest - 299, latest, min_amount=100_000)
    print(f"耗时 {time.time()-t0:.1f}s, 大额转账 {len(transfers)} 笔（>= $100K）")
    for it in transfers[:15]:
        ts = time.strftime("%m-%d %H:%M", time.localtime(it["timestamp"])) if it["timestamp"] else "?"
        print(f"  {ts}  {it['amount']:>12,.0f} USDC  {it['from'][:12]} -> {it['to'][:12]}  block={it['block']}")
