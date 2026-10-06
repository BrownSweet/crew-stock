"use strict";
const $ = (s) => document.querySelector(s);
const basePath = $('meta[name="crew-base-path"]').content;
const apiURL = (path) => basePath + "api" + path;
let authenticated = false;
let needsSetup = false;

function showLogin() {
  authenticated = false;
  state.page = "overview";
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  document.body.classList.add("signed-out");
  $("#login-title").textContent = needsSetup ? "创建管理员账号" : "登录研究工作台";
  $("#login-hint").textContent = needsSetup
    ? "账号为 3–64 位字母、数字或 _ . @ -；密码至少 12 个字符。"
    : "输入管理员账号和密码，继续研究。";
  $("#login-form").hidden = false;
  $("#login-confirm-group").hidden = !needsSetup;
  $("#login-confirm").required = needsSetup;
  $("#login-submit").textContent = needsSetup ? "创建管理员并进入" : "登录";
  $("#login-password").minLength = needsSetup ? 12 : 1;
  $("#login-password").autocomplete = needsSetup ? "new-password" : "current-password";
}

async function enterWorkspace(session) {
  authenticated = true;
  $("#account-name").textContent = session.username;
  $("#login-password").value = "";
  $("#login-confirm").value = "";
  document.body.classList.remove("signed-out");
  await Promise.all([refresh(), refreshStatus()]);
}

async function initializeLogin() {
  $("#login-error").textContent = "";
  $("#login-retry").hidden = true;
  try {
    needsSetup = !(await api("/auth/status")).initialized;
    if (needsSetup) return showLogin();
    try {
      await enterWorkspace(await api("/auth/session"));
    } catch {
      showLogin();
    }
  } catch (e) {
    $("#login-title").textContent = "暂时无法连接工作台";
    $("#login-error").textContent = e.message;
    $("#login-retry").hidden = false;
  }
}
const state = {
  companies: [],
  evidence: [],
  runs: [],
  reports: [],
  docs: [],
  submissions: [],
  schedules: [],
  status: null,
  page: "overview",
};
const labels = {
  overview: "研究工作台",
  evidence: "证据档案",
  runs: "运行记录",
  submissions: "观点与批注",
  schedules: "持续跟踪",
  settings: "连接设置",
};
const statuses = {
  queued: "等待执行",
  collecting: "采集中",
  analyzing: "AI 研究中",
  completed: "已完成",
  completed_with_warnings: "完成 · 有缺口",
  failed: "失败",
  interrupted: "已中断",
  success: "采集成功",
  empty: "无数据",
  pending_confirmation: "待你确认",
  confirmed: "已确认",
  rejected: "已驳回",
};
const classes = {
  fact: "事实",
  inference: "推测",
  sentiment: "市场情绪",
  opinion: "外部观点",
  user_annotation: "用户批注",
};
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function date(value) {
  if (!value) return "日期未提供";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}
function badge(value) {
  const color = ["success", "completed", "confirmed"].includes(value)
    ? "green"
    : ["failed", "interrupted", "rejected"].includes(value)
      ? "red"
      : "amber";
  return el("span", `tag ${color}`, statuses[value] || value);
}
function toast(message, error = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = error ? "error" : "";
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 6000);
}
async function api(path, options = {}) {
  const response = await fetch(apiURL(path), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Crew-Stock": "1",
      ...options.headers,
    },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务器返回异常，请检查容器是否运行");
  }
  if (!response.ok) {
    if (response.status === 401 && authenticated) showLogin();
    const d = data.detail;
    throw new Error(
      typeof d === "string"
        ? d
        : Array.isArray(d)
          ? d.map((x) => x.msg).join("；")
          : "请求失败",
    );
  }
  return data;
}
function actionButton(text, fn, cls = "link-button") {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", () =>
    Promise.resolve(fn()).catch((e) => toast(e.message, true)),
  );
  return b;
}
function page(name) {
  state.page = name;
  document
    .querySelectorAll(".page")
    .forEach((x) => (x.hidden = x.id !== "page-" + name));
  document
    .querySelectorAll(".nav")
    .forEach((x) => x.classList.toggle("active", x.dataset.page === name));
  $("#page-label").textContent = labels[name];
  window.scrollTo({ top: 0 });
}
document
  .querySelectorAll("[data-page]")
  .forEach((x) => x.addEventListener("click", () => page(x.dataset.page)));
document
  .querySelectorAll("[data-close]")
  .forEach((x) =>
    x.addEventListener("click", () => x.closest("dialog").close()),
  );
