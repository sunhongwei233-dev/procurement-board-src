# -*- coding: utf-8 -*-
"""catalog.json（商品信息）与 state.json（云端可编辑）合并规则。"""
from __future__ import annotations

import time
from typing import Any

FX = 7.2
DEFAULT_FIRST_LEG = 2.85  # 海运头程 2.5 + 入库约 0.35
DEFAULT_LAST_MILE = 4.8

CATALOG_FIELDS = (
    "handle",
    "item_id",
    "title",
    "title_zh",
    "image_url",
    "ebay_url",
    "store_id",
    "store_name",
    "sold_last_7d",
    "sold_last_30d",
    "sold_last_90d",
    "sold_count_total",
    "listed_since",
    "source_list_price",
    "source_cogs_cny",
    "cogs_offer_url",
)

EDITABLE = (
    "sell_usd",
    "cogs_cny",
    "note",
    "po_notes",
    "po_shop_name",
    "po_shop_url",
    "po_negotiator",
    "po_progress",
    "status",
    "first_leg_usd",
    "last_mile_usd",
    "return_cost_usd",
    "length_cm",
    "width_cm",
    "height_cm",
    "package_weight_kg",
    "wh_zone",
    "profit_usd",
    "margin_pct",
    "verdict",
    "transaction_fee_usd",
    "ad_fee_usd",
    "cancelled_at",
    "cancel_reason",
)

SEED_FIELDS = (
    "sell_usd",
    "cogs_cny",
    "note",
    "po_notes",
    "po_shop_name",
    "po_shop_url",
    "po_negotiator",
    "po_progress",
    "first_leg_usd",
    "last_mile_usd",
    "return_cost_usd",
    "length_cm",
    "width_cm",
    "height_cm",
    "package_weight_kg",
    "wh_zone",
)

PROGRESS_BOUGHT = "已采购"
PROGRESS_NEGOTIATING = "议价中"
STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"


def empty_state() -> dict:
    return {"rev": 0, "updated_at": 0, "rows": {}}


def empty_catalog() -> dict:
    return {"generated_at": "", "source": "ebay-ops", "n": 0, "items": []}


def catalog_item_from_incoming(raw: dict) -> dict | None:
    item_id = str(raw.get("item_id") or raw.get("handle") or "").strip()
    if not item_id:
        return None
    out = {"handle": item_id, "item_id": item_id}
    for k in CATALOG_FIELDS:
        if k in ("handle", "item_id"):
            continue
        if k in raw:
            out[k] = raw[k]
    return out


def merge_row(base: dict, over: dict | None) -> dict:
    r = dict(base)
    if over:
        for k in EDITABLE:
            if k in over and over[k] is not None:
                r[k] = over[k]
    if not (r.get("po_progress") or "").strip():
        r["po_progress"] = PROGRESS_NEGOTIATING
    if not (r.get("status") or "").strip():
        r["status"] = STATUS_ACTIVE
    return r


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def calc(r: dict) -> dict:
    sell = _num(r.get("sell_usd"))
    cogs = _num(r.get("cogs_cny"))
    r["sell_usd"] = sell
    r["cogs_cny"] = cogs
    if not sell or sell <= 0:
        r["transaction_fee_usd"] = r["ad_fee_usd"] = None
        r["profit_usd"] = r["margin_pct"] = None
        r["verdict"] = "缺采购价" if cogs is None else "缺售价"
        return r
    if cogs is None:
        r["transaction_fee_usd"] = r["ad_fee_usd"] = None
        r["profit_usd"] = r["margin_pct"] = None
        r["verdict"] = "缺采购价"
        return r
    fl = _num(r.get("first_leg_usd"))
    if fl is None:
        fl = DEFAULT_FIRST_LEG
    lm = _num(r.get("last_mile_usd"))
    if lm is None:
        lm = DEFAULT_LAST_MILE
    fee_base = sell * 1.075
    closing = fee_base * 0.115
    intl = fee_base * 0.015 + 0.40
    ad = fee_base * 0.10
    ret = _num(r.get("return_cost_usd"))
    if ret is None:
        ret = 0.065 * lm
    profit = sell - cogs / FX - fl - lm - closing - intl - ad - ret
    r["first_leg_usd"] = round(fl, 2)
    r["last_mile_usd"] = round(lm, 2)
    r["return_cost_usd"] = round(ret, 2)
    r["transaction_fee_usd"] = round(closing + intl, 2)
    r["ad_fee_usd"] = round(ad, 2)
    r["profit_usd"] = round(profit, 2)
    r["margin_pct"] = round(profit / sell * 100, 1)
    m = r["margin_pct"]
    r["verdict"] = "优秀" if m >= 40 else "可做" if m >= 25 else "偏紧" if m >= 15 else "不建议"
    return r


