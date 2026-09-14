#!/usr/bin/env python3
"""采购协作表 catalog/state 合并：再同步不覆盖云端可编辑字段。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOARD))

from merge import apply_catalog_push, calc, empty_catalog, empty_state, tab_of  # noqa: E402


class TestMerge(unittest.TestCase):
    def test_first_sync_seeds_prices(self):
        incoming = [
            {
                "item_id": "111",
                "title": "Switch A",
                "source_list_price": 20,
                "seed": {"sell_usd": 20, "cogs_cny": 40, "po_progress": "议价中"},
            }
        ]
        blob, st, stats = apply_catalog_push(empty_catalog(), empty_state(), incoming)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(blob["items"][0]["title"], "Switch A")
        row = st["rows"]["111"]
        self.assertEqual(row["sell_usd"], 20)
        self.assertEqual(row["cogs_cny"], 40)
        self.assertEqual(row["status"], "active")
        self.assertIsNotNone(row.get("profit_usd"))

    def test_resync_does_not_overwrite_prices_or_cancel(self):
        first = [
            {
                "item_id": "111",
                "title": "Old",
                "source_list_price": 20,
                "image_url": "http://img/old.jpg",
                "seed": {"sell_usd": 20, "cogs_cny": 40},
            }
        ]
        blob, st, _ = apply_catalog_push(empty_catalog(), empty_state(), first)
        st["rows"]["111"]["sell_usd"] = 33.5
        st["rows"]["111"]["cogs_cny"] = 18
        st["rows"]["111"]["note"] = "同事改的"
        st["rows"]["111"]["status"] = "cancelled"
        again = [
            {
                "item_id": "111",
                "title": "New title",
                "source_list_price": 99,
                "image_url": "http://img/new.jpg",
                "seed": {"sell_usd": 99, "cogs_cny": 1, "note": "本机备注"},
            }
        ]
        blob2, st2, stats = apply_catalog_push(blob, st, again)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(blob2["items"][0]["title"], "New title")
        self.assertEqual(blob2["items"][0]["image_url"], "http://img/new.jpg")
        self.assertEqual(blob2["items"][0]["source_list_price"], 99)
        row = st2["rows"]["111"]
        self.assertEqual(row["sell_usd"], 33.5)
        self.assertEqual(row["cogs_cny"], 18)
        self.assertEqual(row["note"], "同事改的")
        self.assertEqual(row["status"], "cancelled")
        merged = dict(blob2["items"][0])
        merged.update(row)
        self.assertEqual(tab_of(merged), "cancelled")

    def test_cancel_stays_out_of_active(self):
        incoming = [{"item_id": "222", "title": "B", "seed": {"sell_usd": 10, "cogs_cny": 20}}]
        blob, st, _ = apply_catalog_push(empty_catalog(), empty_state(), incoming)
        st["rows"]["222"]["status"] = "cancelled"
        merged = {**blob["items"][0], **st["rows"]["222"]}
        self.assertEqual(tab_of(merged), "cancelled")

    def test_calc_uses_cloud_costs(self):
        r = calc(
            {
                "sell_usd": 40,
                "cogs_cny": 72,
                "first_leg_usd": 2.85,
                "last_mile_usd": 4.8,
                "return_cost_usd": 0,
            }
        )
        self.assertIsNotNone(r["profit_usd"])
        self.assertEqual(r["cogs_cny"], 72)

    def test_filter_sort_tab_all(self):
        from merge import filter_sort

        rows = [
            {"handle": "1", "title": "A", "status": "active", "po_progress": "议价中", "profit_usd": 1},
            {"handle": "2", "title": "B", "status": "cancelled", "po_progress": "议价中", "profit_usd": 2},
        ]
        all_rows = filter_sort(rows, {"tab": "all"})
        self.assertEqual(len(all_rows), 2)
        active = filter_sort(rows, {"tab": "active"})
        self.assertEqual([r["handle"] for r in active], ["1"])


if __name__ == "__main__":
    unittest.main()
