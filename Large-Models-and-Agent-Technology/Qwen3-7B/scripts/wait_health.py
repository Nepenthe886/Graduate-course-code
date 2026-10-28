"""等待 http://host:port/health 就绪（供 run_server.cmd 调用）。

用法： python wait_health.py 127.0.0.1 8012 180
成功时退出码 0，超时退出码 1。
"""
from __future__ import annotations

import sys
import time
import urllib.request


def main(argv: list[str]) -> int:
    host = argv[1] if len(argv) > 1 else "127.0.0.1"
    port = int(argv[2]) if len(argv) > 2 else 8012
    timeout = float(argv[3]) if len(argv) > 3 else 120.0
    url = f"http://{host}:{port}/health"
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                body = response.read(200).decode("utf-8", "replace")
                if response.status == 200:
                    print(f"[OK] {url} 就绪（{time.time() - started:.1f}s）")
                    return 0
                last = f"status={response.status}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:80]
        time.sleep(1.0)
    print(f"[FAIL] {url} 等待 {timeout:.0f}s 未就绪：{last}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
