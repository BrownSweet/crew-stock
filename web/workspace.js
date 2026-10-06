"use strict";
const ws = { cid: null, tab: "timeline", records: [], groups: [] };
const wsNames = {
  timeline: "长期档案",
  disclosure: "财报与电话会",
  promise: "承诺兑现",
  expectations: "预期与指引",
  peers: "行业与竞对",
  decisions: "决策与复盘",
};
const recordNames = {
  business_order: "已确认商业订单",
  disclosure: "披露整理",
  promise: "管理层承诺",
  metric: "经营指标",
  expectation_snapshot: "供应商预期快照",
  thesis_version: "投资逻辑变化",
  investment_decision: "投资决策",
  decision_review: "决策复盘",
  promise_evaluation: "承诺评价",
  record_review: "记录审阅",
  source_document: "原始资料",
  report: "AI 研究报告",
  evidence: "原始证据",
  decision: "报告审阅",
  document: "研究基线",
  submission: "用户观点",
  run: "研究运行",
};
const actionNames = {
  buy: "买入",
  add: "增持",
  reduce: "减持",
  sell: "卖出",
  hold: "持有",
  watch: "观察",
  pass: "放弃",
};
const outcomeNames = {
  fulfilled: "已兑现",
  missed: "未达标",
  partial: "部分兑现",
  delayed: "延期",
  unresolved: "待验证",
  supported: "获得支持",
  refuted: "被证伪",
  actual: "实际",
  consensus: "一致预期",
  guidance: "管理层指引",
  industry: "行业指标",
};
function wsSelect(items, value) {
  const s = el("select");
  for (const [v, label] of items) {
    const o = el("option", "", label);
    o.value = v;
    s.append(o);
  }
  if (value != null) s.value = value;
  return s;
}
function wsField(
  form,
  name,
  label,
  type = "text",
  value = "",
  required = true,
  choices = null,
) {
  const wrap = el("div", "ws-field");
  const id = "ws-" + name;
  const lab = el("label", "", label);
  lab.htmlFor = id;
  const input = choices
    ? wsSelect(choices, value)
    : el(type === "textarea" ? "textarea" : "input");
  if (!choices && type !== "textarea") input.type = type;
  if (!choices) input.value = value;
  input.name = name;
  input.id = id;
  input.required = required;
  if (type === "textarea") input.rows = 3;
  wrap.append(lab, input);
  form.append(wrap);
  return input;
}
function wsForm(title, submit) {
  const root = showDetail(title),
    f = el("form", "ws-form");
  root.append(f);
  const button = el("button", "button dark", "保存记录");
  button.type = "submit";
  const finish = () => {
    f.append(button);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      button.disabled = true;
      try {
        await submit(new FormData(f), f);
        $("#detail-dialog").close();
        await wsLoad();
        toast("记录已保存");
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  };
  return { f, finish, button };
}
function wsEvidenceFields(form, quote = true) {
  const s = wsField(
    form,
    "evidence",
    "来源证据（可按 Ctrl / ⌘ 多选）",
    "text",
    "",
    true,
    [],
  );
  s.multiple = true;
  s.size = 5;
  const evidence = ws.records.filter(
    (r) =>
      r.type === "evidence" && r.acquisition_status === "success" && r.blob,
  );
  const search = wsField(
    form,
    "evidence_search",
    "筛选来源证据（ID、类型或日期）",
    "search",
    "",
    false,
  );
  const renderOptions = () => {
    const selected = new Set([...s.selectedOptions].map((o) => o.value));
    const query = search.value.trim().toLowerCase();
    const candidates = evidence.filter(
      (e) =>
        selected.has(e.id) ||
        `${e.id} ${e.topic || ""} ${e.display_name || ""} ${e.captured_at || ""}`
          .toLowerCase()
          .includes(query),
    );
    s.replaceChildren();
    const visible = [
      ...candidates.filter((e) => selected.has(e.id)),
      ...candidates.filter((e) => !selected.has(e.id)).slice(0, 150),
    ];
    visible.forEach((e) => {
      const option = el(
        "option",
        "",
        `${e.id} · ${e.topic || e.display_name || e.source} · ${date(e.captured_at)}`,
      );
      option.value = e.id;
      option.selected = selected.has(e.id);
      s.append(option);
    });
  };
  search.addEventListener("input", renderOptions);
  renderOptions();
  if (quote) {
    wsField(
      form,
      "source_quote",
      "原文逐字摘录（需与证据原文一致）",
      "textarea",
    );
    form.append(
      el(
        "p",
        "muted",
        "可在证据档案查看全文并复制原句。模型提取与手动记录均需核对确认。",
      ),
    );
  }
}
function wsIDs(fd) {
  return fd.getAll("evidence");
}
function wsTitle(r) {
  return (
    r.data?.title ||
    r.data?.name ||
    r.data?.metric ||
    r.title ||
    r.report?.title ||
    (r.type === "investment_decision" ? actionNames[r.action] : null) ||
    r.display_name ||
    r.id
  );
}
function wsCard(r) {
  const card = el("article", "ws-record");
  const top = el("div", "card-title");
  top.append(
    el("h3", "", wsTitle(r)),
    el("span", "tag", recordNames[r.type] || r.type),
  );
  card.append(top);
  card.append(
    el(
      "p",
      "muted",
      `${r.id} · ${date(r.created_at)}${r.review_status ? " · " + (statuses[r.review_status] || r.review_status) : ""}`,
    ),
  );
  let text =
    r.data?.summary ||
    r.rationale ||
    r.reason ||
    r.outcome ||
    r.note ||
    r.report?.summary ||
    "";
  if (r.type === "thesis_version")
    text = `变化前：${r.before || "尚未建立"}\n变化后：${r.after}\n原因：${r.reason}`;
  if (r.type === "promise")
    text = `${r.data.speaker} · 截止 ${r.data.due_date}\n${r.data.source_quote}\n条件：${r.data.conditions || "原文未列明"}`;
  if (r.type === "metric")
    text = `${outcomeNames[r.data.category]} · ${r.data.period}\n${r.data.value} ${r.data.unit} ${r.data.currency} · ${r.data.basis}`;
  if (r.type === "expectation_snapshot")
    text = `${r.data.period} · 预期 ${r.data.estimate} · 实际 ${r.data.actual ?? "未公布"}\n差额 ${r.data.delta ?? "—"} · ${r.data.provider_assessment || ""}\n${r.data.limitation}`;
  card.append(
    el("p", "ws-preserve", text.slice(0, 700)),
    actionButton("查看与操作 →", () => wsShow(r)),
  );
  return card;
}
function wsEmpty(root, text) {
  root.append(el("p", "ws-empty", text));
}
async function wsLoad() {
  if (!ws.cid) ws.cid = state.companies[0]?.id;
  const chooser = $("#ws-company");
  options(
    chooser,
    state.companies.map((c) => ({
      value: c.id,
      text: c.name_zh_cn || c.display_name,
    })),
  );
  if (ws.cid) chooser.value = ws.cid;
  if (!ws.cid) return;
  const requested = ws.cid;
  $("#ws-content").replaceChildren(el("p", "muted", "正在读取公司档案…"));
  const [data, groups] = await Promise.all([
    api(`/workspace/companies/${requested}`),
    api("/workspace/groups"),
  ]);
  if (requested !== ws.cid) return;
  ws.records = data.records;
  ws.comparisons = data.comparisons;
  ws.promiseChecks = data.promise_checks;
  ws.company = data.company;
  ws.groups = groups;
  wsRender();
}
function wsRender() {
  const root = $("#ws-content");
  root.replaceChildren();
  document
    .querySelectorAll("[data-ws-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.wsTab === ws.tab));
  const toolbar = el("div", "ws-actions");
  root.append(toolbar);
  if (ws.tab === "timeline") {
    toolbar.append(actionButton("导出完整公司档案", wsExport, "button pale"));
    const search = el("input");
    search.placeholder = "搜索事件、内容或记录 ID";
    search.setAttribute("aria-label", "搜索公司时间线");
    toolbar.append(search);
    const thesis = el("section", "ws-callout");
    thesis.append(
      el("h3", "", "当前已确认投资逻辑"),
      el(
        "p",
        "ws-preserve",
        ws.company?.confirmed_thesis?.text || "尚未确认；请先研究并审阅报告。",
      ),
    );
    root.append(thesis);
    const list = el("div", "ws-grid");
    root.append(list);
    const render = () => {
      list.replaceChildren();
      const rows = ws.records.filter(
        (r) =>
          !["evidence", "run", "expectation_snapshot"].includes(r.type) &&
          JSON.stringify(r).toLowerCase().includes(search.value.toLowerCase()),
      );
      rows.forEach((r) => list.append(wsCard(r)));
      if (!rows.length) wsEmpty(list, "暂无符合条件的研究事件。");
    };
    search.addEventListener("input", render);
    render();
    return;
  }
  if (ws.tab === "disclosure") {
    toolbar.append(
      actionButton("导入财报 / 电话会原文", wsImport, "button dark"),
      actionButton("采集最新资料并整理", wsResearch, "button pale"),
      actionButton(
        "解析 PDF / 图片 / Office 文件",
        () => extNavigate("assets", ws.cid),
        "button pale",
      ),
    );
    root.append(
      el(
        "p",
        "muted",
        "自动查找长桥财报与电话会资料，区分全文、节选、新闻摘要；没有逐字稿时不生成虚构问答。支持导入 TXT / Markdown 原文。",
      ),
    );
    wsList(root, ["disclosure", "source_document"]);
    return;
  }
  if (ws.tab === "promise") {
    toolbar.append(
      actionButton(
        "登记管理层承诺",
        () => wsRecordForm("promise"),
        "button dark",
      ),
    );
    for (const check of ws.promiseChecks || []) {
      const item = el("div", "ws-callout");
      item.append(
        el(
          "p",
          "",
          `${check.promise_id} · ${{ numeric_target_met: "数值达到目标", numeric_target_missed: "数值未达目标", awaiting_comparable_actual: "等待实际指标" }[check.status]}：${check.reason}`,
        ),
      );
      root.append(item);
    }
    wsList(root, ["promise", "promise_evaluation"]);
    return;
  }
  if (ws.tab === "expectations") {
    toolbar.append(
      actionButton(
        "登记实际 / 预期 / 指引指标",
        () => wsRecordForm("metric"),
        "button dark",
      ),
      actionButton("比较两条指标", wsCompareForm, "button pale"),
    );
    root.append(
      el(
        "p",
        "muted",
        "只比较同一公司、期间、币种、单位和口径。模型提取的指标需先核对；供应商 Beat/Miss 与本地事前快照分开展示。",
      ),
    );
    for (const c of ws.comparisons || []) root.append(wsComparison(c));
    wsList(root, ["metric"]);
    wsExpectationsTable(root);
    return;
  }
  if (ws.tab === "peers") {
    toolbar.append(
      actionButton("建立行业 / 竞对组", () => wsGroupForm(), "button dark"),
      actionButton(
        "打开独立行业研究中心",
        async () => {
          page("industries");
          await extRefresh();
        },
        "button pale",
      ),
      actionButton(
        "查看公司供应链影响图",
        () => extNavigate("graph", ws.cid),
        "button pale",
      ),
    );
    root.append(
      el(
        "p",
        "muted",
        "每组 2–6 家已有公司。整组研究会逐家采集、分析；持续跟踪可在「持续跟踪」为成员证券设置周期。比较表只合并相同日历期间、币种、单位与口径。",
      ),
    );
    if (!ws.groups.length)
      wsEmpty(root, "先在研究工作台建立竞对公司档案，再创建比较组。");
    for (const g of ws.groups) {
      const c = el("section", "ws-record");
      c.append(
        el("h3", "", g.name),
        el("p", "", g.description),
        el("p", "muted", `${g.company_ids.length} 家公司`),
        actionButton("查看行业比较", () => wsGroupShow(g.id)),
        actionButton("编辑成员", () => wsGroupForm(g)),
      );
      root.append(c);
    }
    return;
  }
  if (ws.tab === "decisions") {
    toolbar.append(
      actionButton("记录一次投资决策", wsDecisionForm, "button dark"),
    );
    root.append(
      el(
        "p",
        "muted",
        "记录行动、替代方案、风险、原始假设和失效条件；保存当时证据及逻辑快照。此处仅记研究日志，不会下单。",
      ),
    );
    wsList(root, ["investment_decision", "decision_review", "thesis_version"]);
  }
}
function wsList(root, kinds) {
  const list = el("div", "ws-grid"),
    rows = ws.records.filter(
      (r) => kinds.includes(r.type) && r.review_status !== "rejected",
    );
  root.append(list);
  if (!rows.length)
    wsEmpty(list, "暂无记录。可发起 AI 研究自动整理，或使用上方按钮添加。");
  rows.slice(0, 150).forEach((r) => list.append(wsCard(r)));
  if (rows.length > 150)
    root.append(
      el(
        "p",
        "muted",
        `共 ${rows.length} 条，当前显示最近 150 条；完整记录可从长期档案导出。`,
      ),
    );
}
async function wsResearch() {
  const c = state.companies.find((x) => x.id === ws.cid);
  await api("/runs", {
    method: "POST",
    body: JSON.stringify({
      company_id: ws.cid,
      symbol: c.securities[0].symbol,
      mode: "research",
      question:
        "整理最新财报与电话会，提取管理层承诺、指引和实际数据；对比历史记录，标注事实、推测、情绪以及投资逻辑变化。",
    }),
  });
  toast("研究已排队，可在运行记录查看进度");
  await refresh();
}
function wsImport() {
  const { f, finish, button } = wsForm("导入原始研究资料", async (fd) => {
    const r = await api("/workspace/documents", {
      method: "POST",
      body: JSON.stringify({
        company_id: ws.cid,
        title: fd.get("title"),
        kind: fd.get("kind"),
        source_url: fd.get("url"),
        published_at: fd.get("published_at") || null,
        text: fd.get("text"),
        analyze: fd.get("analyze") === "yes",
      }),
    });
    if (r.analysis_error)
      toast("原文已保存，分析未排队：" + r.analysis_error, true);
  });
  wsField(f, "title", "资料标题");
  wsField(f, "kind", "资料类型", "text", "earnings_call", true, [
    ["earnings_call", "电话会逐字稿 / 节选"],
    ["financial_report", "财报原文"],
    ["industry_data", "行业数据"],
  ]);
  wsField(f, "url", "原始来源链接（仅记录来源）", "url", "", false);
  wsField(f, "published_at", "原文发布日期（未知可留空）", "date", "", false);
  const file = wsField(f, "file", "或从文本文件读取", "file", "", false);
  file.accept = ".txt,.md,.csv,.json";
  const text = wsField(f, "text", "原文（最多 20 万字）", "textarea");
  text.maxLength = 200000;
  text.rows = 10;
  file.addEventListener("change", async () => {
    if (file.files[0]) {
      if (file.files[0].size > 1000000)
        return toast("文件过大，请截取相关部分", true);
      text.value = await file.files[0].text();
    }
  });
  wsField(f, "analyze", "保存后自动整理", "text", "yes", true, [
    ["yes", "使用 DeepSeek 整理"],
    ["no", "只保存原文"],
  ]);
  button.textContent = "保存原文";
  finish();
}
function wsRecordForm(kind) {
  const { f, finish } = wsForm(
    kind === "promise" ? "登记管理层承诺" : "登记经营指标",
    async (fd) => {
      const data = {
        evidence_ids: wsIDs(fd),
        source_quote: fd.get("source_quote"),
      };
      if (kind === "promise") {
        [
          "title",
          "speaker",
          "promised_at",
          "due_date",
          "conditions",
          "operator",
          "metric",
          "basis",
          "unit",
          "currency",
        ].forEach((k) => (data[k] = fd.get(k) || ""));
        ["target", "period_start", "period_end"].forEach(
          (k) => (data[k] = fd.get(k) || null),
        );
      } else {
        [
          "metric",
          "period",
          "period_start",
          "period_end",
          "basis",
          "unit",
          "currency",
          "value",
          "category",
          "direction",
        ].forEach((k) => (data[k] = fd.get(k)));
        data.published_at = new Date(fd.get("published_at")).toISOString();
      }
      await api("/workspace/records", {
        method: "POST",
        body: JSON.stringify({ company_id: ws.cid, kind, data }),
      });
    },
  );
  if (kind === "promise") {
    wsField(f, "title", "承诺内容");
    wsField(f, "speaker", "管理层发言人");
    wsField(f, "promised_at", "提出日期", "date");
    wsField(f, "due_date", "承诺截止日期", "date");
    wsField(
      f,
      "conditions",
      "适用条件（兑现评价时须同时核查）",
      "textarea",
      "",
      false,
    );
    wsField(f, "operator", "目标判断方式", "text", "qualitative", true, [
      ["qualitative", "定性承诺 / 人工评价"],
      ["at_least", "至少达到"],
      ["at_most", "不超过"],
      ["equal", "等于目标"],
    ]);
    wsField(f, "target", "数值目标（定性承诺可留空）", "text", "", false);
  } else {
    wsField(
      f,
      "category",
      "指标类型",
      "text",
      "actual",
      true,
      Object.entries(outcomeNames).filter(([k]) =>
        ["actual", "consensus", "guidance", "industry"].includes(k),
      ),
    );
    wsField(f, "period", "财务期间标签，如 FY2027 Q2");
    wsField(f, "value", "数值（按原始单位，不加千位分隔符）");
    wsField(
      f,
      "published_at",
      "该数值的原始发布时间（本地时间）",
      "datetime-local",
    );
    wsField(f, "direction", "评价方向", "text", "higher", true, [
      ["higher", "越高越好"],
      ["lower", "越低越好"],
    ]);
  }
  const required = kind === "metric";
  wsField(
    f,
    "metric",
    "指标名（使用一致命名，如 revenue）",
    "text",
    "",
    required,
  );
  wsField(f, "period_start", "对应期间开始", "date", "", required);
  wsField(f, "period_end", "对应期间结束", "date", "", required);
  wsField(f, "basis", "会计 / 统计口径，如 GAAP", "text", "", required);
  wsField(f, "unit", "原始单位，如 元 / 百万美元 / %", "text", "", required);
  wsField(f, "currency", "币种，如 USD；非金额填写 N/A", "text", "", required);
  wsEvidenceFields(f);
  finish();
}
function wsRefs(root, ids) {
  const line = el("div", "ws-actions");
  (ids || []).forEach((id) =>
    line.append(actionButton(id, () => showEvidence(id))),
  );
  root.append(line);
}
function wsShow(r) {
  if (r.type === "business_order") {
    page("orders");
    $("#order-at").value = "";
    return orderLoad()
      .then(() => orderDetail(r))
      .catch((e) => toast(e.message, true));
  }
  if (["report", "document", "submission", "run", "evidence"].includes(r.type))
    return r.type === "evidence" ? showEvidence(r.id) : showObject(r.id);
  const root = showDetail(wsTitle(r));
  root.append(
    el(
      "p",
      "muted",
      `${r.id} · ${recordNames[r.type] || r.type} · ${date(r.created_at)}`,
    ),
  );
  if (r.origin === "model" && r.review_status === "pending_confirmation")
    root.append(
      el(
        "p",
        "ws-callout",
        "AI 从原文提取的建议记录，尚需你核对。确认仅代表已审阅，不是来源已被独立审计。",
      ),
    );
  if (r.review_status === "rejected")
    root.append(
      el("p", "ws-callout", "已驳回：" + (r.review_note || "请查看审阅记录")),
    );
  const d = r.data || r;
  if (r.type === "disclosure") {
    root.append(
      el(
        "p",
        "tag",
        { full_text: "全文", excerpt: "节选", news_summary: "新闻摘要" }[
          d.coverage
        ],
      ),
      el("p", "ws-preserve", d.summary),
    );
    for (const point of d.key_points) root.append(el("p", "", "• " + point));
    root.append(el("h3", "", "管理层问答"));
    if (!d.questions_answers.length)
      root.append(el("p", "muted", "未取得可核实的问答原文。"));
    d.questions_answers.forEach((q) => {
      root.append(
        el("p", "", "问：" + q.question),
        el("p", "", `${q.speaker}：${q.answer}`),
      );
    });
    d.unanswered_questions.forEach((q) =>
      root.append(el("p", "muted", "待核查：" + q)),
    );
  } else if (r.type === "thesis_version") {
    root.append(
      el("h3", "", "变化前"),
      el("p", "ws-preserve", r.before || "尚未建立"),
      el("h3", "", "变化后"),
      el("p", "ws-preserve", r.after),
      el("h3", "", "确认原因"),
      el("p", "", r.reason),
    );
  } else if (r.type === "investment_decision") {
    root.append(
      el("h3", "", `${actionNames[r.action]} · ${date(r.decided_at)}`),
      el("p", "ws-preserve", r.rationale),
      el("p", "", "替代方案：" + r.alternatives),
      el("p", "", "风险：" + r.risks),
    );
    r.assumptions.forEach((a, i) =>
      root.append(
        el(
          "p",
          "ws-callout",
          `${i + 1}. ${a.text}\n失效条件：${a.invalidate_if}\n检查日期：${a.check_date}`,
        ),
      ),
    );
    if (r.retrospective)
      root.append(el("p", "tag amber", "事后补记；部分资料不能证明当时已知"));
    root.append(
      el("p", "muted", "复盘日期：" + r.review_date),
      actionButton(
        "逐项复盘这次决策",
        () => wsDecisionReview(r),
        "button dark",
      ),
      actionButton(
        "让 AI 检查最新证据",
        async () => {
          await api(`/workspace/decisions/${r.id}/analyze`, { method: "POST" });
          toast("复盘研究已排队");
        },
        "button pale",
      ),
    );
    ws.records
      .filter((x) => x.type === "decision_review" && x.decision_id === r.id)
      .forEach((x) => root.append(wsCard(x)));
  } else if (r.type === "decision_review") {
    root.append(
      el("h3", "", "结果"),
      el("p", "", r.outcome),
      el("h3", "", "决策过程质量"),
      el("p", "", r.process_quality),
      el("h3", "", "教训与改进"),
      el("p", "", r.lessons),
    );
    r.assumption_results.forEach((a) =>
      root.append(
        el(
          "p",
          "",
          `假设 ${a.index + 1} · ${outcomeNames[a.status]}：${a.explanation}`,
        ),
      ),
    );
  } else if (r.type === "promise") {
    root.append(
      el(
        "p",
        "ws-preserve",
        `${d.speaker} · ${d.promised_at}\n截止 ${d.due_date}\n适用条件：${d.conditions || "未列明"}`,
      ),
    );
    if (d.target != null)
      root.append(
        el(
          "p",
          "",
          `目标：${d.metric} ${d.operator} ${d.target} ${d.unit} ${d.currency}`,
        ),
      );
    if (r.review_status === "confirmed")
      root.append(
        actionButton(
          "对照实际结果评价",
          () => wsPromiseEvaluate(r),
          "button dark",
        ),
      );
    ws.records
      .filter((x) => x.type === "promise_evaluation" && x.promise_id === r.id)
      .forEach((x) => root.append(wsCard(x)));
  } else if (r.type === "source_document") {
    root.append(
      el("p", "", "用户提供原文；来源尚未独立核验。"),
      actionButton(
        "重新整理原文",
        async () => {
          await api(`/workspace/documents/${r.id}/analyze`, { method: "POST" });
          toast("整理已排队");
        },
        "button pale",
      ),
    );
  } else if (r.type === "promise_evaluation")
    root.append(el("p", "", `${outcomeNames[r.status]} · ${r.note}`));
  else if (r.type === "metric")
    root.append(
      el(
        "p",
        "ws-preserve",
        `${outcomeNames[d.category]} · ${d.period}\n${d.value} ${d.unit} ${d.currency}\n口径 ${d.basis}\n期间 ${d.period_start} — ${d.period_end}\n发布时间 ${date(d.published_at)}`,
      ),
    );
  else if (r.type === "expectation_snapshot")
    root.append(
      el(
        "p",
        "ws-preserve",
        `${d.period}\n预期 ${d.estimate}\n实际 ${d.actual ?? "未公布"}\n差额 ${d.delta ?? "—"}\n${d.limitation}`,
      ),
    );
  if (d.source_quote)
    root.append(el("blockquote", "ws-preserve", d.source_quote));
  wsRefs(root, r.evidence_ids);
  if (
    ["disclosure", "metric", "promise"].includes(r.type) &&
    r.review_status === "pending_confirmation"
  ) {
    const note = el("textarea");
    note.placeholder = "核对原文后填写审阅理由";
    note.setAttribute("aria-label", "审阅理由");
    root.append(note);
    for (const [decision, label] of [
      ["confirmed", "核对并确认"],
      ["rejected", "驳回提取"],
    ])
      root.append(
        actionButton(
          label,
          async () => {
            if (!note.value.trim()) throw new Error("请填写审阅理由");
            await api(`/workspace/records/${r.id}/review`, {
              method: "POST",
              body: JSON.stringify({ decision, note: note.value }),
            });
            await wsLoad();
            wsShow(ws.records.find((x) => x.id === r.id));
          },
          "button pale",
        ),
      );
  }
  const detail = el("details");
  detail.append(
    el("summary", "", "查看完整记录与来源元数据"),
    el("pre", "raw-data", JSON.stringify(r, null, 2)),
  );
  root.append(detail);
}
function wsPromiseEvaluate(r) {
  const { f, finish } = wsForm("评价管理层承诺", async (fd) => {
    await api(`/workspace/promises/${r.id}/evaluate`, {
      method: "POST",
      body: JSON.stringify({
        actual_metric_id: fd.get("actual") || null,
        status: fd.get("status"),
        note: fd.get("note"),
        evidence_ids: wsIDs(fd),
      }),
    });
  });
  f.append(
    el(
      "p",
      "ws-callout",
      `承诺：${r.data.title}\n条件：${r.data.conditions || "未列明"}。请在说明中核查条件是否成立。`,
    ),
  );
  wsField(
    f,
    "actual",
    "对照已确认的实际指标（定性评价可留空）",
    "text",
    "",
    false,
    [
      ["", "使用人工定性评价"],
      ...ws.records
        .filter(
          (x) =>
            x.type === "metric" &&
            x.review_status === "confirmed" &&
            x.data.category === "actual",
        )
        .map((x) => [
          x.id,
          `${x.id} · ${x.data.metric} · ${x.data.period} · ${x.data.value}`,
        ]),
    ],
  );
  wsField(
    f,
    "status",
    "人工评价（选择指标时以程序计算为准）",
    "text",
    "unresolved",
    true,
    Object.entries(outcomeNames).filter(([k]) =>
      ["fulfilled", "missed", "partial", "delayed", "unresolved"].includes(k),
    ),
  );
  wsField(f, "note", "评价依据及条件核查", "textarea");
  wsEvidenceFields(f, false);
  f.elements.evidence.required = false;
  finish();
}
function wsComparison(c) {
  const box = el("div", "ws-callout");
  box.append(
    el(
      "p",
      "",
      c.status === "comparable"
        ? `${c.metric} ${c.period}：${c.before} → ${c.after}；变化 ${c.delta}（${c.percent == null ? "基数为零" : Number(c.percent).toFixed(2) + "%"}）`
        : "不可比：" + c.reason,
    ),
  );
  wsRefs(box, c.evidence_ids);
  return box;
}
function wsCompareForm() {
  const root = showDetail("比较预期修订 / 实际与预期");
  const rows = ws.records.filter((r) => r.type === "metric");
  const choices = rows.map((r) => [
    r.id,
    `${r.id} · ${outcomeNames[r.data.category]} · ${r.data.metric} ${r.data.period} ${r.data.value}`,
  ]);
  const before = wsField(
      root,
      "before",
      "原始预期 / 旧指引",
      "text",
      null,
      true,
      choices,
    ),
    after = wsField(
      root,
      "after",
      "实际结果 / 新指引",
      "text",
      null,
      true,
      choices,
    );
  const result = el("div");
  root.append(
    actionButton(
      "计算可比变化",
      async () => {
        const c = await api("/workspace/compare", {
          method: "POST",
          body: JSON.stringify({
            before_id: before.value,
            after_id: after.value,
          }),
        });
        result.replaceChildren(wsComparison(c));
      },
      "button dark",
    ),
    result,
  );
}
function wsGroupForm(group = null) {
  const { f, finish } = wsForm(
    group ? "编辑行业 / 竞对组" : "建立行业 / 竞对组",
    async (fd) => {
      await api("/workspace/groups" + (group ? "/" + group.id : ""), {
        method: group ? "PUT" : "POST",
        body: JSON.stringify({
          name: fd.get("name"),
          description: fd.get("description"),
          company_ids: fd.getAll("members"),
        }),
      });
    },
  );
  wsField(f, "name", "行业 / 主题名称", "text", group?.name || "");
  wsField(
    f,
    "description",
    "比较目的与关键行业指标",
    "textarea",
    group?.description || "",
    false,
  );
  const members = wsField(
    f,
    "members",
    "选择 2–6 家公司（按 Ctrl / ⌘ 多选）",
    "text",
    "",
    true,
    state.companies.map((c) => [c.id, c.name_zh_cn || c.display_name]),
  );
  members.multiple = true;
  members.size = Math.min(8, state.companies.length);
  for (const option of members.options)
    option.selected = (group?.company_ids || []).includes(option.value);
  finish();
}
async function wsGroupShow(id) {
  const g = await api("/workspace/groups/" + id);
  const root = showDetail(g.group.name);
  root.append(
    el("p", "", g.group.description),
    actionButton(
      "整组采集与研究",
      async () => {
        const result = await api(`/workspace/groups/${id}/runs`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        toast(
          `已排队 ${result.runs.length} 家${result.errors.length ? "；部分未排队：" + result.errors.map((e) => e.error).join("；") : ""}`,
        );
      },
      "button dark",
    ),
  );
  for (const m of g.members) {
    const card = el("section", "ws-record");
    card.append(el("h3", "", m.company.name_zh_cn || m.company.display_name));
    m.reports.forEach((r) =>
      card.append(
        actionButton(`${date(r.created_at)} ${r.report.title}`, () =>
          showObject(r.id),
        ),
      ),
    );
    if (!m.metrics.length)
      card.append(el("p", "muted", "暂无已确认、可比较的指标。"));
    wsRefs(
      card,
      m.industry_evidence.map((e) => e.id),
    );
    root.append(card);
  }
  root.append(el("h3", "", "同口径指标对照"));
  if (!g.aligned_metrics.length)
    wsEmpty(root, "暂无可对齐指标；先研究并确认提取结果，或登记指标。");
  for (const m of g.aligned_metrics) {
    const card = el("div", "ws-callout");
    card.append(
      el(
        "h4",
        "",
        `${m.metric} · ${m.period_start} — ${m.period_end} · ${m.currency} ${m.unit} ${m.basis}`,
      ),
    );
    for (const member of g.members) {
      const r = m.members[member.company.id];
      card.append(
        el(
          "p",
          "",
          `${member.company.display_name}：${r ? r.data.value : "缺少同口径数据"}`,
        ),
      );
      if (r) wsRefs(card, r.evidence_ids);
    }
    root.append(card);
  }
}
function wsDecisionForm() {
  const { f, finish } = wsForm("记录投资决策", async (fd) => {
    const assumptions = [...f.querySelectorAll(".ws-assumption")].map(
      (row) => ({
        text: row.querySelector("[name=assumption]").value,
        invalidate_if: row.querySelector("[name=invalidate]").value,
        check_date: row.querySelector("[name=check_date]").value,
      }),
    );
    await api("/workspace/decisions", {
      method: "POST",
      body: JSON.stringify({
        company_id: ws.cid,
        action: fd.get("action"),
        decided_at: new Date(fd.get("decided_at")).toISOString(),
        retrospective: fd.get("retrospective") === "yes",
        rationale: fd.get("rationale"),
        alternatives: fd.get("alternatives"),
        risks: fd.get("risks"),
        assumptions,
        review_date: fd.get("review_date"),
        evidence_ids: wsIDs(fd),
      }),
    });
  });
  wsField(
    f,
    "action",
    "决策行动（仅日志，不下单）",
    "text",
    "watch",
    true,
    Object.entries(actionNames),
  );
  wsField(f, "decided_at", "实际决策时间（本地时间）", "datetime-local");
  wsField(f, "retrospective", "记录方式", "text", "no", true, [
    ["no", "当时记录（校验资料已入库）"],
    ["yes", "事后补记（明确标记后见信息）"],
  ]);
  wsField(f, "rationale", "为什么作出这个决定", "textarea");
  wsField(f, "alternatives", "考虑过哪些替代方案", "textarea");
  wsField(f, "risks", "已知风险与不确定性", "textarea");
  wsField(f, "review_date", "计划复盘日期", "date");
  const list = el("div");
  f.append(list);
  function add() {
    const row = el("fieldset", "ws-assumption");
    row.append(el("legend", "", "原始假设 " + (list.children.length + 1)));
    const id = list.children.length;
    for (const [name, label, type] of [
      ["assumption", "假设内容", "textarea"],
      ["invalidate", "什么情况说明假设失效", "textarea"],
      ["check_date", "检查日期", "date"],
    ]) {
      const input = wsField(row, name, label, type);
      input.id += "-" + id;
      input.previousElementSibling?.setAttribute("for", input.id);
    }
    list.append(row);
  }
  add();
  f.append(
    actionButton("＋ 增加一个假设", () => {
      if (list.children.length < 20) add();
    }),
  );
  wsEvidenceFields(f, false);
  finish();
}
function wsDecisionReview(r) {
  const { f, finish } = wsForm("复盘原始假设与决策过程", async (fd) => {
    const assumption_results = r.assumptions.map((a, index) => ({
      index,
      status: fd.get("status-" + index),
      explanation: fd.get("explanation-" + index),
    }));
    await api(`/workspace/decisions/${r.id}/reviews`, {
      method: "POST",
      body: JSON.stringify({
        observed_at: fd.get("observed_at"),
        outcome: fd.get("outcome"),
        process_quality: fd.get("process_quality"),
        lessons: fd.get("lessons"),
        assumption_results,
        evidence_ids: wsIDs(fd),
      }),
    });
  });
  wsField(
    f,
    "observed_at",
    "截至哪一天的观察",
    "date",
    new Date().toISOString().slice(0, 10),
  );
  r.assumptions.forEach((a, i) => {
    f.append(
      el(
        "p",
        "ws-callout",
        `假设 ${i + 1}：${a.text}\n失效条件：${a.invalidate_if}`,
      ),
    );
    wsField(f, "status-" + i, "该假设目前如何", "text", "unresolved", true, [
      ["unresolved", "尚待验证"],
      ["supported", "得到支持"],
      ["refuted", "被证伪"],
    ]);
    wsField(f, "explanation-" + i, "依据与不确定性", "textarea");
  });
  wsField(
    f,
    "outcome",
    "实际结果（可记录收益，但不以收益代替过程评价）",
    "textarea",
  );
  wsField(f, "process_quality", "当时的决策过程质量", "textarea");
  wsField(f, "lessons", "复盘教训与下一次改进", "textarea");
  wsEvidenceFields(f, false);
  finish();
}
async function wsExport() {
  const data = await api(`/workspace/export/${ws.cid}`);
  const link = el("a");
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  link.href = url;
  link.download = `${ws.cid}-research-archive.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
labels.workspace = "公司研究中心";
$("#ws-company").addEventListener("change", () => {
  ws.cid = $("#ws-company").value;
  wsLoad().catch((e) => toast(e.message, true));
});
document.querySelectorAll("[data-ws-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    ws.tab = b.dataset.wsTab;
    wsRender();
  }),
);
document
  .querySelectorAll('[data-page="workspace"]')
  .forEach((b) =>
    b.addEventListener("click", () =>
      wsLoad().catch((e) => toast(e.message, true)),
    ),
  );
$("#ws-refresh").addEventListener("click", () =>
  wsLoad().catch((e) => toast(e.message, true)),
);

function wsExpectationsTable(root) {
  const snapshots = ws.records.filter((r) => r.type === "expectation_snapshot");
  if (!snapshots.length) return;
  root.append(
    el("h3", "", "长桥一致预期快照"),
    el(
      "p",
      "muted",
      "下表使用供应商原始数值。未披露币种或预测形成时间时，不能据此进行跨公司比较，也不视为本地事前预测。",
    ),
  );
  const rank = (p) => {
    const m = p.match(/Q(\d) (\d{4})/);
    return m ? Number(m[2]) * 10 + Number(m[1]) : 0;
  };
  const periods = [...new Set(snapshots.map((r) => r.data.period))].sort(
    (a, b) => rank(b) - rank(a),
  );
  const released = snapshots
    .filter((r) => r.data.is_released)
    .sort((a, b) => rank(b.data.period) - rank(a.data.period));
  const select = wsSelect(
    periods.map((p) => [p, p]),
    released[0]?.data.period || periods[0],
  );
  select.setAttribute("aria-label", "供应商预期财务期间");
  root.append(select);
  const target = el("div", "table-scroll");
  root.append(target);
  const fmt = (value) =>
    value == null
      ? "未公布"
      : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  const render = () => {
    target.replaceChildren();
    const table = el("table"),
      head = el("thead"),
      tr = el("tr");
    ["指标", "预期", "实际", "差额", "供应商判断", "证据"].forEach((t) =>
      tr.append(el("th", "", t)),
    );
    head.append(tr);
    table.append(head);
    const body = el("tbody"),
      seen = new Set();
    snapshots
      .filter((r) => r.data.period === select.value)
      .forEach((r) => {
        if (seen.has(r.data.metric)) return;
        seen.add(r.data.metric);
        const row = el("tr");
        [
          r.data.name,
          fmt(r.data.estimate),
          fmt(r.data.actual),
          fmt(r.data.delta),
          r.data.provider_assessment || "—",
        ].forEach((t) => row.append(el("td", "", t)));
        const refs = el("td");
        refs.append(actionButton(r.id, () => wsShow(r)));
        row.append(refs);
        body.append(row);
      });
    table.append(body);
    target.append(table);
  };
  select.addEventListener("change", render);
  render();
}
