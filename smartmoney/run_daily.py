"""一键调度：跑全部采集器 → 生成简报

用法：
    python run_daily.py                # 跑三条链 + 生成简报
    python run_daily.py --brief-only   # 只重新生成简报（复用最近信号）
"""
import os
import subprocess
import sys

_SM_ROOT = os.path.dirname(os.path.abspath(__file__))
COLLECTORS = ["base_collector.py", "eth_collector.py", "sol_collector.py"]


def main():
    brief_only = "--brief-only" in sys.argv
    if not brief_only:
        for name in COLLECTORS:
            path = os.path.join(_SM_ROOT, "collector", name)
            print(f"\n=== 运行 {name} ===")
            subprocess.run([sys.executable, path], cwd=_SM_ROOT)

    brief_path = os.path.join(_SM_ROOT, "briefing", "generate_brief.py")
    print(f"\n=== 生成简报 ===")
    subprocess.run([sys.executable, brief_path], cwd=_SM_ROOT)


if __name__ == "__main__":
    main()
