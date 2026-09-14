(function () {
  const $ = (id) => document.getElementById(id);
  let tab = "active";
  let page = 1;
  let rev = 0;
  let pages = 1;
  let allItems = [];
  let loading = false;
  const tbody = $("tbody");

  function money(v, pfx) {
    if (v == null || v === "") return "—";
    return (pfx || "") + Number(v).toFixed(2);
  }
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function tabOf(r) {
    if ((r.status || "") === "cancelled") return "cancelled";
    if ((r.po_progress || "") === "已采购") return "bought";
    return "active";
  }
  function searchBlob(r) {
    return [
      r.title,
      r.title_zh,
      r.item_id,
      r.store_name,
      r.note,
      r.po_notes,
      r.po_shop_name,
      r.po_negotiator,
      r.listed_since,
    ]
      .map((x) => String(x || ""))
      .join(" ")
      .toLowerCase();
  }
  function pageSize() {
    return Number($("page-size").value || 20);
  }

  function applyView() {
    const kw = ($("q").value || "").trim().toLowerCase();
    let counts = { active: 0, bought: 0, cancelled: 0 };
    allItems.forEach((r) => {
      counts[tabOf(r)] = (counts[tabOf(r)] || 0) + 1;
    });
    $("n-active").textContent = String(counts.active || 0);
    $("n-bought").textContent = String(counts.bought || 0);
    $("n-cancelled").textContent = String(counts.cancelled || 0);

    let rows = allItems.filter((r) => tabOf(r) === tab);
    if (kw) rows = rows.filter((r) => searchBlob(r).includes(kw));
    const size = pageSize();
    pages = Math.max(1, Math.ceil(rows.length / size) || 1);
    if (page > pages) page = pages;
    if (page < 1) page = 1;
    const start = (page - 1) * size;
    const chunk = rows.slice(start, start + size);
    $("shown").textContent = String(chunk.length);
    $("filtered").textContent = String(rows.length);
    $("pageinfo").textContent = page + " / " + pages;
    $("prev").disabled = page <= 1;
    $("next").disabled = page >= pages;
    render(chunk, start);
  }

  async function fetchAllItems() {
    const items = [];
    let pageNo = 1;
    let totalPages = 1;
    let lastRev = rev;
    do {
      const p = new URLSearchParams();
      p.set("tab", "all");
      p.set("page", String(pageNo));
      p.set("page_size", "5000");
      const r = await fetch("/api/rows?" + p.toString());
      if (r.status === 401) {
        location.href = "/login";
        return null;
      }
      const d = await r.json();
      lastRev = d.rev || 0;
      items.push.apply(items, d.items || []);
      totalPages = d.pages || 1;
      pageNo += 1;
    } while (pageNo <= totalPages && pageNo <= 20);
    rev = lastRev;
    return items;
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    $("sync").textContent = "加载中…";
    try {
      const items = await fetchAllItems();
      if (!items) return;
      allItems = items;
      applyView();
      $("sync").textContent = "已同步";
    } catch (e) {
      $("sync").textContent = "加载失败";
    } finally {
      loading = false;
    }
  }

  function render(items, offset) {
    const frag = document.createDocumentFragment();
    items.forEach((r, i) => {
      const tr = el("tr");
      tr.dataset.handle = r.handle;
      if (r.status === "cancelled") tr.classList.add("st-cancelled");
      else if (r.po_progress === "已采购") tr.classList.add("st-bought");

      tr.appendChild(el("td", "", String((offset || 0) + i + 1)));

      const tdImg = el("td");
      if (r.image_url) {
        const img = el("img", "thumb");
        img.src = r.image_url;
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        tdImg.appendChild(img);
      } else tdImg.textContent = "—";
      tr.appendChild(tdImg);

      const tdTitle = el("td", "title-cell");
      const a = el("a", "title-link");
      a.href = r.ebay_url || "https://www.ebay.com/itm/" + r.item_id;
      a.target = "_blank";
      a.rel = "noopener";
      const titleText = r.title || r.item_id || "";
      a.textContent = titleText;
      a.title = titleText;
      tdTitle.appendChild(a);
      tdTitle.appendChild(
        el("div", "hint", r.item_id + (r.store_name ? " · " + r.store_name : ""))
      );
      tr.appendChild(tdTitle);

      tr.appendChild(el("td", "", r.sold_count_total == null ? "—" : String(r.sold_count_total)));
      tr.appendChild(el("td", "hint", r.listed_since || "—"));
      tr.appendChild(el("td", "", r.verdict || "—"));
      tr.appendChild(el("td", "num-strong", r.margin_pct == null ? "—" : r.margin_pct + "%"));
      tr.appendChild(el("td", "num-strong", money(r.profit_usd, "$")));
      tr.appendChild(tdInput("sell_usd", r.sell_usd, "$"));
      tr.appendChild(el("td", "hint", money(r.source_list_price, "$")));
      tr.appendChild(tdInput("cogs_cny", r.cogs_cny, "¥"));
      tr.appendChild(tdInput("first_leg_usd", r.first_leg_usd, "$"));
      tr.appendChild(tdInput("last_mile_usd", r.last_mile_usd, "$"));
      tr.appendChild(tdInput("return_cost_usd", r.return_cost_usd, "$"));
      tr.appendChild(tdInput("package_weight_kg", r.package_weight_kg, "", "sm"));

      const tdDim = el("td");
      ["length_cm", "width_cm", "height_cm"].forEach((f, di) => {
        if (di) tdDim.appendChild(document.createTextNode("×"));
        const inp = el("input");
        inp.type = "number";
        inp.step = "0.1";
        inp.className = "px sm";
        inp.dataset.field = f;
        if (r[f] != null) inp.value = String(r[f]);
        tdDim.appendChild(inp);
      });
      tr.appendChild(tdDim);

      const tdProg = el("td");
      const sel = el("select");
      ["议价中", "已采购"].forEach((p) => {
        const o = el("option", "", p);
        o.value = p;
        if ((r.po_progress || "议价中") === p) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", () => save(r.handle, { po_progress: sel.value }));
      tdProg.appendChild(sel);
      tr.appendChild(tdProg);

      tr.appendChild(tdText("po_negotiator", r.po_negotiator, 70));
      tr.appendChild(tdNote("note", r.note, r.handle));
      tr.appendChild(tdNote("po_notes", r.po_notes, r.handle));

      const tdOp = el("td", "ops");
      tdOp.appendChild(btn("确定", "btn ok", () => confirmPrice(tr, r.handle)));
      if (r.status === "cancelled") {
        tdOp.appendChild(btn("重新采购", "btn pick", () => save(r.handle, { status: "active" })));
      } else {
        tdOp.appendChild(
          btn("取消采购", "btn drop", () => {
            if (!window.confirm("确认取消采购？将进入「取消采购」列表，备注和价格会保留。")) return;
            save(r.handle, { status: "cancelled" });
          })
        );
      }
      tr.appendChild(tdOp);
      frag.appendChild(tr);
    });
    tbody.replaceChildren(frag);
  }

  function tdInput(field, value, pfx, extraCls) {
    const td = el("td");
    const wrap = el("label", "edit");
    if (pfx) wrap.appendChild(el("span", "pfx", pfx));
    const inp = el("input");
    inp.type = "number";
    inp.step = "0.01";
    inp.className = "px" + (extraCls ? " " + extraCls : "");
    inp.dataset.field = field;
    if (value != null) inp.value = String(value);
    wrap.appendChild(inp);
    td.appendChild(wrap);
    return td;
  }
  function tdText(field, value, width) {
    const td = el("td");
    const inp = el("input");
    inp.type = "text";
    inp.className = "px";
    inp.style.width = (width || 80) + "px";
    inp.dataset.field = field;
    inp.value = value || "";
    let t;
    inp.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const handle = inp.closest("tr").dataset.handle;
        const body = {};
        body[field] = inp.value;
        save(handle, body);
      }, 400);
    });
    td.appendChild(inp);
    return td;
  }
  function tdNote(field, value, handle) {
    const td = el("td");
    const note = el("textarea");
    note.className = "note";
    note.rows = 2;
    note.value = value || "";
    let t;
    note.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const body = {};
        body[field] = note.value;
        save(handle, body);
      }, 400);
    });
    td.appendChild(note);
    return td;
  }
  function btn(label, cls, fn) {
    const b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }
  function num(v) {
    if (v === "" || v == null) return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function patchLocal(handle, item) {
    const i = allItems.findIndex((x) => String(x.handle) === String(handle));
    if (i >= 0) allItems[i] = item;
    else allItems.push(item);
  }

  async function save(handle, body) {
    $("sync").textContent = "保存中…";
    const r = await fetch("/api/row", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ handle: handle }, body)),
    });
    if (r.status === 401) {
      location.href = "/login";
      return;
    }
    const j = await r.json();
    if (j && j.item) {
      rev = j.rev || rev;
      patchLocal(handle, j.item);
      applyView();
      $("sync").textContent = "已同步";
      return;
    }
    await refresh();
  }

  function confirmPrice(tr, handle) {
    const val = (f) => {
      const n = tr.querySelector('input[data-field="' + f + '"]');
      return n ? num(n.value) : null;
    };
    save(handle, {
      sell_usd: val("sell_usd"),
      cogs_cny: val("cogs_cny"),
      first_leg_usd: val("first_leg_usd"),
      last_mile_usd: val("last_mile_usd"),
      return_cost_usd: val("return_cost_usd"),
      package_weight_kg: val("package_weight_kg"),
      length_cm: val("length_cm"),
      width_cm: val("width_cm"),
      height_cm: val("height_cm"),
      recalc: true,
    });
  }

  function setTab(name) {
    tab = name;
    page = 1;
    ["active", "bought", "cancelled"].forEach((n) => {
      $("tab-" + n).classList.toggle("on", tab === n);
    });
    applyView();
  }

  $("tab-active").onclick = () => setTab("active");
  $("tab-bought").onclick = () => setTab("bought");
  $("tab-cancelled").onclick = () => setTab("cancelled");
  $("prev").onclick = () => {
    if (page > 1) {
      page -= 1;
      applyView();
    }
  };
  $("next").onclick = () => {
    if (page < pages) {
      page += 1;
      applyView();
    }
  };
  let qt;
  $("q").addEventListener("input", () => {
    clearTimeout(qt);
    qt = setTimeout(() => {
      page = 1;
      applyView();
    }, 150);
  });
  $("page-size").addEventListener("change", () => {
    page = 1;
    applyView();
  });

  async function poll() {
    try {
      const r = await fetch("/api/meta");
      if (r.status === 401) return;
      const d = await r.json();
      if ((d.rev || 0) !== rev) await refresh();
    } catch (e) {}
  }

  refresh();
  setInterval(poll, 8000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
})();
