/* Экран результата расследования. Этот файл общий для приложения и для отчёта-файла,
   поэтому здесь нет обращений к серверу: всё рисуется из одного объекта `res`. */
(function () {
  const TT = (window.TT = window.TT || {});

  /* Комментарии к переводам пишут посторонние люди — любой текст из данных
     вставляем в страницу только через esc(), иначе в отчёт можно было бы подсунуть код. */
  TT.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  TT.num = (v) => {
    const a = Math.abs(v);
    return Number(v).toLocaleString("ru-RU", { maximumFractionDigits: a >= 100 ? 2 : a >= 1 ? 4 : 6 });
  };
  TT.usd = (v) => (v == null ? "" : "≈ $" + Number(v).toLocaleString("ru-RU", { maximumFractionDigits: v >= 100 ? 0 : 2 }));
  const pad = (n) => String(n).padStart(2, "0");
  TT.time = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  TT.tz = () => {
    const m = -new Date().getTimezoneOffset();
    return "UTC" + (m >= 0 ? "+" : "−") + Math.floor(Math.abs(m) / 60) + (m % 60 ? ":" + pad(Math.abs(m) % 60) : "");
  };
  TT.copy = async (text, btn) => {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch (e2) { /* нет доступа к буферу */ }
      t.remove();
    }
    if (btn) { const old = btn.textContent; btn.textContent = "Скопировано"; setTimeout(() => (btn.textContent = old), 1400); }
  };
  const esc = TT.esc;
  const amounts = (list) => list.map((a) => `${TT.num(a.amount)} ${esc(a.asset)}`).join(" + ");
  const pill = (role, text) => `<span class="pill"><i class="role-${esc(role)}"></i>${esc(text)}</span>`;
  const txLink = (url) => (/^https:\/\/tonviewer\.com\//.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Tonviewer</a>` : "");
  const PAT = { peel_chain: "цепочка отщипываний", peel_step: "отщипывание", split: "дробление", consolidation: "консолидация", transit: "транзит" };

  TT.renderResult = function (root, res, opts) {
    opts = opts || {};
    const nodes = new Map(res.nodes.map((n) => [n.id, n]));
    const addr = (id) => { const n = nodes.get(id); return `<button class="addr-link" data-node="${esc(id)}" title="${esc(n.address)}">${esc(n.label)}</button>`; };
    const d = res.distribution;
    const unexplored = res.nodes.filter((n) => !n.expanded && n.stop_reason && ["wallet", "contract", "hub", "scam"].includes(n.role));
    const tabs = [
      ["map", "Карта потоков", null],
      ["exits", "Конечные точки", res.exits.length],
      ["chains", "Цепочки", res.chains.length],
      ["transfers", "Переводы", res.edges.length],
      ["patterns", "Паттерны", res.patterns.length],
      ["letters", "Обращения на биржи", res.letters.length],
      ["about", "О данных", null],
    ];

    /* ---- шапка и распределение ---- */
    const stolen = d.stolen.length ? amounts(d.stolen) + (d.stolen_usd ? ` <span class="muted">(${TT.usd(d.stolen_usd)} по текущему курсу)</span>` : "") : "ничего";
    const bar = d.basis ? `<div class="bar" role="img" aria-label="Распределение средств">${d.rows.map((r) =>
      `<span class="b-${r.code}" style="width:${(r.share * 100).toFixed(2)}%" title="${esc(r.title)}: ${(r.share * 100).toFixed(1)}%">${r.share >= 0.07 ? Math.round(r.share * 100) + "%" : ""}</span>`).join("")}</div>` : "";
    const rows = d.rows.map((r) => `<div class="dist-row"><i class="b-${r.code}"></i>
        <div><b>${esc(r.title)}</b>${d.basis ? `<span class="share">${(r.share * 100).toFixed(r.share < 0.1 ? 1 : 0)}%</span>` : ""}</div>
        <small>${amounts(r.amounts)}${r.usd ? " · " + TT.usd(r.usd) : ""}<br>${esc(r.help)}</small></div>`).join("");
    const modeNote = d.mode === "all" ? `<p class="small muted">Режим «все переводы»: остатки на кошельках не считаются, в распределение попали только поступления на конечные точки.</p>` : "";

    root.innerHTML = `
      <div class="res-head">
        <h1>${esc(opts.title || "Расследование")}</h1>
        <div class="res-actions">${opts.actionsHtml || ""}</div>
      </div>
      <div class="res-meta">
        <span>Кошелёк <span class="mono">${esc(res.source_address)}</span></span>
        <span>Инцидент: ${res.incident_ts ? esc(TT.time(res.incident_ts)) + " (" + TT.tz() + ")" : "время не задано — учтены все переводы"}</span>
        <span>Глубина ${res.params.max_depth}</span>
        <span>Проверено ${esc(TT.time(res.generated_at))}</span>
      </div>
      <p class="headline">${esc(res.headline)}</p>
      ${res.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${d.rows.length ? `<section class="dist">
        <div class="dist-top"><div><b>Выведено с кошелька:</b> ${stolen}</div>
        <div class="small muted">${d.basis === "usd" ? "Доли — в долларах по текущему курсу" : d.basis === "asset" ? "Доли — по сумме актива" : "Курсы неизвестны — доли не посчитаны"}</div></div>
        ${bar}<div class="dist-rows">${rows}</div>${modeNote}</section>` : ""}
      <div class="tabs" role="tablist">${tabs.map(([id, t, n], i) =>
        `<button role="tab" data-tab="${id}" aria-selected="${i === 0}">${t}${n == null ? "" : `<em>${n}</em>`}</button>`).join("")}</div>
      ${tabs.map(([id], i) => `<section class="tab-panel" data-panel="${id}" ${i ? "hidden" : ""}></section>`).join("")}`;

    const panel = (id) => root.querySelector(`[data-panel="${id}"]`);
    const empty = (title, text) => `<div class="tbl-wrap"><div class="empty"><b>${title}</b>${text}</div></div>`;

    /* ---- карта ---- */
    let map = null;
    const mapPanel = panel("map");
    if (!res.edges.length) {
      mapPanel.innerHTML = empty("Переводов не найдено", "После указанного времени с кошелька не уходило сумм крупнее порога. Проверьте адрес, время инцидента и пороги.");
    } else {
      const assets = [];
      res.edges.forEach((e) => { if (e.kind !== "swap" && e.kind !== "nft" && !assets.some((a) => a.key === e.key)) assets.push({ key: e.key, asset: e.asset }); });
      mapPanel.innerHTML = `<div class="map-wrap">
        <div class="map-bar">
          ${assets.length > 1 ? `<button class="chip" aria-pressed="true" data-asset="">Все активы</button>` + assets.map((a) => `<button class="chip" aria-pressed="false" data-asset="${esc(a.key)}">${esc(a.asset)}</button>`).join("") : ""}
          <span class="spacer"></span>
          <span class="small muted">Колесо — масштаб, перетаскивание — сдвиг, клик по адресу — подробности</span>
          <button class="btn sm" data-zoom="out" aria-label="Уменьшить">−</button>
          <button class="btn sm" data-zoom="in" aria-label="Увеличить">+</button>
          <button class="btn sm" data-zoom="fit">Вписать</button>
        </div>
        <div class="map-host" style="position:relative"></div>
        <div class="map-legend">
          <span><i class="ln" style="background:var(--flow-ton)"></i>TON</span>
          <span><i class="ln" style="background:var(--flow-jetton)"></i>жетоны</span>
          <span><i class="ln" style="background:var(--flow-nft)"></i>NFT</span>
          <span><i style="background:var(--r-source)"></i>исходный</span>
          <span><i style="background:var(--r-wallet)"></i>кошелёк</span>
          <span><i style="background:var(--r-cex)"></i>биржа</span>
          <span><i style="background:var(--r-dex)"></i>DEX</span>
          <span><i style="background:var(--r-bridge)"></i>мост</span>
          <span><i style="background:var(--r-hub)"></i>хаб</span>
          <span><i style="background:var(--r-contract)"></i>контракт</span>
          <span>пунктирная рамка — адрес не раскрыт</span>
          <span>толщина линии — сумма</span>
        </div></div>`;
      map = new TT.FlowMap(mapPanel.querySelector(".map-host"), res, { onSelect: (id) => (id ? openNode(id) : closeDrawer()) });
      mapPanel.querySelectorAll("[data-asset]").forEach((b) => b.addEventListener("click", () => {
        mapPanel.querySelectorAll("[data-asset]").forEach((x) => x.setAttribute("aria-pressed", x === b));
        map.setAsset(b.dataset.asset || null);
      }));
      mapPanel.querySelector('[data-zoom="in"]').onclick = () => map.zoom(1.25);
      mapPanel.querySelector('[data-zoom="out"]').onclick = () => map.zoom(0.8);
      mapPanel.querySelector('[data-zoom="fit"]').onclick = () => map.fit();
    }

    /* ---- конечные точки ---- */
    panel("exits").innerHTML = res.exits.length ? `
      <p class="note">Поступления отслеживаемых средств на биржи, обменники, мосты и хабы. Memo — номер, по которому биржа зачисляет депозит на конкретный аккаунт: это главный след. Время — ваше местное (${TT.tz()}).</p>
      <div class="tbl-wrap"><table><thead><tr><th>Время</th><th>Куда</th><th>Отслеживается</th><th>Memo</th><th>От кого</th><th>Метка</th><th></th></tr></thead><tbody>
      ${res.exits.map((r) => `<tr><td class="nowrap">${esc(TT.time(r.ts))}</td>
        <td>${pill(r.role, r.role_ru)} ${addr(r.node)}</td>
        <td class="num">${TT.num(r.traced)} ${esc(r.asset)}${r.traced + 1e-9 < r.amount ? `<br><span class="conf">из ${TT.num(r.amount)}</span>` : ""}</td>
        <td>${r.memo ? `<span class="memo">${esc(r.memo)}</span>` : r.memo_required ? `<span class="conf">пусто, хотя адрес требует memo</span>` : `<span class="conf">нет</span>`}</td>
        <td>${addr(r.from_node)}</td>
        <td class="conf">${esc(r.confidence)}<br>${esc(r.reason)}</td>
        <td>${txLink(r.tx_url)}</td></tr>`).join("")}</tbody></table></div>`
      : empty("Конечные точки не найдены", "Средства не дошли до известных бирж и сервисов в пределах заданной глубины. Попробуйте увеличить глубину или посмотрите вкладку «Цепочки» — где след обрывается.");

    /* ---- цепочки ---- */
    panel("chains").innerHTML = res.chains.length ? `
      <p class="note">Для каждой точки, где след заканчивается, — основной путь от исходного кошелька. Нажмите «Показать на карте», чтобы подсветить маршрут.</p>
      ${res.chains.map((c) => `<div class="chain">
        <div class="chain-head"><b>${esc(c.entity || c.path_labels[c.path_labels.length - 1])}</b>${pill(c.role, c.role_ru)}
          <span>${c.status === "конечная точка" ? "дошло" : esc(c.status)}: ${amounts(c.amounts) || "—"}</span>
          <span class="muted small">${c.hops} ${c.hops === 1 ? "шаг" : c.hops < 5 ? "шага" : "шагов"}</span>
          ${map ? `<button class="btn sm quiet" data-path="${c.id}">Показать на карте</button>` : ""}</div>
        <div class="path">${c.path.map((id, i) => `${i ? '<span class="arr">→</span>' : ""}<button class="hop" data-node="${esc(id)}">${esc(c.path_labels[i])}</button>`).join("")}</div>
        <div class="tagrow">${c.memos.map((m) => `<span class="memo">memo ${esc(m)}</span>`).join("")}${c.patterns.map((p) => `<span class="tag">${esc(PAT[p] || p)}</span>`).join("")}</div>
        ${c.stop_reason && c.status !== "конечная точка" ? `<p class="small muted" style="margin-top:8px">Почему остановились: ${esc(c.stop_reason)}</p>` : ""}
      </div>`).join("")}` : empty("Цепочек нет", "Нет ни одного прослеженного перевода.");

    /* ---- переводы ---- */
    const trRow = (e) => `<tr data-text="${esc((nodes.get(e.from).label + " " + nodes.get(e.to).label + " " + e.asset + " " + e.memo + " " + nodes.get(e.from).address + " " + nodes.get(e.to).address).toLowerCase())}">
      <td class="num">${e.depth}</td><td class="nowrap">${esc(TT.time(e.ts))}</td><td>${addr(e.from)}</td><td>${addr(e.to)}</td>
      <td class="num">${e.kind === "swap" ? "обмен" : e.kind === "nft" ? "NFT" : TT.num(e.traced) + " " + esc(e.asset)}${e.kind !== "swap" && e.traced + 1e-9 < e.amount ? `<br><span class="conf">из ${TT.num(e.amount)}</span>` : ""}</td>
      <td>${e.memo ? `<span class="memo">${esc(e.memo)}</span>` : ""}${e.note ? `<span class="conf"> ${esc(e.note)}</span>` : ""}</td><td>${txLink(e.tx_url)}</td></tr>`;
    panel("transfers").innerHTML = res.edges.length ? `
      <div class="tbl-tools"><input type="text" placeholder="Найти по адресу, названию, активу или memo" aria-label="Фильтр переводов" data-filter>
      <span class="small muted" data-count>${res.edges.length} переводов</span></div>
      <div class="tbl-wrap"><table><thead><tr><th>Шаг</th><th>Время</th><th>От</th><th>Кому</th><th>Отслеживается</th><th>Memo и примечания</th><th></th></tr></thead>
      <tbody>${res.edges.map(trRow).join("")}</tbody></table></div>` : empty("Переводов нет", "");
    const filter = panel("transfers").querySelector("[data-filter]");
    if (filter) filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      let shown = 0;
      panel("transfers").querySelectorAll("tbody tr").forEach((tr) => { const ok = !q || tr.dataset.text.includes(q); tr.hidden = !ok; shown += ok; });
      panel("transfers").querySelector("[data-count]").textContent = `${shown} из ${res.edges.length}`;
    });

    /* ---- паттерны ---- */
    panel("patterns").innerHTML = res.patterns.length ? `
      <p class="note">Типичные приёмы запутывания следа. Это наблюдения о движении средств, а не доказательство умысла: так же может выглядеть и обычная активность.</p>
      <div class="tbl-wrap"><table><thead><tr><th>Паттерн</th><th>Адрес</th><th>Что произошло</th></tr></thead><tbody>
      ${res.patterns.map((p) => `<tr><td><b>${esc(p.title)}</b><br><span class="conf">${esc(p.help)}</span></td><td>${addr(p.node)}</td>
        <td>${esc(p.detail)}${p.amount ? ` · ${TT.num(p.amount)} ${esc(p.asset)}` : ""}${p.chain.length ? `<div class="path" style="margin-top:6px">${p.chain.map((id, i) => `${i ? '<span class="arr">→</span>' : ""}<button class="hop" data-node="${esc(id)}">${esc(p.chain_labels[i])}</button>`).join("")}</div>` : ""}</td></tr>`).join("")}
      </tbody></table></div>` : empty("Паттерны не найдены", "Дробления, транзита и цепочек отщипываний в этом графе нет.");

    /* ---- обращения ---- */
    panel("letters").innerHTML = res.letters.length ? `
      <p class="note">Готовый текст для службы поддержки или отдела комплаенс биржи. Биржа не вернёт средства по письму, но может приостановить вывод и сохранить данные до запроса полиции — поэтому подайте заявление и впишите его номер. Перед отправкой проверьте, что метка биржи верна.</p>
      ${res.letters.map((l, i) => `<div class="letter" data-letter="${i}">
        <div class="letter-head"><div><b>${esc(l.entity)}</b> <span class="conf">метка: ${esc(l.confidence)} — ${esc(l.reason)} · депозитов: ${l.deposits}</span></div>
        <div><button class="chip" aria-pressed="true" data-lang="ru">Русский</button> <button class="chip" aria-pressed="false" data-lang="en">English</button>
        <button class="btn sm" data-copy>Скопировать</button></div></div><pre>${esc(l.ru)}</pre></div>`).join("")}`
      : empty("Обращаться пока некуда", "Поступлений на адреса бирж не найдено. Если вы знаете, что один из адресов принадлежит бирже, пометьте его и запустите расследование заново.");
    panel("letters").querySelectorAll("[data-letter]").forEach((box) => {
      const l = res.letters[+box.dataset.letter];
      let lang = "ru";
      box.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
        lang = b.dataset.lang;
        box.querySelectorAll("[data-lang]").forEach((x) => x.setAttribute("aria-pressed", x === b));
        box.querySelector("pre").textContent = l[lang];
      }));
      box.querySelector("[data-copy]").addEventListener("click", (ev) => TT.copy(l[lang], ev.currentTarget));
    });

    /* ---- о данных ---- */
    const s = res.stats || {};
    panel("about").innerHTML = `<div class="cols2">
      <div><h3>Как получен результат</h3>
        <dl class="kv" style="margin-top:10px">
          <dt>Источник</dt><dd>TonAPI (tonapi.io) — публичный индексатор сети TON</dd>
          <dt>Запросов к источнику</dt><dd>${s.api_calls ?? "—"}${s.cache_hits ? `, из кэша: ${s.cache_hits}` : ""}${s.retries ? `, повторов после ошибок: ${s.retries}` : ""}</dd>
          <dt>Курсы</dt><dd>${esc(s.rates || "—")}</dd>
          <dt>Время работы</dt><dd>${s.seconds ?? "—"} с</dd>
          <dt>Режим</dt><dd>${res.params.mode === "budget" ? "в пределах украденной суммы" : "все исходящие переводы"}</dd>
          <dt>Пороги</dt><dd>${res.params.min_ton} TON, ${res.params.min_jetton} в единицах жетона</dd>
          <dt>Адресов / переводов</dt><dd>${res.nodes.length} / ${res.edges.length}</dd>
        </dl>
        <h3 style="margin-top:22px">Что не удалось выяснить</h3>
        ${unexplored.length ? `<ul class="plain-list">${unexplored.map((n) => `<li>${addr(n.id)} — ${esc(n.stop_reason)}</li>`).join("")}</ul>` : `<p class="muted" style="margin-top:8px">Все кошельки в пределах заданной глубины раскрыты.</p>`}
      </div>
      <div><h3>Как читать результат</h3>
        <ul class="plain-list">
          <li>Это граф переводов, а не доказательство владения. Промежуточный кошелёк может принадлежать постороннему: обменнику, продавцу, участнику P2P-сделки.</li>
          <li>«Отслеживается» — оценка. Программа считает, что украденные средства уходят с кошелька первыми, и не идёт дальше, когда их сумма исчерпана.</li>
          <li>Метки бирж и сервисов берутся из TonAPI и неполны: неподписанный горячий кошелёк биржи будет выглядеть как хаб или обычный кошелёк. Уровень уверенности указан у каждой метки.</li>
          <li>Доллары — по курсу на момент проверки, а не на момент кражи.</li>
          <li>Каждую транзакцию можно проверить по ссылке на Tonviewer.</li>
        </ul>
      </div></div>`;

    /* ---- вкладки ---- */
    const tabBtns = root.querySelectorAll("[data-tab]");
    const showTab = (id) => {
      tabBtns.forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === id));
      root.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== id));
      if (id === "map" && map) requestAnimationFrame(() => map.fit());
    };
    tabBtns.forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

    /* ---- карточка адреса ---- */
    const drawer = document.getElementById("drawer");
    function closeDrawer() {
      const was = document.body.classList.contains("with-drawer");
      drawer.hidden = true; drawer.innerHTML = "";
      document.body.classList.remove("with-drawer");
      if (was && map) requestAnimationFrame(() => map.fit());
    }
    function openNode(id) {
      const n = nodes.get(id);
      if (!n) return;
      const ins = res.edges.filter((e) => e.to === id), outs = res.edges.filter((e) => e.from === id);
      const tx = (e, other, dir) => `<div class="tx"><div class="tx-top"><span>${dir} ${addr(other)}</span>
        <b>${e.kind === "swap" ? "обмен" : e.kind === "nft" ? "NFT" : TT.num(e.traced) + " " + esc(e.asset)}</b></div>
        <div class="conf">${esc(TT.time(e.ts))}${e.kind !== "swap" && e.traced + 1e-9 < e.amount ? ` · перевод на ${TT.num(e.amount)}, отслеживается часть` : ""}${e.memo ? ` · memo <span class="memo">${esc(e.memo)}</span>` : ""}${e.note ? " · " + esc(e.note) : ""} ${txLink(e.tx_url)}</div></div>`;
      drawer.innerHTML = `
        <button class="btn quiet sm drawer-close" data-close aria-label="Закрыть">Закрыть</button>
        ${pill(n.role, n.role_ru)}
        <h2>${esc(n.entity || n.short)}</h2>
        <div class="addr-full"><span class="mono">${esc(n.address)}</span><button class="btn sm" data-copy-addr>Копировать</button></div>
        ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">Открыть в Tonviewer</a>` : ""}
        <section><dl class="kv">
          <dt>Шаг от источника</dt><dd>${n.depth}</dd>
          ${n.reason ? `<dt>Почему такая метка</dt><dd>${esc(n.reason)}${n.confidence && n.role !== "source" ? ` <span class="conf">(${esc(n.confidence)})</span>` : ""}</dd>` : ""}
          ${n.received.length ? `<dt>Получено</dt><dd>${amounts(n.received)}</dd>` : ""}
          ${n.sent.length ? `<dt>Отправлено дальше</dt><dd>${amounts(n.sent)}</dd>` : ""}
          ${n.holding.length ? `<dt>Осталось на адресе</dt><dd>${amounts(n.holding)} <span class="conf">(оценка)</span></dd>` : ""}
          ${n.memo_required ? `<dt>Memo</dt><dd>адрес требует memo — признак биржи</dd>` : ""}
          ${n.is_scam ? `<dt>Внимание</dt><dd>TonAPI помечает адрес как scam</dd>` : ""}
          ${n.tags.length ? `<dt>Паттерны</dt><dd>${n.tags.map((t) => esc(PAT[t] || t)).join(", ")}</dd>` : ""}
          ${n.stop_reason ? `<dt>Дальше не пошли</dt><dd>${esc(n.stop_reason)}</dd>` : ""}
          ${n.untraced_out ? `<dt>Вне расследования</dt><dd>${n.untraced_out} исходящих переводов не связаны с отслеживаемой суммой</dd>` : ""}
        </dl></section>
        ${ins.length ? `<section><h3>Поступления</h3>${ins.map((e) => tx(e, e.from, "от")).join("")}</section>` : ""}
        ${outs.length ? `<section><h3>Отправления</h3>${outs.map((e) => tx(e, e.to, "→")).join("")}</section>` : ""}
        ${opts.labelFormHtml && n.role !== "source" && n.id.includes(":") && /^-?\d+:[0-9a-f]{64}$/.test(n.id) ? `<section><h3>Вы знаете, чей это адрес?</h3>${opts.labelFormHtml}</section>` : ""}`;
      drawer.hidden = false;
      // На широком экране сдвигаем страницу, чтобы карточка не закрывала карту.
      if (!document.body.classList.contains("with-drawer")) {
        document.body.classList.add("with-drawer");
        if (map) requestAnimationFrame(() => map.fit());
      }
      drawer.querySelector("[data-close]").onclick = () => { closeDrawer(); if (map) map.clearSelection(); };
      drawer.querySelector("[data-copy-addr]").onclick = (ev) => TT.copy(n.address, ev.currentTarget);
      if (opts.onLabelForm) opts.onLabelForm(drawer, n);
      if (map && map.nodeEls.has(id)) map.select(id, false);
    }
    // Клик по любому адресу на странице или в карточке открывает его карточку.
    const onNodeClick = (ev) => {
      const b = ev.target.closest("[data-node]");
      if (b) { openNode(b.dataset.node); return; }
      const p = ev.target.closest("[data-path]");
      if (p && map) { const c = res.chains.find((x) => x.id === +p.dataset.path); showTab("map"); closeDrawer(); map.highlight(c.path); window.scrollTo({ top: root.querySelector(".tabs").offsetTop - 10 }); }
    };
    root.addEventListener("click", onNodeClick);
    drawer.onclick = onNodeClick;
    document.onkeydown = (ev) => { if (ev.key === "Escape" && !drawer.hidden) { closeDrawer(); if (map) map.clearSelection(); } };
    closeDrawer();
    return { openNode, closeDrawer, showTab };
  };
})();
