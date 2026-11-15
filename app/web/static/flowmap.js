/* Карта потоков: слева исходный кошелёк, каждый столбец — следующий шаг.
   Толщина линии — сумма. Рисуется обычным SVG, без сторонних библиотек,
   поэтому та же карта работает в отчёте, который открывается без интернета. */
(function () {
  const TT = (window.TT = window.TT || {});
  const NS = "http://www.w3.org/2000/svg";
  const COL_W = 252, NODE_W = 172, GAP = 18, PAD = 34;
  const EXIT = new Set(["cex", "dex", "bridge", "mixer", "service"]);

  function svg(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

  class FlowMap {
    constructor(container, res, opts) {
      this.box = container;
      this.res = res;
      this.opts = opts || {};
      this.asset = null;           // фильтр по активу (ключ) или null
      this.selected = null;
      this.view = { x: 0, y: 0, k: 1 };
      this.nodeById = new Map(res.nodes.map((n) => [n.id, n]));
      this.root = svg("svg", { class: "map-svg", role: "img", "aria-label": "Карта потоков средств" });
      this.layer = svg("g", {}, this.root);
      this.tip = document.createElement("div");
      this.tip.className = "map-tip";
      this.tip.hidden = true;
      container.append(this.root, this.tip);
      this._bindPanZoom();
      this.draw();
      // Размер контейнера известен только после вставки в страницу.
      requestAnimationFrame(() => this.fit());
    }

    /* ---- данные: склеиваем переводы между одной парой адресов в одну линию ---- */
    _groups() {
      const groups = new Map();
      for (const e of this.res.edges) {
        if (this.asset && e.key !== this.asset) continue;
        const cls = e.kind === "swap" ? "swap" : e.kind === "nft" ? "nft" : e.key === "TON" ? "ton" : "jetton";
        const id = e.from + "|" + e.to + "|" + (cls === "swap" ? "swap" : e.key.startsWith("NFT:") ? "NFT" : e.key);
        let g = groups.get(id);
        if (!g) groups.set(id, (g = { id, from: e.from, to: e.to, cls, asset: e.asset, total: 0, usd: 0, n: 0, memos: [], items: [], priced: true }));
        g.total += e.traced; g.n += 1; g.items.push(e);
        if (e.usd == null) g.priced = false; else g.usd += e.usd;
        if (e.memo && !g.memos.includes(e.memo)) g.memos.push(e.memo);
      }
      return [...groups.values()];
    }

    /* Вес линии: в долларах, если знаем курсы; иначе — доля от самой крупной суммы в том же активе. */
    _weights(groups) {
      const real = groups.filter((g) => g.cls !== "swap" && g.cls !== "nft");
      const usdOk = real.length > 0 && real.every((g) => g.priced);
      const maxByAsset = {};
      for (const g of real) maxByAsset[g.asset] = Math.max(maxByAsset[g.asset] || 0, g.total);
      for (const g of groups) {
        if (g.cls === "swap" || g.cls === "nft") g.w = 0;
        else g.w = usdOk ? g.usd : g.total / (maxByAsset[g.asset] || 1);
      }
      const max = Math.max(1e-9, ...groups.map((g) => g.w));
      for (const g of groups) g.width = g.w > 0 ? 1.6 + 15 * Math.sqrt(g.w / max) : 1.8;
    }

    draw() {
      this.layer.textContent = "";
      const groups = this._groups();
      this._weights(groups);
      const used = new Set([this.res.source]);
      groups.forEach((g) => { used.add(g.from); used.add(g.to); });
      const nodes = this.res.nodes.filter((n) => used.has(n.id)).map((n) => ({ n, col: Math.min(n.depth, 99), ins: [], outs: [], w: 0 }));
      const P = new Map(nodes.map((p) => [p.n.id, p]));
      for (const g of groups) { P.get(g.from).outs.push(g); P.get(g.to).ins.push(g); }
      const sum = (arr) => arr.reduce((s, g) => s + g.w, 0);
      const maxW = Math.max(1e-9, ...nodes.map((p) => (p.w = Math.max(sum(p.ins), sum(p.outs)))));
      // Memo — главный след, поэтому показываем его прямо на адресе-получателе отдельной строкой.
      nodes.forEach((p) => {
        p.memos = [...new Set(p.ins.flatMap((g) => g.memos))];
        p.h = 40 + 44 * Math.sqrt(p.w / maxW) + (p.memos.length ? 15 : 0);
      });

      // Столбцы по числу шагов от исходного кошелька.
      const cols = [];
      nodes.forEach((p) => (cols[p.col] = cols[p.col] || []).push(p));
      const filled = cols.filter(Boolean);
      filled.forEach((c, i) => c.forEach((p, j) => { p.ci = i; p.order = j; }));
      // Несколько проходов: ставим узел напротив соседей, чтобы линии меньше пересекались.
      const bary = (p, side) => {
        const peers = (side === "in" ? p.ins.map((g) => P.get(g.from)) : p.outs.map((g) => P.get(g.to))).filter((q) => q.ci !== p.ci);
        return peers.length ? peers.reduce((s, q) => s + q.order, 0) / peers.length : p.order;
      };
      for (let pass = 0; pass < 4; pass++) {
        const seq = pass % 2 === 0 ? filled : [...filled].reverse();
        for (const c of seq) {
          c.forEach((p) => (p.b = bary(p, pass % 2 === 0 ? "in" : "out")));
          c.sort((a, b) => a.b - b.b || b.w - a.w);
          c.forEach((p, j) => (p.order = j));
        }
      }
      const heights = filled.map((c) => c.reduce((s, p) => s + p.h + GAP, -GAP));
      const H = Math.max(...heights, 200);
      filled.forEach((c, i) => {
        let y = PAD + (H - heights[i]) / 2;
        c.forEach((p) => { p.x = PAD + i * COL_W; p.y = y; y += p.h + GAP; });
      });
      this.size = { w: PAD * 2 + (filled.length - 1) * COL_W + NODE_W, h: H + PAD * 2 };

      filled.forEach((c, i) => {
        const t = svg("text", { class: "col-title", x: PAD + i * COL_W, y: PAD - 20 }, this.layer);
        t.textContent = c[0].col === 0 ? "Откуда" : "Шаг " + c[0].col;
      });

      // Точки входа и выхода линий распределяем по высоте узла.
      for (const p of nodes) {
        p.outs.sort((a, b) => P.get(a.to).y - P.get(b.to).y).forEach((g, i) => (g.y1 = p.y + (p.h * (i + 1)) / (p.outs.length + 1)));
        p.ins.sort((a, b) => P.get(a.from).y - P.get(b.from).y).forEach((g, i) => (g.y2 = p.y + (p.h * (i + 1)) / (p.ins.length + 1)));
      }

      const edgeLayer = svg("g", {}, this.layer), labelLayer = svg("g", {}, this.layer), nodeLayer = svg("g", {}, this.layer);
      const showLabels = groups.length <= 70;
      this.edgeEls = [];
      for (const g of groups) {
        const a = P.get(g.from), b = P.get(g.to);
        const x1 = a.x + NODE_W, x2 = b.x, forward = b.ci > a.ci;
        let d, lx, ly;
        if (forward) {
          const dx = (x2 - x1) * 0.5;
          d = `M${x1},${g.y1} C${x1 + dx},${g.y1} ${x2 - dx},${g.y2} ${x2},${g.y2}`;
          lx = (x1 + x2) / 2; ly = (g.y1 + g.y2) / 2;
        } else {
          // Возврат на тот же или предыдущий шаг — обводим снизу.
          const low = Math.max(a.y + a.h, b.y + b.h) + 46;
          d = `M${x1},${g.y1} C${x1 + 70},${g.y1} ${x1 + 70},${low} ${(x1 + x2) / 2},${low} S${x2 - 70},${g.y2} ${x2},${g.y2}`;
          lx = (x1 + x2) / 2; ly = low;
        }
        const path = svg("path", { d, class: `edge ${g.cls}${forward ? "" : " back"}`, "stroke-width": g.width.toFixed(1) }, edgeLayer);
        path.addEventListener("pointermove", (ev) => this._showTip(ev, this._edgeTip(g)));
        path.addEventListener("pointerleave", () => (this.tip.hidden = true));
        let label = null;
        if (showLabels && g.cls !== "swap") {
          label = svg("text", { class: "edge-label" + (g.memos.length ? " memo" : ""), x: lx, y: ly - g.width / 2 - 4, "text-anchor": "middle" }, labelLayer);
          const amount = g.cls === "nft" ? `NFT × ${g.n}` : `${TT.num(g.total)} ${g.asset}`;
          label.textContent = amount;
        }
        this.edgeEls.push({ g, path, label });
      }

      this.nodeEls = new Map();
      for (const p of nodes) {
        const n = p.n;
        const isExit = EXIT.has(n.role);
        const el = svg("g", {
          class: `node r-${n.role}${isExit ? " exit" : ""}${n.expanded || isExit || n.role === "source" ? "" : " unexpanded"}`,
          transform: `translate(${p.x},${p.y})`, tabindex: 0, role: "button",
          "aria-label": `${n.role_ru} ${n.label}`,
        }, nodeLayer);
        svg("rect", { class: "box", width: NODE_W, height: p.h, rx: 6 }, el);
        svg("rect", { class: `role-${n.role}`, width: 6, height: p.h - 2, x: 1, y: 1, rx: 3, "stroke-width": 0 }, el);
        const mid = p.h / 2 - (p.memos.length ? 7 : 0);
        const name = svg("text", { class: "name", x: 16, y: mid - 2 }, el);
        name.textContent = cut(n.label, 19);
        const sub = svg("text", { class: "sub", x: 16, y: mid + 13 }, el);
        sub.textContent = cut(this._sub(n), 28);
        if (p.memos.length) {
          const m = svg("text", { class: "sub memo-line", x: 16, y: mid + 27 }, el);
          m.textContent = cut("memo " + p.memos.join(", "), 26);
        }
        if (n.tags.length) {
          // Жёлтая метка — на этом кошельке сработал паттерн (подробности в карточке).
          svg("circle", { cx: NODE_W - 13, cy: 13, r: 8, fill: "var(--r-cex)" }, el);
          const f = svg("text", { class: "flag", x: NODE_W - 13, y: 16.5, "text-anchor": "middle" }, el);
          f.textContent = n.tags.length;
        }
        el.addEventListener("click", (ev) => { if (!this._moved) this.select(n.id, true); ev.stopPropagation(); });
        el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.select(n.id, true); } });
        this.nodeEls.set(n.id, el);
      }
      if (this.selected && this.nodeEls.has(this.selected)) this._paintSelection(); else this.root.classList.remove("has-sel");
    }

    _sub(n) {
      if (n.role === "source") return n.sent.map((a) => `${TT.num(a.amount)} ${a.asset}`).join(" + ") || n.role_ru;
      const got = n.received.map((a) => `${TT.num(a.amount)} ${a.asset}`).join(" + ");
      if (n.role === "wallet") return got || n.role_ru;
      return n.role_ru + (got ? " · " + got : "");
    }

    _edgeTip(g) {
      const rows = g.items.slice(0, 6).map((e) =>
        `${TT.esc(TT.time(e.ts))} — ${e.kind === "swap" ? TT.esc(e.note) : TT.num(e.traced) + " " + TT.esc(e.asset) + (e.traced + 1e-9 < e.amount ? ` из ${TT.num(e.amount)}` : "")}` +
        (e.memo ? ` · memo <b style="display:inline">${TT.esc(e.memo)}</b>` : ""));
      const more = g.items.length > 6 ? `<br>…и ещё ${g.items.length - 6}` : "";
      const a = this.nodeById.get(g.from), b = this.nodeById.get(g.to);
      return `<b>${TT.esc(a.label)} → ${TT.esc(b.label)}</b>${rows.join("<br>")}${more}`;
    }

    _showTip(ev, html) {
      const r = this.box.getBoundingClientRect();
      this.tip.innerHTML = html;
      this.tip.hidden = false;
      const x = Math.min(ev.clientX - r.left + 14, r.width - this.tip.offsetWidth - 8);
      this.tip.style.left = Math.max(8, x) + "px";
      this.tip.style.top = ev.clientY - r.top + 16 + "px";
    }

    /* ---- выделение: показываем, откуда деньги пришли на адрес и куда ушли с него ---- */
    select(id, notify) {
      this.selected = id;
      this._paintSelection();
      if (notify && this.opts.onSelect) this.opts.onSelect(id);
    }
    clearSelection() {
      this.selected = null;
      this.root.classList.remove("has-sel");
      this.root.querySelectorAll(".on, .sel").forEach((el) => el.classList.remove("on", "sel"));
    }
    highlight(ids) {
      this.selected = null;
      const set = new Set(ids);
      this.root.classList.add("has-sel");
      this.nodeEls.forEach((el, id) => { el.classList.toggle("on", set.has(id)); el.classList.remove("sel"); });
      for (const { g, path, label } of this.edgeEls) {
        const i = ids.indexOf(g.from);
        const on = i >= 0 && ids[i + 1] === g.to;
        path.classList.toggle("on", on);
        if (label) label.classList.toggle("on", on);
      }
    }
    _paintSelection() {
      const id = this.selected;
      const on = new Set([id]), onEdges = new Set();
      const walk = (start, dir) => {
        const stack = [start], seen = new Set([start]);
        while (stack.length) {
          const cur = stack.pop();
          for (const { g } of this.edgeEls) {
            const [a, b] = dir > 0 ? [g.from, g.to] : [g.to, g.from];
            if (a !== cur || g.cls === "swap") continue;
            onEdges.add(g.id);
            if (!seen.has(b)) { seen.add(b); on.add(b); stack.push(b); }
          }
        }
      };
      walk(id, 1); walk(id, -1);
      for (const { g } of this.edgeEls) if (g.cls === "swap" && on.has(g.from)) { onEdges.add(g.id); on.add(g.to); }
      this.root.classList.add("has-sel");
      this.nodeEls.forEach((el, nid) => { el.classList.toggle("on", on.has(nid)); el.classList.toggle("sel", nid === id); });
      for (const { g, path, label } of this.edgeEls) {
        path.classList.toggle("on", onEdges.has(g.id));
        if (label) label.classList.toggle("on", onEdges.has(g.id));
      }
    }

    setAsset(key) { this.asset = key; this.draw(); this.fit(); }

    /* ---- перемещение и масштаб ---- */
    _apply() { this.layer.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`); }
    fit() {
      const r = this.root.getBoundingClientRect();
      if (!r.width || !this.size) return;
      // Сначала вписываем по ширине и подгоняем высоту холста под схему, чтобы не было пустых полей.
      // В узком окне не уменьшаем до нечитаемого: показываем начало схемы, остальное — сдвигом.
      let k = Math.max(Math.min(r.width / this.size.w, 1.25), 0.5);
      const maxH = Math.max(360, Math.round(window.innerHeight * 0.7));
      const h = Math.min(maxH, Math.max(300, Math.round(this.size.h * k + 24)));
      this.root.style.height = h + "px";
      k = Math.min(k, h / this.size.h);
      this.view = { k, x: Math.max(6, (r.width - this.size.w * k) / 2), y: Math.max(0, (h - this.size.h * k) / 2) };
      this._apply();
    }
    zoom(f, cx, cy) {
      const r = this.root.getBoundingClientRect();
      cx = cx == null ? r.width / 2 : cx; cy = cy == null ? r.height / 2 : cy;
      const k = Math.min(3, Math.max(0.15, this.view.k * f));
      this.view.x = cx - ((cx - this.view.x) * k) / this.view.k;
      this.view.y = cy - ((cy - this.view.y) * k) / this.view.k;
      this.view.k = k;
      this._apply();
    }
    _bindPanZoom() {
      let start = null;
      this.root.addEventListener("wheel", (ev) => {
        ev.preventDefault();
        const r = this.root.getBoundingClientRect();
        this.zoom(ev.deltaY < 0 ? 1.12 : 1 / 1.12, ev.clientX - r.left, ev.clientY - r.top);
      }, { passive: false });
      this.root.addEventListener("pointerdown", (ev) => {
        start = { x: ev.clientX, y: ev.clientY, vx: this.view.x, vy: this.view.y };
        this._moved = false;
      });
      window.addEventListener("pointermove", (ev) => {
        if (!start) return;
        const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) { this._moved = true; this.root.classList.add("drag"); }
        if (this._moved) { this.view.x = start.vx + dx; this.view.y = start.vy + dy; this._apply(); }
      });
      window.addEventListener("pointerup", () => { start = null; this.root.classList.remove("drag"); });
      this.root.addEventListener("click", () => { if (!this._moved && this.selected) { this.clearSelection(); if (this.opts.onSelect) this.opts.onSelect(null); } });
    }
  }

  TT.FlowMap = FlowMap;
})();