document.querySelectorAll("dialog").forEach((x) =>
  x.addEventListener("click", (e) => {
    if (e.target === x) {
      const r = x.getBoundingClientRect();
      if (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      )
        x.close();
    }
  }),
);
function options(select, items, placeholder = null) {
  const value = select.value;
  select.replaceChildren();
  if (placeholder) select.add(new Option(placeholder, ""));
  for (const item of items) select.add(new Option(item.text, item.value));
  if (items.some((x) => x.value === value)) select.value = value;
}
function fillSymbols(companySelect, symbolSelect) {
  const c = state.companies.find((c) => c.id === $(companySelect).value);
  options(
    $(symbolSelect),
    (c?.securities || []).map((s) => ({ value: s.symbol, text: s.symbol })),
  );
}
$("#research-company").addEventListener("change", () =>
  fillSymbols("#research-company", "#research-symbol"),
);
$("#schedule-company").addEventListener("change", () =>
  fillSymbols("#schedule-company", "#schedule-symbol"),
);
$("#evidence-filter").addEventListener("change", renderEvidence);
$("#new-company").addEventListener("click", () =>
  $("#company-dialog").showModal(),
);
function empty(target, text) {
  target.replaceChildren(el("div", "empty", text));
}
function renderOverview() {
  const total = state.reports.filter(
    (r) => r.review_status === "pending_confirmation",
  ).length;
  const values = [
    ["研究中的公司", state.companies.length, "公司与证券分别建档", "◈"],
    ["已保存证据", state.evidence.length, "原始资料，可回溯来源", "▤"],
    [
      "研究报告",
      state.reports.length + state.docs.length,
      "积累每个时点的判断",
      "↗",
    ],
    ["待确认判断", total, "投资逻辑由你最终确认", "◷"],
  ];
  $("#stats").replaceChildren(
    ...values.map(([title, value, note, icon]) => {
      const c = el("div", "stat"),
        top = el("div", "stat-top", title);
      top.append(el("span", "stat-icon", icon));
      c.append(
        top,
        el("div", "stat-value", String(value).padStart(2, "0")),
        el("div", "stat-note", note),
      );
      return c;
    }),
  );
  $("#company-count").textContent = state.companies.length + " 家公司";
  const list = $("#company-list");
  list.replaceChildren();
  if (!state.companies.length)
    empty(list, "还没有公司档案。先添加你想持续跟踪的公司。");
  for (const c of state.companies) {
    const item = el("div", "company-item"),
      top = el("div", "company-top"),
      info = el("div", "company-info"),
      actions = el("div", "company-actions");
    info.append(
      el("strong", "", c.name_zh_cn || c.display_name),
      el(
        "div",
        "company-ticker",
        c.securities.map((s) => s.symbol).join(" · ") + "  /  " + c.id,
      ),
    );
    top.append(
      el(
        "div",
        "company-logo",
        c.securities[0]?.symbol === "NVDA.US" ? "N" : c.display_name[0],
      ),
      info,
      el("span", "tag green", c.historical ? "已有历史基线" : "持续研究"),
    );
    actions.append(
      el(
        "span",
        "muted",
        c.confirmed_thesis ? "投资逻辑已确认" : "尚未确认投资逻辑",
      ),
      actionButton("进入研究 ↗", () => {
        $("#research-company").value = c.id;
        fillSymbols("#research-company", "#research-symbol");
        $("#research-question").focus();
      }),
    );
    item.append(
      top,
      el("p", "company-purpose", c.research_purpose || "长期研究档案"),
      actions,
    );
    list.append(item);
  }
  const reportList = $("#report-list");
  reportList.replaceChildren();
  const records = [...state.reports, ...state.docs].sort((a, b) =>
    (b.created_at || "").localeCompare(a.created_at || ""),
  );
  if (!records.length)
    empty(reportList, "尚无研究报告。采集证据后，发起第一次研究。");
  for (const r of records) {
    const row = el("div", "record-row"),
      info = el("div", "record-content"),
      meta = el("div", "record-meta");
    info.append(el("div", "record-title", r.report?.title || r.display_name));
    meta.append(
      el("span", "", r.id),
      el("span", "", date(r.created_at)),
      el(
        "span",
        "",
        r.historical ? "历史资料 · 非实时" : "DeepSeek · " + r.model,
      ),
    );
    info.append(meta);
    row.append(
      el("div", "record-icon", "▤"),
      info,
      badge(r.review_status || "历史基线"),
      actionButton("↗", () => showObject(r.id)),
    );
    reportList.append(row);
  }
}
function renderEvidence() {
  const target = $("#evidence-list"),
    filter = $("#evidence-filter").value,
    c = state.companies.find((x) => x.id === filter);
  const parents = c ? [c.id, ...c.securities.map((s) => s.id)] : null;
  const items = state.evidence.filter(
    (e) => !parents || parents.includes(e.parent_id),
  );
  if (!items.length)
    return empty(target, "暂无证据。运行采集后，原始资料将在这里留存。");
  const wrap = el("div", "table-wrap"),
    table = el("table"),
    thead = el("thead"),
    tr = el("tr");
  ["证据 / 来源", "时间截面", "采集结果", "变化", "原始记录"].forEach((t) =>
    tr.append(el("th", "", t)),
  );
  thead.append(tr);
  table.append(thead);
  const tbody = el("tbody");
  for (const e of items) {
    const row = el("tr"),
      name = el("td"),
      time = el("td"),
      result = el("td"),
      delta = el("td"),
      action = el("td");
    name.append(
      el("strong", "", e.display_name || e.topic),
      el("small", "", e.id + " · " + (e.transport || e.source)),
    );
    time.append(
      el("div", "", date(e.captured_at || e.created_at)),
      el(
        "small",
        "",
        e.historical
          ? "历史资料"
          : e.as_of
            ? "业务日期 " + e.as_of
            : "业务时间见原始数据",
      ),
    );
    result.append(badge(e.acquisition_status || "未标注"));
    delta.textContent =
      e.changed === true ? "结构变化" : e.changed === false ? "内容相同" : "—";
    action.append(actionButton("查看 ↗", () => showEvidence(e.id)));
    row.append(name, time, result, delta, action);
    tbody.append(row);
  }
  table.append(tbody);
  wrap.append(table);
  target.replaceChildren(wrap);
}
function renderRuns() {
  const list = $("#run-list");
  list.replaceChildren();
  if (!state.runs.length)
    return empty(list, "还没有运行记录。开始研究后可在这里查看进度。");
  for (const r of state.runs) {
    const row = el("div", "record-row"),
      info = el("div", "record-content"),
      meta = el("div", "record-meta");
    info.append(
      el(
        "div",
        "record-title",
        r.display_name ||
          r.symbol +
            " · " +
            ({
              research: "最新数据研究",
              collect: "长桥证据采集",
              archive: "历史资料研究",
            }[r.mode] || r.mode),
      ),
    );
    meta.append(
      el("span", "", r.id),
      el("span", "", date(r.created_at)),
      el(
        "span",
        "",
        r.current_step || (r.historical ? "历史运行" : "等待开始"),
      ),
      el("span", "", (r.evidence_ids?.length || 0) + " 条证据"),
    );
    info.append(meta);
    if (r.error) info.append(el("div", "run-error", r.error));
    row.append(
      el("div", "record-icon", "↗"),
      info,
      badge(r.status),
      actionButton("查看", () => showObject(r.report_id || r.id)),
    );
    list.append(row);
  }
}
function renderSubmissions() {
  const list = $("#submission-list");
  list.replaceChildren(el("div", "card-title", "已保存的观点"));
  if (!state.submissions.length) {
    list.append(el("div", "empty", "还没有观点。原文与批注会分别保存。"));
    return;
  }
  for (const s of state.submissions) {
    const row = el("div", "record-row"),
      info = el("div", "record-content");
    info.append(
      el("div", "record-title", s.display_name || s.id),
      el(
        "div",
        "record-meta",
        s.id + " · " + date(s.created_at) + " · 待核实资料",
      ),
    );
    row.append(
      info,
      actionButton("查看", () => showObject(s.id)),
    );
    list.append(row);
  }
}
function renderSchedules() {
  const list = $("#schedule-list");
  list.replaceChildren(el("div", "card-title", "跟踪计划"));
  if (!state.schedules.length) {
    list.append(el("div", "empty", "尚未创建计划。创建后由你手动启用。"));
    return;
  }
  for (const s of state.schedules) {
    const row = el("div", "record-row"),
      info = el("div", "record-content");
    info.append(
      el(
        "div",
        "record-title",
        s.symbol + " · 每 " + s.interval_hours + " 小时",
      ),
      el(
        "div",
        "record-meta",
        (s.enabled ? "下次执行 " + date(s.next_at) : "已暂停") +
          " · " +
          (s.mode === "collect" ? "只采集" : "AI 研究"),
      ),
    );
    if (s.last_error) info.append(el("div", "run-error", s.last_error));
    row.append(
      info,
      actionButton(
        s.enabled ? "暂停" : "启用",
        async () => {
          await api("/schedules/" + s.id, {
            method: "PUT",
            body: JSON.stringify({ enabled: !s.enabled }),
          });
          await refresh();
          toast(s.enabled ? "计划已暂停" : "计划已启用");
        },
        "button pale",
      ),
    );
    list.append(row);
  }
}
async function refreshStatus() {
  state.status = await api("/status");
  const { deepseek: d, longbridge: l } = state.status;
  $("#setup-banner").hidden = d.configured && l.authenticated;
  $("#setup-text").textContent =
    [
      !d.configured ? "DeepSeek 待配置" : null,
      !l.authenticated ? "长桥容器待授权" : null,
    ]
      .filter(Boolean)
      .join(" · ") + "。历史资料现在即可查看。";
  $("#settings-dot").hidden = d.configured && l.authenticated;
  $("#deepseek-state").textContent = d.configured
    ? "已保存 · 可测试"
    : "待配置";
  $("#longbridge-state").textContent = l.authenticated
    ? "已授权 · 可测试"
    : l.status === "not_found"
      ? "待授权"
      : l.status;
  $("#model").value = d.model;
  $("#base-url").value = d.base_url;
}
async function refresh() {
  const [companies, evidence, runs, reports, docs, submissions, schedules] =
    await Promise.all([
      api("/companies"),
      api("/objects?kind=evidence"),
      api("/objects?kind=run"),
      api("/objects?kind=report"),
      api("/objects?kind=document"),
      api("/objects?kind=submission"),
      api("/objects?kind=schedule"),
    ]);
  Object.assign(state, {
    companies,
    evidence,
    runs,
    reports,
    docs,
    submissions,
    schedules,
  });
  const items = companies.map((c) => ({
    value: c.id,
    text: c.name_zh_cn || c.display_name,
  }));
  ["#research-company", "#submission-company", "#schedule-company"].forEach(
    (s) => options($(s), items),
  );
  options($("#evidence-filter"), items, "全部公司");
  fillSymbols("#research-company", "#research-symbol");
  fillSymbols("#schedule-company", "#schedule-symbol");
  renderOverview();
  renderEvidence();
  renderRuns();
  renderSubmissions();
  renderSchedules();
}
function showDetail(title) {
  $("#detail-title").textContent = title;
  const target = $("#detail-content");
  target.replaceChildren();
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
  return target;
}
async function showEvidence(id) {
  const e = await api("/objects/" + id);
  const target = showDetail(e.display_name || id);
  target.append(
    el(
      "div",
      "details-grid",
      `${id} · ${e.source} / ${e.transport || "form"} · 采集于 ${date(e.captured_at)}`,
    ),
  );
  if (e.historical)
    target.append(
      el(
        "div",
        "detail-warning",
        "这是历史采集资料，不能视为当前行情或最新财务信息。",
      ),
    );
  if (e.error) target.append(el("p", "run-error", e.error));
  target.append(el("pre", "", JSON.stringify(e, null, 2)));
  if (e.blob) {
    const response = await fetch(apiURL("/evidence/" + id + "/raw"));
    if (!response.ok) throw new Error("原始证据读取失败");
    const raw = await response.text();
    if ($("#detail-title").textContent === (e.display_name || id)) {
      target.append(el("h3", "", "原始载荷"), el("pre", "", raw));
    }
  }
}
function reportSection(target, title, items) {
  const section = el("section", "report-section");
  section.append(el("h3", "", title));
  if (Array.isArray(items)) {
    const ul = el("ul");
    for (const i of items) ul.append(el("li", "", i));
    section.append(items.length ? ul : el("p", "", "未列出"));
  } else section.append(el("p", "", items));
  target.append(section);
}
async function showObject(id) {
  const obj = await api("/objects/" + id);
  if (obj.type === "evidence") return showEvidence(id);
  const target = showDetail(obj.report?.title || obj.display_name || id);
  if (obj.historical)
    target.append(
      el(
        "div",
        "detail-warning",
        "历史档案 · 来源日期保留于原始记录，不代表当前情况。",
      ),
    );
  if (obj.type === "report") {
    const r = obj.report;
    target.append(
      badge(obj.review_status),
      el(
        "p",
        "muted",
        obj.id + " · " + obj.model + " · " + date(obj.created_at),
      ),
      el("div", "report-summary", r.summary),
    );
    const claims = el("section", "report-section");
    claims.append(el("h3", "", "结论与证据"));
    for (const c of r.claims) {
      const claim = el("div", "claim");
      claim.append(
        el("span", "tag green", classes[c.classification]),
        el("p", "", c.text),
        el(
          "div",
          "muted",
          "业务日期：" + (c.as_of || "未提供") + " · 置信度：" + c.confidence,
        ),
      );
      for (const eid of c.evidence_ids)
        claim.append(actionButton(eid + " ↗", () => showEvidence(eid)));
      claims.append(claim);
    }
    target.append(claims);
    reportSection(target, "本次变化", r.changes);
    reportSection(target, "反方证据", r.counter_evidence);
    reportSection(target, "数据缺口", r.gaps);
    reportSection(target, "待确认投资逻辑", r.thesis_proposal);
    reportSection(target, "下一步追问", r.next_questions);
    if (obj.review_status === "pending_confirmation") {
      const area = el("div", "review-area"),
        label = el("label", "", "审阅说明（必填）"),
        note = el("textarea");
      note.rows = 3;
      note.maxLength = 4000;
      note.id = "review-note";
      label.htmlFor = note.id;
      const buttons = el("div", "button-row");
      for (const [decision, text] of [
        ["confirmed", "确认此研究判断"],
        ["rejected", "驳回建议"],
      ])
        buttons.append(
          actionButton(
            text,
            async () => {
              if (!note.value.trim()) throw new Error("请填写审阅说明");
              await api("/reports/" + id + "/review", {
                method: "POST",
                body: JSON.stringify({ decision, note: note.value.trim() }),
              });
              await showObject(id);
              await refresh();
              toast("审阅已保存，历史记录保留");
            },
            decision === "confirmed" ? "button dark" : "button pale",
          ),
        );
      area.append(label, note, buttons);
      target.append(area);
    } else reportSection(target, "审阅说明", obj.review_note || "");
  } else {
    if (obj.markdown) target.append(markdownView(obj.markdown));
    if (obj.text) reportSection(target, "原文", obj.text);
    if (obj.annotation) reportSection(target, "你的批注", obj.annotation);
    if (obj.type === "run" && obj.evidence_ids) {
      const refs = el("div", "report-section");
      refs.append(el("h3", "", "本次证据"));
      obj.evidence_ids.forEach((eid) =>
        refs.append(actionButton(eid + " ↗", () => showEvidence(eid))),
      );
      target.append(refs);
    }
    if (obj.attachment_ids) {
      for (const aid of obj.attachment_ids) {
        const image = el("img", "attachment");
        image.src = apiURL("/attachments/" + encodeURIComponent(aid));
        image.alt = "历史投稿截图 " + aid;
        image.loading = "lazy";
        target.append(image);
      }
    }
    const details = el("details", "record-details");
    details.append(
      el("summary", "", "查看来源与元数据"),
      el("pre", "", JSON.stringify({ ...obj, markdown: undefined }, null, 2)),
    );
    target.append(details);
  }
}
function form(id, handler) {
  $(id).addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = e.submitter || $(id).querySelector("button");
    if (b) b.disabled = true;
    try {
      await handler();
    } catch (err) {
      toast(err.message, true);
    } finally {
      if (b) b.disabled = false;
    }
  });
}
form("#company-form", async () => {
  await api("/companies", {
    method: "POST",
    body: JSON.stringify({
      name: $("#company-name").value.trim(),
      symbols: $("#company-symbols")
        .value.split(/[,，\s]+/)
        .filter(Boolean),
      purpose: $("#company-purpose").value,
    }),
  });
  $("#company-dialog").close();
  $("#company-form").reset();
  await refresh();
  toast("公司档案已建立");
});
form("#research-form", async () => {
  const r = await api("/runs", {
    method: "POST",
    body: JSON.stringify({
      company_id: $("#research-company").value,
      symbol: $("#research-symbol").value,
      mode: $("#research-mode").value,
      question: $("#research-question").value,
    }),
  });
  await refresh();
  page("runs");
  toast(r.id + " 已进入研究队列");
});
form("#settings-form", async () => {
  await api("/settings", {
    method: "PUT",
    body: JSON.stringify({
      api_key: $("#api-key").value,
      model: $("#model").value,
      base_url: $("#base-url").value,
    }),
  });
  $("#api-key").value = "";
  await refreshStatus();
  toast("配置已保存，请测试连接");
});
form("#auth-form", async () => {
  $("#longbridge-result").textContent = "正在授权，请稍候…";
  try {
    await api("/longbridge/authorize", {
      method: "POST",
      body: JSON.stringify({ code: $("#auth-code").value }),
    });
    $("#auth-code").value = "";
    await refreshStatus();
    $("#longbridge-result").textContent = "授权已完成，请测试行情连接。";
  } catch (e) {
    $("#longbridge-result").textContent = e.message;
    throw e;
  }
});
for (const [button, path, result] of [
  ["#test-deepseek", "/settings/test-deepseek", "#deepseek-result"],
  ["#test-longbridge", "/longbridge/test", "#longbridge-result"],
]) {
  $(button).addEventListener("click", async () => {
    $(button).disabled = true;
    $(result).textContent = "正在测试…";
    try {
      const r = await api(path, { method: "POST" });
      $(result).textContent =
        "连接成功 · " + (r.model || r.symbol) + " · " + date(r.checked_at);
    } catch (e) {
      $(result).textContent = e.message;
    } finally {
      $(button).disabled = false;
    }
  });
}
form("#submission-form", async () => {
  await api("/submissions", {
    method: "POST",
    body: JSON.stringify({
      company_id: $("#submission-company").value,
      text: $("#submission-text").value,
      source_url: $("#submission-url").value,
      annotation: $("#submission-note").value,
    }),
  });
  $("#submission-text").value = "";
  $("#submission-note").value = "";
  $("#submission-url").value = "";
  await refresh();
  toast("原文与批注已分别保存");
});
form("#schedule-form", async () => {
  await api("/schedules", {
    method: "POST",
    body: JSON.stringify({
      company_id: $("#schedule-company").value,
      symbol: $("#schedule-symbol").value,
      interval_hours: Number($("#schedule-interval").value),
      mode: $("#schedule-mode").value,
    }),
  });
  await refresh();
  toast("计划已创建，当前为暂停状态");
});
let refreshing = false;
async function poll() {
  if (!authenticated || refreshing || document.hidden) return;
  refreshing = true;
  try {
    await refresh();
  } catch (e) {
    toast(e.message, true);
  } finally {
    refreshing = false;
  }
}
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  if (needsSetup && $("#login-password").value !== $("#login-confirm").value) {
    $("#login-error").textContent = "两次输入的密码不一致";
    return;
  }
  $("#login-submit").disabled = true;
  try {
    const session = await api(needsSetup ? "/auth/setup" : "/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#login-username").value.trim(), password: $("#login-password").value }),
    });
    needsSetup = false;
    await enterWorkspace(session);
  } catch (e) {
    $("#login-error").textContent = e.message;
    if (needsSetup) {
      try { needsSetup = !(await api("/auth/status")).initialized; showLogin(); } catch { /* Preserve connection error. */ }
    }
  } finally {
    $("#login-submit").disabled = false;
  }
});
$("#login-retry").addEventListener("click", initializeLogin);
$("#logout").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); window.location.reload(); }
  catch (e) { toast(e.message, true); }
});
initializeLogin();
setInterval(poll, 6000);