def _seed_state_row(incoming: dict) -> dict:
    seed = incoming.get("seed") if isinstance(incoming.get("seed"), dict) else {}
    cur: dict[str, Any] = {k: seed[k] for k in SEED_FIELDS if k in seed and seed[k] is not None}
    if "status" not in cur or not cur.get("status"):
        cur["status"] = STATUS_ACTIVE
    if not (cur.get("po_progress") or "").strip():
        cur["po_progress"] = PROGRESS_NEGOTIATING
    merged = merge_row({"handle": incoming.get("item_id")}, cur)
    return calc(merged)


def apply_catalog_push(
    catalog_blob: dict | None,
    state: dict | None,
    incoming_items: list[dict],
) -> tuple[dict, dict, dict]:
    """Upsert 商品信息. 已有 SKU 不覆盖 state 可编辑字段（含取消状态）。"""
    blob = dict(catalog_blob or empty_catalog())
    items = list(blob.get("items") or [])
    by_handle: dict[str, dict] = {}
    order: list[str] = []
    for it in items:
        h = str(it.get("handle") or it.get("item_id") or "").strip()
        if not h:
            continue
        if h not in by_handle:
            order.append(h)
        by_handle[h] = dict(it)
        by_handle[h]["handle"] = h
        by_handle[h]["item_id"] = h

    st = dict(state or empty_state())
    rows_map: dict[str, dict] = dict(st.get("rows") or {})
    created = updated = failed = 0
    errors: list[str] = []

    for raw in incoming_items or []:
        cat = catalog_item_from_incoming(raw or {})
        if not cat:
            failed += 1
            errors.append("缺少 item_id")
            continue
        h = cat["handle"]
        if h in by_handle:
            by_handle[h].update(cat)
            updated += 1
        else:
            by_handle[h] = cat
            order.append(h)
            created += 1
        if h not in rows_map:
            rows_map[h] = _seed_state_row(raw)

    blob["items"] = [by_handle[h] for h in order if h in by_handle]
    blob["n"] = len(blob["items"])
    st["rows"] = rows_map
    st["rev"] = int(st.get("rev") or 0) + 1
    st["updated_at"] = time.time()
    return blob, st, {
        "ok": True,
        "created": created,
        "updated": updated,
        "failed": failed,
        "errors": errors[:8],
        "total": len(blob["items"]),
    }


def tab_of(row: dict) -> str:
    if (row.get("status") or "") == STATUS_CANCELLED:
        return "cancelled"
    if (row.get("po_progress") or "") == PROGRESS_BOUGHT:
        return "bought"
    return "active"


def filter_sort(rows: list[dict], q: dict) -> list[dict]:
    tab = q.get("tab") or "active"
    kw = (q.get("q") or "").strip().lower()
    out = []
    for r in rows:
        if tab != "all" and tab_of(r) != tab:
            continue
        blob = " ".join(
            str(r.get(k) or "")
            for k in (
                "title",
                "title_zh",
                "item_id",
                "store_name",
                "note",
                "po_notes",
                "po_shop_name",
                "po_negotiator",
                "listed_since",
            )
        ).lower()
        if kw and kw not in blob:
            continue
        out.append(r)

    def key(r):
        return (r.get("profit_usd") is None, -(r.get("profit_usd") or 0))

    out.sort(key=key)
    return out
