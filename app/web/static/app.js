/* Оболочка приложения: список расследований, форма запуска, ход работы, метки, настройки.
   Экран результата рисует view.js. Страницы переключаются по «хвосту» адреса: #/new, #/i/5, #/labels. */
(function () {
  const TT = window.TT;
  const esc = TT.esc;
  const view = document.getElementById("view");
  const drawer = document.getElementById("drawer");
  let meta = { roles: [], has_key: false };
  let pollTimer = null;
  let prefill = null;   // параметры для повторного запуска

  /* ---------- общение с сервером ---------- */
  async function api(path, method, body) {
    let r;
    try {
      r = await fetch(path, { method: method || "GET", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw new Error("Программа не отвечает. Похоже, она закрыта — запустите её снова.");
    }
    let data = {};
    try { data = await r.json(); } catch (e) { /* пустой ответ */ }
    if (!r.ok) throw new Error(data.error || "Ошибка " + r.status);
    return data;
  }

  function toast(text, actionText, action) {
    document.querySelectorAll(".toast").forEach((t) => t.remove());
    const t = document.createElement("div");
    t.className = "toast";
    t.setAttribute("role", "status");
    t.textContent = text;
    if (actionText) {
      const b = document.createElement("button");
      b.textContent = actionText;
      b.onclick = () => { t.remove(); action(); };
      t.appendChild(b);
    }
    document.body.appendChild(t);
    setTimeout(() => t.remove(), actionText ? 9000 : 3500);
  }

  /* ---------- список слева ---------- */
  const STATUS = { running: "идёт…", done: "", error: "ошибка", cancelled: "остановлено" };
  function invTitle(i) { return i.title || "Кошелёк " + i.address.slice(0, 6) + "…" + i.address.slice(-4); }
  async function refreshList() {
    const el = document.getElementById("inv-list");
    let data;
    try { data = await api("/api/investigations"); } catch (e) { return; }
    const cur = (location.hash.match(/^#\/i\/(\d+)/) || [])[1];
    el.innerHTML = data.items.length
      ? "<h3>Расследования</h3>" + data.items.map((i) => `<a class="inv${String(i.id) === cur ? " active" : ""}" href="#/i/${i.id}">
          <span class="inv-title">${esc(invTitle(i))}</span>
          <span class="inv-sub">${esc(TT.time(i.created_at))}${STATUS[i.status] ? ` · <span class="st-${i.status}">${STATUS[i.status]}</span>` : ""}</span></a>`).join("")
      : `<p class="small muted" style="padding:10px">Здесь появятся ваши расследования. Они хранятся только на этом компьютере.</p>`;
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", location.hash === "#/" + a.dataset.nav));
  }

  /* ---------- новое расследование ---------- */
  function toLocalInput(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000 - new Date().getTimezoneOffset() * 60000);
    return d.toISOString().slice(0, 16);
  }
  function showNew() {
    const p = prefill || {};
    const pr = p.params || {};
    prefill = null;
    view.innerHTML = `<div class="ask">
      <h1>Куда ушли деньги?</h1>
      <p>Укажите кошелёк, с которого украли средства, и время кражи. Программа пройдёт по переводам в сети TON и покажет, где деньги сейчас: на бирже, в обменнике, на промежуточных кошельках.</p>
      <form id="new-form" novalidate>
        <label class="field addr"><span>Адрес кошелька</span>
          <input type="text" name="address" autocomplete="off" spellcheck="false" placeholder="UQ… или EQ…" value="${esc(p.address || "")}" required>
          <small>Адрес можно скопировать из Tonkeeper или со страницы кошелька на tonviewer.com.</small></label>
        <div class="row">
          <label class="field"><span>Когда произошла кража</span>
            <input type="datetime-local" name="incident" value="${toLocalInput(pr.incident_ts)}">
            <small>По вашему времени (${TT.tz()}). Учитываются только переводы после этого момента. Лучше указать на минуту раньше первого чужого перевода.</small></label>
          <label class="field"><span>Не позже (необязательно)</span>
            <input type="datetime-local" name="until" value="${toLocalInput(pr.end_ts)}">
            <small>Если после кражи вы сами продолжили пользоваться кошельком — отсеките свои переводы.</small></label>
        </div>
        <label class="field"><span>Название (необязательно)</span>
          <input type="text" name="title" maxlength="120" placeholder="Например: взлом кошелька 29 октября" value="${esc(p.title || "")}"></label>
        <details class="tune" ${p.params ? "open" : ""}>
          <summary>Тонкая настройка</summary>
          <div class="grid">
            <div class="row3">
              <label class="field"><span>Глубина, шагов</span><input type="number" name="max_depth" min="1" max="12" value="${pr.max_depth || 5}">
                <small>Сколько кошельков подряд проходить. Больше — дольше.</small></label>
              <label class="field"><span>Минимум, TON</span><input type="number" name="min_ton" min="0" step="any" value="${pr.min_ton ?? 1}">
                <small>Переводы мельче считаются пылью.</small></label>
              <label class="field"><span>Минимум, жетоны</span><input type="number" name="min_jetton" min="0" step="any" value="${pr.min_jetton ?? 1}">
                <small>В единицах жетона (для USDT — в долларах).</small></label>
            </div>
            <label class="field"><span>Как идти по переводам</span>
              <select name="mode">
                <option value="budget" ${pr.mode !== "all" ? "selected" : ""}>В пределах украденной суммы — рекомендуется</option>
                <option value="all" ${pr.mode === "all" ? "selected" : ""}>По всем исходящим переводам каждого кошелька</option>
              </select>
              <small>В первом режиме программа не идёт дальше, когда кошелёк уже переслал всё полученное от вора. Второй захватывает больше, но в граф попадают чужие деньги.</small></label>
            <div class="row">
              <label class="field"><span>Только эти жетоны</span><input type="text" name="jettons" placeholder="USDT, NOT" value="${esc((pr.jettons || []).join ? (pr.jettons || []).join(", ") : pr.jettons || "")}">
                <small>Пусто — следить за всеми. Можно указать адрес мастер-контракта.</small></label>
              <label class="field"><span>Лимит событий на адрес</span><input type="number" name="max_events" min="100" max="20000" value="${pr.max_events || 1000}">
                <small>Адрес с большей активностью считается хабом (горячим кошельком сервиса) и не раскрывается.</small></label>
            </div>
            <label class="check"><input type="checkbox" name="follow_nft" ${pr.follow_nft === false ? "" : "checked"}><span>Следить за NFT</span></label>
            <label class="check"><input type="checkbox" name="follow_contracts" ${pr.follow_contracts ? "checked" : ""}><span>Раскрывать неизвестные смарт-контракты <span class="muted">(обычно это пулы и сервисы, где деньги смешиваются)</span></span></label>
            <label class="check"><input type="checkbox" name="fresh"><span>Не использовать сохранённые ответы <span class="muted">(запросить всё заново; по умолчанию ответы хранятся 6 часов)</span></span></label>
          </div>
        </details>
        <p class="form-error" id="form-error" role="alert" hidden></p>
        <div class="actions">
          <button class="btn primary" type="submit">Отследить</button>
          <button class="btn quiet" type="button" id="demo-btn">Посмотреть на выдуманном примере</button>
        </div>
      </form>
      <div class="route-note">
        Что важно знать заранее
        <ul>
          <li>Данные берутся из открытого блокчейна через TonAPI. Ключ и регистрация не нужны; без ключа это примерно один запрос в секунду, поэтому большое расследование идёт несколько минут.</li>
          <li>Программа показывает путь средств между адресами. Кому принадлежит адрес, знает только биржа или сервис — им и адресуется обращение, которое программа подготовит.</li>
          <li>Адрес, время и результаты хранятся только на этом компьютере.</li>
        </ul>
      </div></div>`;
    const form = document.getElementById("new-form");
    const err = document.getElementById("form-error");
    const fail = (msg) => { err.textContent = msg; err.hidden = false; };
    const toTs = (v) => (v ? Math.floor(new Date(v).getTime() / 1000) : 0);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      err.hidden = true;
      const f = new FormData(form);
      const address = String(f.get("address")).trim();
      if (!address) return fail("Вставьте адрес кошелька.");
      const body = {
        address, title: String(f.get("title")).trim(),
        params: {
          incident_ts: toTs(f.get("incident")), end_ts: toTs(f.get("until")),
          max_depth: f.get("max_depth"), min_ton: f.get("min_ton"), min_jetton: f.get("min_jetton"),
          mode: f.get("mode"), jettons: String(f.get("jettons")).trim(), max_events: f.get("max_events"),
          follow_nft: f.get("follow_nft") === "on", follow_contracts: f.get("follow_contracts") === "on",
          fresh: f.get("fresh") === "on",
        },
      };
      const btn = form.querySelector("[type=submit]");
      btn.disabled = true;
      try { const r = await api("/api/investigations", "POST", body); location.hash = "#/i/" + r.id; }
      catch (e) { fail(e.message); btn.disabled = false; }
    });
    document.getElementById("demo-btn").addEventListener("click", async () => {
      err.hidden = true;
      try { const r = await api("/api/investigations", "POST", { demo: true }); location.hash = "#/i/" + r.id; }
      catch (e) { fail(e.message); }
    });
    form.address.focus();
  }

  /* ---------- расследование: ход работы, ошибка или результат ---------- */
  async function showInvestigation(id) {
    let inv;
    try { inv = await api("/api/investigations/" + id); }
    catch (e) { view.innerHTML = `<div class="page"><h1>Расследование не найдено</h1><p>${esc(e.message)}</p><a class="btn" href="#/new">Начать новое</a></div>`; return; }
    if (location.hash !== "#/i/" + id) return;   // пока ждали ответа, человек ушёл на другую страницу

    if (inv.status === "running") {
      const p = inv.progress || {};
      if (!view.querySelector(".progress")) {
        view.innerHTML = `<div class="progress">
          <h1>Иду по переводам</h1>
          <p class="muted">${esc(invTitle(inv))}. Можно свернуть окно — расследование продолжится.</p>
          <div class="track"><i></i></div>
          <div class="counters">
            <div><b data-c="nodes">0</b><span>адресов проверено</span></div>
            <div><b data-c="transfers">0</b><span>переводов в графе</span></div>
            <div><b data-c="api_calls">0</b><span>запросов к TonAPI</span></div>
            <div><b data-c="elapsed">0</b><span>секунд</span></div>
          </div>
          <p class="small muted">Сейчас: <span class="mono" data-c="current">—</span></p>
          <div class="actions" style="margin-top:22px"><button class="btn" id="stop-btn">Остановить</button></div>
        </div>`;
        document.getElementById("stop-btn").onclick = async (ev) => {
          ev.currentTarget.disabled = true; ev.currentTarget.textContent = "Останавливаю…";
          try { await api(`/api/investigations/${id}/cancel`, "POST", {}); } catch (e) { toast(e.message); }
        };
      }
      for (const k of ["nodes", "transfers", "api_calls", "elapsed", "current"]) {
        const el = view.querySelector(`[data-c="${k}"]`);
        if (el && p[k] != null && p[k] !== "") el.textContent = p[k];
      }
      pollTimer = setTimeout(() => showInvestigation(id), 1000);
      return;
    }

    refreshList();
    if (inv.status !== "done" || !inv.result) {
      const title = inv.status === "cancelled" ? "Расследование остановлено" : "Не получилось завершить";
      view.innerHTML = `<div class="page"><h1>${title}</h1>
        <p>${esc(inv.error || "Результат не сохранён.")}</p>
        <div class="actions"><button class="btn primary" id="again">Запустить снова с теми же параметрами</button>
        <button class="btn danger quiet" id="del">Удалить</button></div></div>`;
      document.getElementById("again").onclick = () => rerun(inv);
      document.getElementById("del").onclick = () => remove(inv);
      return;
    }

    const roleOptions = meta.roles.map((r) => `<option value="${esc(r.code)}">${esc(r.title)}</option>`).join("");
    TT.renderResult(view, inv.result, {
      title: invTitle(inv),
      actionsHtml: `<a class="btn sm" href="/api/investigations/${id}/report.html">Скачать отчёт (HTML)</a>
        <a class="btn sm" href="/api/investigations/${id}/export.zip">Все файлы (ZIP)</a>
        <button class="btn sm" id="again">Повторить</button>
        <button class="btn sm quiet" id="rename">Переименовать</button>
        <button class="btn sm quiet danger" id="del">Удалить</button>`,
      labelFormHtml: `<p class="small muted" style="margin-bottom:10px">Например, биржа подтвердила, что адрес её. Метка сохранится и применится при следующем запуске.</p>
        <form data-label-form>
          <label class="field"><span>Что это за адрес</span><select name="role">${roleOptions}</select></label>
          <label class="field"><span>Название</span><input type="text" name="entity" maxlength="120" placeholder="Bybit, обменник…"></label>
          <label class="field"><span>Откуда вы это знаете</span><input type="text" name="note" maxlength="300" placeholder="ответ поддержки от 02.11"></label>
          <button class="btn sm" type="submit">Сохранить метку</button>
        </form>`,
      onLabelForm: (box, node) => {
        const form = box.querySelector("[data-label-form]");
        if (!form) return;
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const f = new FormData(form);
          try {
            await api("/api/labels", "POST", { address: node.id, role: f.get("role"), entity: f.get("entity"), note: f.get("note") });
            toast("Метка сохранена. Она применится при следующем запуске.", "Запустить заново", () => rerun(inv, true));
          } catch (e) { toast(e.message); }
        });
      },
    });
    document.getElementById("again").onclick = () => rerun(inv);
    document.getElementById("del").onclick = () => remove(inv);
    document.getElementById("rename").onclick = () => {
      const h1 = view.querySelector(".res-head h1");
      h1.innerHTML = `<input type="text" maxlength="120" value="${esc(inv.title || "")}" placeholder="Название расследования" aria-label="Название">`;
      const input = h1.querySelector("input");
      input.focus();
      const save = async () => {
        try { await api(`/api/investigations/${id}/rename`, "POST", { title: input.value.trim() }); } catch (e) { toast(e.message); }
        inv.title = input.value.trim();
        h1.textContent = invTitle(inv);
        refreshList();
      };
      input.addEventListener("blur", save, { once: true });
      input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") input.blur(); });
    };
  }

  async function rerun(inv, immediately) {
    if (inv.is_demo) {
      try { const r = await api("/api/investigations", "POST", { demo: true }); location.hash = "#/i/" + r.id; } catch (e) { toast(e.message); }
      return;
    }
    if (immediately) {
      try { const r = await api("/api/investigations", "POST", { address: inv.address, title: inv.title, params: inv.params }); location.hash = "#/i/" + r.id; }
      catch (e) { toast(e.message); }
      return;
    }
    prefill = inv;
    if (location.hash === "#/new") route(); else location.hash = "#/new";
  }

  function remove(inv) {
    // Удаление нельзя отменить, поэтому сначала просим подтверждение прямо на кнопке.
    const b = document.getElementById("del");
    if (b.dataset.armed) {
      api("/api/investigations/" + inv.id, "DELETE").then(() => { location.hash = "#/new"; refreshList(); }).catch((e) => toast(e.message));
      return;
    }
    b.dataset.armed = "1";
    b.textContent = "Точно удалить?";
    setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; b.textContent = "Удалить"; } }, 4000);
  }

  /* ---------- метки ---------- */
  async function showLabels() {
    let data;
    try { data = await api("/api/labels"); } catch (e) { view.innerHTML = `<div class="page"><h1>Метки</h1><p>${esc(e.message)}</p></div>`; return; }
    const titles = Object.fromEntries(meta.roles.map((r) => [r.code, r.title]));
    view.innerHTML = `<div class="page"><h1>Метки адресов</h1>
      <p>Ваши собственные знания об адресах: «это депозит Bybit», «это обменник». Ручная метка важнее автоматической и применяется ко всем новым расследованиям.</p>
      <div class="card"><h2>Добавить метку</h2>
        <form id="label-form">
          <label class="field"><span>Адрес</span><input type="text" name="address" spellcheck="false" autocomplete="off" placeholder="UQ…, EQ… или 0:…" required></label>
          <div class="row">
            <label class="field"><span>Что это</span><select name="role">${meta.roles.map((r) => `<option value="${esc(r.code)}">${esc(r.title)}</option>`).join("")}</select></label>
            <label class="field"><span>Название</span><input type="text" name="entity" maxlength="120" placeholder="Bybit"></label>
          </div>
          <label class="field"><span>Откуда вы это знаете</span><input type="text" name="note" maxlength="300" placeholder="ответ поддержки, публичная страница биржи…"></label>
          <p class="form-error" id="label-error" role="alert" hidden></p>
          <div><button class="btn primary" type="submit">Сохранить метку</button></div>
        </form></div>
      ${data.items.length ? `<div class="tbl-wrap"><table><thead><tr><th>Адрес</th><th>Что это</th><th>Название</th><th>Основание</th><th></th></tr></thead><tbody>
        ${data.items.map((l) => `<tr><td class="mono">${esc(l.address)}</td><td>${esc(titles[l.role] || l.role)}</td><td>${esc(l.entity)}</td><td>${esc(l.note)}</td>
          <td><button class="btn sm quiet danger" data-del="${esc(l.raw)}">Удалить</button></td></tr>`).join("")}</tbody></table></div>`
        : `<div class="tbl-wrap"><div class="empty"><b>Меток пока нет</b>Метку можно добавить здесь или прямо из карточки адреса на карте потоков.</div></div>`}</div>`;
    const form = document.getElementById("label-form"), err = document.getElementById("label-error");
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      err.hidden = true;
      const f = new FormData(form);
      try { await api("/api/labels", "POST", Object.fromEntries(f)); toast("Метка сохранена"); showLabels(); }
      catch (e) { err.textContent = e.message; err.hidden = false; }
    });
    view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      try { await api("/api/labels/" + encodeURIComponent(b.dataset.del), "DELETE"); toast("Метка удалена"); showLabels(); } catch (e) { toast(e.message); }
    }));
  }

  /* ---------- настройки ---------- */
  function showSettings() {
    view.innerHTML = `<div class="page"><h1>Настройки</h1>
      <p>Программа работает без регистрации. Здесь — то, что можно изменить при желании.</p>
      <div class="card"><h2>Ключ TonAPI — чтобы работало быстрее</h2>
        <p class="muted" style="margin-bottom:14px">Без ключа TonAPI отвечает примерно на один запрос в секунду. Бесплатный ключ с tonconsole.com ускоряет расследования в несколько раз. Ключ хранится только на этом компьютере и отправляется только в TonAPI.</p>
        <form id="key-form">
          <label class="field"><span>Ключ</span><input type="password" name="tonapi_key" autocomplete="off" placeholder="${meta.has_key ? "ключ сохранён — вставьте новый, чтобы заменить" : "вставьте ключ"}"></label>
          <p class="form-error" id="key-error" role="alert" hidden></p>
          <div class="actions"><button class="btn primary" type="submit">Сохранить ключ</button>
          ${meta.has_key ? `<button class="btn quiet danger" type="button" id="key-del">Удалить ключ</button>` : ""}
          <span class="muted small">${meta.has_key ? "Сейчас: ключ используется" : "Сейчас: работа без ключа"}</span></div>
        </form></div>
      <div class="card"><h2>Где лежат данные</h2>
        <p>Расследования, метки и сохранённые ответы TonAPI хранятся в папке</p>
        <p class="mono" style="margin:8px 0">${esc(meta.data_dir || "")}</p>
        <p class="muted">Чтобы стереть всё, закройте программу и удалите эту папку. В интернет эти данные не отправляются.</p></div>
      <div class="card"><h2>О программе</h2>
        <p>${esc(meta.name || "TON Tracer")} ${esc(meta.version || "")} — открытый инструмент для отслеживания украденных средств в сети TON. Источник данных: TonAPI.</p>
        <p class="muted" style="margin-top:8px">Программа предназначена для расследования краж у себя или по просьбе пострадавшего. Она показывает движение средств между адресами и не устанавливает личности владельцев.</p></div></div>`;
    const form = document.getElementById("key-form"), err = document.getElementById("key-error");
    const save = async (key) => {
      err.hidden = true;
      try { const r = await api("/api/settings", "POST", { tonapi_key: key }); meta.has_key = r.has_key; toast(key ? "Ключ сохранён" : "Ключ удалён"); showSettings(); }
      catch (e) { err.textContent = e.message; err.hidden = false; }
    };
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const key = form.tonapi_key.value.trim();
      if (!key) { err.textContent = "Вставьте ключ."; err.hidden = false; return; }
      save(key);
    });
    const del = document.getElementById("key-del");
    if (del) del.onclick = () => save("");
  }

  /* ---------- переключение страниц ---------- */
  function route() {
    clearTimeout(pollTimer);
    drawer.hidden = true;
    document.body.classList.remove("with-drawer");
    const h = location.hash || "#/new";
    const m = h.match(/^#\/i\/(\d+)$/);
    view.innerHTML = "";
    window.scrollTo(0, 0);
    if (m) showInvestigation(m[1]);
    else if (h === "#/labels") showLabels();
    else if (h === "#/settings") showSettings();
    else showNew();
    refreshList();
  }

  document.getElementById("theme-btn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("tt-theme", next); } catch (e) { /* не запомнится до следующего запуска */ }
  });
  window.addEventListener("hashchange", route);
  api("/api/meta").then((m) => { meta = m; }).catch(() => {}).finally(route);
})();
