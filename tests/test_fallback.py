"""Offline tests for the second-source adapters."""

from __future__ import annotations

import unittest

from app.providers.fallback import FallbackProvider, SinaSnapshotProvider, _tencent_symbol


class FallbackProviderTests(unittest.TestCase):
    def test_symbol_mapping_respects_market(self) -> None:
        self.assertEqual(_tencent_symbol("1.600000"), "sh600000")
        self.assertEqual(_tencent_symbol("0.000001"), "sz000001")
        self.assertEqual(_tencent_symbol("1.000300"), "sh000300")
        self.assertEqual(_tencent_symbol("0.920000"), "bj920000")

    def test_sina_normalize_keeps_degraded_marker(self) -> None:
        provider = SinaSnapshotProvider()
        row = provider._normalize(
            {
                "symbol": "sz000001",
                "code": "000001",
                "name": "平安银行",
                "trade": "12.07",
                "changepercent": "-1.07",
                "settlement": "12.20",
                "open": "12.20",
                "high": "12.26",
                "low": "12.05",
                "volume": "79506321",
                "amount": "965058466",
                "ticktime": "11:11:09",
                "turnoverratio": "0.41",
                "per": "5.83",
                "pb": "0.49",
                "mktcap": "23422943",
                "nmc": "23422661",
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["code"], "000001")
        self.assertEqual(row["industry"], "未分类")
        self.assertTrue(row["degraded"])
        self.assertEqual(row["source"], "sina")

    def test_fallback_uses_sina_when_primary_fails(self) -> None:
        class BrokenPrimary:
            last_snapshot_meta = {}
            last_limit_up_total = 0

            def fetch_snapshot(self, *args, **kwargs):
                raise RuntimeError("primary down")

        class FakeSina:
            last_snapshot_meta = {"coverage": 1.0, "source": "sina"}

            def fetch_snapshot(self, *args, **kwargs):
                return [{"code": "000001"}]

        provider = FallbackProvider()
        provider.primary = BrokenPrimary()
        provider.sina = FakeSina()
        rows = provider.fetch_snapshot()
        self.assertEqual(rows[0]["code"], "000001")
        self.assertEqual(provider.current_source, "新浪财经（降级）")
        self.assertTrue(provider.last_snapshot_meta["fallback"])


if __name__ == "__main__":
    unittest.main()