// Small text-only Markdown viewer: never parses HTML from research materials.
function markdownView(text) {
  const root = el("article", "markdown-body");
  const inline = (node, value) => {
    for (const part of value.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)) {
      if (part.startsWith("`") && part.endsWith("`")) {
        const value = part.slice(1, -1);
        node.append(
          /^evd-\d{6}$/.test(value)
            ? actionButton(value, () => showEvidence(value))
            : el("code", "", value),
        );
      } else if (part.startsWith("**") && part.endsWith("**"))
        node.append(el("strong", "", part.slice(2, -2)));
      else node.append(document.createTextNode(part));
    }
  };
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    if (line.startsWith("|")) {
      const wrap = el("div", "table-wrap"),
        table = el("table");
      let first = true;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i]
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|");
        if (!cells.every((c) => /^[\s:-]+$/.test(c))) {
          const row = el("tr");
          for (const cell of cells) {
            const td = el(first ? "th" : "td");
            inline(td, cell.trim());
            row.append(td);
          }
          table.append(row);
          first = false;
        }
        i++;
      }
      i--;
      wrap.append(table);
      root.append(wrap);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const node = heading
      ? el("h" + Math.min(heading[1].length + 1, 6))
      : el(line.startsWith(">") ? "blockquote" : "p");
    inline(node, heading ? heading[2] : line.replace(/^>\s?/, ""));
    root.append(node);
  }
  return root;
}
