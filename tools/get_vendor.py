"""Download a local ECharts build for offline charts."""

from __future__ import annotations

from pathlib import Path

import requests

URLS = [
    "https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.1/echarts.min.js",
    "https://unpkg.com/echarts@5.5.1/dist/echarts.min.js",
    "https://registry.npmmirror.com/echarts/5.5.1/files/dist/echarts.min.js",
    "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.1/echarts.min.js",
]
TARGET = Path(__file__).resolve().parents[1] / "web" / "vendor" / "echarts.min.js"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    for url in URLS:
        try:
            response = requests.get(
                url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}
            )
            print(url, response.status_code, len(response.content), response.headers.get("content-type"))
            if response.status_code == 200 and len(response.content) > 100_000:
                TARGET.write_bytes(response.content)
                print("saved", TARGET, TARGET.stat().st_size)
                return 0
        except Exception as exc:  # noqa: BLE001
            print(url, "ERR", exc)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
