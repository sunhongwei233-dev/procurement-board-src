#!/usr/bin/env python3
"""采购协作表：密码登录、商品目录上行、云端改价/取消。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from merge import (
    EDITABLE,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    apply_catalog_push,
    calc,
    empty_catalog,
    empty_state,
    filter_sort,
    merge_row,
    tab_of,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATIC = HERE / "static"
CATALOG = DATA / "catalog.json"
STATE = DATA / "state.json"


def _load_dotenv() -> None:
    envp = HERE / ".env"
    if not envp.is_file():
        return
    for line in envp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
PASSWORD = os.environ.get("BOARD_PASSWORD") or "500wan$$$"
SECRET = (os.environ.get("BOARD_SECRET") or "procurement-board-20260913").encode("utf-8")
SYNC_TOKEN = os.environ.get("BOARD_SYNC_TOKEN") or os.environ.get("BOARD_SECRET") or "procurement-board-20260913"
COOKIE = "procurement_board_auth"
GITHUB_REPO = os.environ.get("GITHUB_REPO") or "sunhongwei233-dev/procurement-board"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
LOCK = threading.Lock()
_MEM: dict = {"key": None, "rows": None, "counts": None}

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def token() -> str:
    return hmac.new(SECRET, PASSWORD.encode("utf-8"), hashlib.sha256).hexdigest()


def ok(request: Request) -> bool:
    return request.cookies.get(COOKIE) == token()


def sync_ok(request: Request, body: dict | None = None) -> bool:
    hdr = (request.headers.get("x-board-sync") or "").strip()
    if hdr and hmac.compare_digest(hdr, SYNC_TOKEN):
        return True
    if body and hmac.compare_digest(str(body.get("token") or ""), SYNC_TOKEN):
        return True
    return False


def load_catalog() -> dict:
    if not CATALOG.is_file():
        return empty_catalog()
    try:
        blob = json.loads(CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_catalog()
    if isinstance(blob, list):
        return {"generated_at": "", "source": "ebay-ops", "n": len(blob), "items": blob}
    return blob if isinstance(blob, dict) else empty_catalog()


def load_state() -> dict:
    if not STATE.is_file():
        return empty_state()
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_state()


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _invalidate_mem() -> None:
    _MEM["key"] = None
    _MEM["rows"] = None
    _MEM["counts"] = None


def save_catalog(blob: dict) -> None:
    _write_json(CATALOG, blob)
    _invalidate_mem()
    snapshot = json.loads(json.dumps(blob))
    threading.Thread(target=_github_push_file, args=("data/catalog.json", snapshot, "archive catalog"), daemon=True).start()


def save_state(st: dict) -> None:
    _write_json(STATE, st)
    _invalidate_mem()
    snapshot = json.loads(json.dumps(st))
    threading.Thread(target=_github_push_file, args=("data/state.json", snapshot, f"archive board state rev={st.get('rev')}"), daemon=True).start()


def _github_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "procurement-board",
    }


def _github_get_file(path: str) -> tuple[dict | list | None, str | None]:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None, None
    import base64
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None, None
    raw = base64.b64decode(meta.get("content") or "").decode("utf-8")
    try:
        return json.loads(raw), meta.get("sha")
    except json.JSONDecodeError:
        return None, meta.get("sha")


def _github_push_file(path: str, obj: dict, message: str) -> None:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    import base64
    import urllib.error
    import urllib.request

    _, sha = _github_get_file(path)
    body = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii"),
    }
    if sha:
        body["sha"] = sha
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError:
        return


def pull_github_on_boot() -> None:
    remote_state, _ = _github_get_file("data/state.json")
    if isinstance(remote_state, dict):
        local = load_state()
        if int(remote_state.get("rev") or 0) >= int(local.get("rev") or 0):
            _write_json(STATE, remote_state)
    remote_cat, _ = _github_get_file("data/catalog.json")
    if isinstance(remote_cat, dict) and (remote_cat.get("items") or remote_cat.get("n")):
        local_cat = load_catalog()
        if int(remote_cat.get("n") or 0) >= int(local_cat.get("n") or 0):
            _write_json(CATALOG, remote_cat)


def all_merged() -> tuple[list[dict], dict]:
    catalog = load_catalog().get("items") or []
    st = load_state()
    overs = st.get("rows") or {}
    rows = [merge_row(x, overs.get(x.get("handle") or x.get("item_id"))) for x in catalog]
    return rows, st


def _mem_key(st: dict) -> tuple:
    return (
        int(st.get("rev") or 0),
        CATALOG.stat().st_mtime if CATALOG.is_file() else 0,
        STATE.stat().st_mtime if STATE.is_file() else 0,
    )


def cached_board() -> tuple[list[dict], dict, dict]:
    st = load_state()
    key = _mem_key(st)
    if _MEM["key"] == key and _MEM["rows"] is not None:
        return _MEM["rows"], st, _MEM["counts"] or counts(_MEM["rows"])
    rows, st = all_merged()
    c = counts(rows)
    _MEM["key"] = key
    _MEM["rows"] = rows
    _MEM["counts"] = c
    return rows, st, c


def counts(rows: list[dict]) -> dict[str, int]:
    out = {"active": 0, "bought": 0, "cancelled": 0}
    for r in rows:
        out[tab_of(r)] = out.get(tab_of(r), 0) + 1
    return out


@app.get("/")
def home(request: Request):
    if not ok(request):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if (body.get("password") or "") != PASSWORD:
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE,
        token(),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.on_event("startup")
def _boot() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not CATALOG.is_file():
        _write_json(CATALOG, empty_catalog())
    if not STATE.is_file():
        _write_json(STATE, empty_state())
    pull_github_on_boot()


@app.get("/api/meta")
def meta(request: Request):
    if not ok(request):
        return JSONResponse({"ok": False}, status_code=401)
    with LOCK:
        rows, st, c = cached_board()
    return {
        "ok": True,
        "rev": st.get("rev") or 0,
        "updated_at": st.get("updated_at") or 0,
        "total": len(rows),
        **c,
    }


@app.get("/api/rows")
def rows(request: Request):
    if not ok(request):
        return JSONResponse({"ok": False}, status_code=401)
    q = dict(request.query_params)
    page = max(1, int(q.get("page") or 1))
    size = min(5000, max(1, int(q.get("page_size") or 20)))
    with LOCK:
        merged, st, c = cached_board()
        filtered = filter_sort(merged, q)
    start = (page - 1) * size
    chunk = filtered[start : start + size]
    return {
        "ok": True,
        "rev": st.get("rev") or 0,
        "page": page,
        "page_size": size,
        "total": len(filtered),
        "pages": max(1, (len(filtered) + size - 1) // size),
        "items": chunk,
        **c,
    }


@app.post("/api/row")
async def update_row(request: Request):
    if not ok(request):
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    handle = str(body.get("handle") or body.get("item_id") or "").strip()
    if not handle:
        return JSONResponse({"ok": False, "error": "missing handle"}, status_code=400)
    with LOCK:
        catalog = {str(x.get("handle") or x.get("item_id")): x for x in (load_catalog().get("items") or [])}
        if handle not in catalog:
            return JSONResponse({"ok": False}, status_code=404)
        st = load_state()
        rows_map = st.setdefault("rows", {})
        cur = dict(rows_map.get(handle) or {})
        for k in EDITABLE:
            if k in body:
                cur[k] = body[k]
        if body.get("status") == STATUS_CANCELLED and not cur.get("cancelled_at"):
            cur["cancelled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if body.get("cancel_reason") is not None:
                cur["cancel_reason"] = body.get("cancel_reason")
        if body.get("status") == STATUS_ACTIVE:
            cur["cancelled_at"] = ""
        merged = merge_row(catalog[handle], cur)
        if body.get("recalc") or any(
            k in body
            for k in (
                "sell_usd",
                "cogs_cny",
                "first_leg_usd",
                "last_mile_usd",
                "return_cost_usd",
                "length_cm",
                "width_cm",
                "height_cm",
                "package_weight_kg",
            )
        ):
            merged = calc(merged)
            for k in (
                "sell_usd",
                "cogs_cny",
                "first_leg_usd",
                "last_mile_usd",
                "return_cost_usd",
                "transaction_fee_usd",
                "ad_fee_usd",
                "profit_usd",
                "margin_pct",
                "verdict",
            ):
                cur[k] = merged.get(k)
        rows_map[handle] = cur
        st["rev"] = int(st.get("rev") or 0) + 1
        st["updated_at"] = time.time()
        save_state(st)
        out = merge_row(catalog[handle], cur)
    return {"ok": True, "rev": st["rev"], "item": out}


@app.post("/api/catalog/push")
async def catalog_push(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not sync_ok(request, body):
        return JSONResponse({"ok": False, "error": "同步密钥无效"}, status_code=401)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"ok": False, "error": "缺少 items"}, status_code=400)
    with LOCK:
        blob, st, stats = apply_catalog_push(load_catalog(), load_state(), items)
        blob["generated_at"] = body.get("generated_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
        blob["source"] = body.get("source") or "ebay-ops"
        save_catalog(blob)
        save_state(st)
    return {"ok": True, **stats}


@app.get("/health")
def health():
    return {"ok": True, "github_archive": bool(GITHUB_TOKEN)}
