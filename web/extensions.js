"use strict";
Object.assign(labels, {
  industries: "行业研究中心",
  assets: "文件与图片",
  graph: "供应链影响图",
});
Object.assign(statuses, {
  parsing: "解析中",
  running: "成员研究中",
  summarizing: "行业汇总中",
});
const ext = {
  industries: [],
  companies: [],
  graph: null,
  source: null,
  simulation: null,
};
const factorLabels = {
  criticality: "输入关键性",
  substitutability: "难以替代程度",
  concentration: "供应集中度",
  buffer_risk: "库存与交期缓冲不足",
  exposure: "经济暴露",
  geopolitical_risk: "地缘与监管风险",
};
const weightLabels = {
  human_confirmed: "人工确认",
  model_proposed: "AI 建议",
  user_proposed: "待审核",
  rejected: "已驳回",
};
function extObjects() {
  return [...ext.industries, ...ext.companies];
}
function extName(id) {
  return extObjects().find((x) => x.id === id)?.display_name || id;
}
async function extLoad() {
  [ext.industries, ext.companies] = await Promise.all([
    api("/industries"),
    api("/companies"),
  ]);
  options(
    $("#industry-select"),
    ext.industries.map((x) => ({ value: x.id, text: x.display_name })),
  );
  for (const id of ["#asset-target", "#graph-scope"])
    options(
      $(id),
      extObjects().map((x) => ({
        value: x.id,
        text: (x.type === "industry" ? "行业 · " : "公司 · ") + x.display_name,
      })),
      id === "#asset-target" ? "全部资料 · 上传后自动识别归类" : null,
    );
}
function extForm(title, submit) {
  const root = showDetail(title),
    f = el("form", "ws-form");
  root.append(f);
  const b = el("button", "button dark", "保存");
  b.type = "submit";
  return {
    f,
    b,
    finish() {
      f.append(b);
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        b.disabled = true;
        try {
          await submit(new FormData(f));
          $("#detail-dialog").close();
          await extRefresh();
          toast("操作已完成");
        } catch (err) {
          toast(err.message, true);
        } finally {
          b.disabled = false;
        }
      });
    },
  };
}
async function extRefresh() {
  await extLoad();
  if (state.page === "industries") await industryRender();
  if (state.page === "assets") await assetRender();
  if (state.page === "graph") await graphLoad();
  if (ext.pendingSimulation) {
    simulationShow(ext.pendingSimulation);
    ext.pendingSimulation = null;
  }
}
async function industryForm(ind = null) {
  await extLoad();
  const { f, finish } = extForm(
    ind ? "编辑行业档案" : "建立行业档案",
    async (fd) => {
      const members = fd
        .getAll("member")
        .map((cid) => ({ company_id: cid, role: fd.get("role-" + cid) || "" }));
      const result = await api("/industries" + (ind ? "/" + ind.id : ""), {
        method: ind ? "PUT" : "POST",
        body: JSON.stringify({
          display_name: fd.get("name"),
          description: fd.get("description"),
          watch_points: fd.get("watch"),
          members,
        }),
      });
      await extLoad();
      $("#industry-select").value = result.id;
    },
  );
  wsField(f, "name", "行业名称", "text", ind?.display_name || "");
  wsField(
    f,
    "description",
    "行业范围与说明",
    "textarea",
    ind?.description || "",
    false,
  );
  wsField(
    f,
    "watch",
    "持续观察重点",
    "textarea",
    ind?.watch_points || "供需、价格、库存、产能、技术路线和竞争格局",
    false,
  );
  f.append(
    el(
      "p",
      "muted",
      "选择已有独立公司；一家公司可以属于多个行业。不同公司的证券请分别建档。",
    ),
  );
  for (const c of ext.companies) {
    const box = el("fieldset", "ws-assumption"),
      label = el("label"),
      input = el("input");
    input.type = "checkbox";
    input.name = "member";
    input.value = c.id;
    input.checked = !!ind?.members?.some((m) => m.company_id === c.id);
    label.append(input, document.createTextNode(" " + c.display_name));
    box.append(label);
    wsField(
      box,
      "role-" + c.id,
      "行业角色 · " + c.display_name,
      "text",
      ind?.members?.find((m) => m.company_id === c.id)?.role || "",
      false,
    );
    f.append(box);
  }
  finish();
}
async function industryRender() {
  const root = $("#industry-content"),
    iid = $("#industry-select").value;
  root.replaceChildren();
  if (!iid) {
    root.append(el("p", "muted", "先建立行业，再选择要持续观察的公司。"));
    return;
  }
  const ind = await api("/industries/" + iid);
  if ($("#industry-select").value !== iid) return;
  const actions = el("div", "ws-toolbar");
  actions.append(
    actionButton("编辑行业与成员", () => industryForm(ind), "button pale"),
    actionButton("整行业采集与研究", () => industryRunForm(ind), "button dark"),
    actionButton(
      "设置行业跟踪计划",
      () => industryScheduleForm(ind),
      "button pale",
    ),
    actionButton(
      "查看影响图",
      () => extNavigate("graph", ind.id),
      "button pale",
    ),
    actionButton(
      "导入行业资料",
      () => extNavigate("assets", ind.id),
      "button pale",
    ),
  );
  root.append(
    actions,
    el("p", "", ind.description || "尚未填写行业说明"),
    el("p", "", "观察重点：" + (ind.watch_points || "尚未填写")),
  );
  const grid = el("div", "ws-grid");
  for (const c of ind.companies) {
    const card = el("article", "ws-record");
    card.append(
      el("h3", "", c.display_name),
      el("p", "", c.role || "角色待补充"),
      el("p", "muted", c.securities.map((s) => s.symbol).join(" · ")),
      actionButton("打开公司档案", async () => {
        page("workspace");
        ws.cid = c.id;
        await wsLoad();
      }),
    );
    for (const r of c.latest_reports)
      card.append(actionButton(r.report.title, () => showObject(r.id)));
    grid.append(card);
  }
  root.append(grid);
  for (const s of ind.schedules)
    root.append(
      el(
        "p",
        "muted",
        `行业计划：${s.enabled ? "已启用" : "暂停"} · 每 ${s.interval_hours} 小时 · 下次 ${date(s.next_at)}${s.last_error ? " · " + s.last_error : ""}`,
      ),
    );
  root.append(el("h2", "", "行业研究记录"));
  for (const r of ind.reports) {
    const c = el("article", "ws-record");
    c.append(
      el("h3", "", r.report.title),
      el("p", "muted", date(r.created_at) + " · AI 综合判断，待核查"),
      el("p", "", r.report.summary),
      actionButton("阅读报告与证据", () => industryReport(r)),
    );
    root.append(c);
  }
  for (const r of ind.runs) {
    const c = el("section", "ws-record");
    c.append(
      badge(r.status),
      el(
        "p",
        "",
        `${r.id} · ${r.next_member}/${r.members.length} 家已调度 · ${date(r.created_at)}`,
      ),
    );
    if (r.error) c.append(el("p", "run-error", r.error));
    for (const e of r.errors || [])
      c.append(
        el("p", "run-error", (e.company_id || e.run_id) + "：" + e.error),
      );
    for (const rid of r.run_ids)
      c.append(actionButton(rid, () => showObject(rid)));
    root.append(c);
  }
}
function industryReport(r) {
  const root = showDetail(r.report.title);
  reportSection(root, "行业摘要", r.report.summary);
  for (const c of r.report.claims) {
    const card = el("section", "ws-record");
    card.append(
      el("b", "", classes[c.classification] || c.classification),
      el("p", "", c.text),
    );
    for (const id of c.evidence_ids)
      card.append(actionButton(id, () => showEvidence(id)));
    root.append(card);
  }
  for (const [key, label] of [
    ["changes", "变化"],
    ["counter_evidence", "反方证据"],
    ["gaps", "资料缺口"],
    ["next_questions", "下一步核查"],
  ])
    reportSection(root, label, r.report[key]);
}
function industryRunForm(ind) {
  const { f, finish, b } = extForm("发起行业研究", async (fd) => {
    await api(`/industries/${ind.id}/runs`, {
      method: "POST",
      body: JSON.stringify({
        question: fd.get("question"),
        mode: fd.get("mode"),
      }),
    });
  });
  wsField(
    f,
    "question",
    "研究问题",
    "textarea",
    ind.watch_points ||
      "跟踪供需、价格、产能、竞争格局及变化，明确反方证据和资料缺口。",
  );
  wsField(f, "mode", "任务类型", "text", "research", true, [
    ["research", "成员研究并生成行业报告"],
    ["collect", "只采集各公司证据"],
  ]);
  f.append(
    el(
      "p",
      "muted",
      "逐家执行，每家公司独立保存资料；AI 研究及行业汇总会产生模型费用。",
    ),
  );
  b.textContent = "加入研究队列";
  finish();
}
function industryScheduleForm(ind) {
  const s = ind.schedules[0],
    { f, finish } = extForm("行业持续观察", async (fd) => {
      await api(`/industries/${ind.id}/schedule`, {
        method: "PUT",
        body: JSON.stringify({
          interval_hours: Number(fd.get("hours")),
          mode: fd.get("mode"),
          question: fd.get("question"),
          enabled: fd.get("enabled") === "yes",
        }),
      });
    });
  const h = wsField(
    f,
    "hours",
    "间隔小时数",
    "number",
    s?.interval_hours || 168,
  );
  h.min = 1;
  h.max = 720;
  wsField(
    f,
    "question",
    "每轮观察问题",
    "textarea",
    s?.question || ind.watch_points || "跟踪行业供需和竞争格局变化",
  );
  wsField(f, "mode", "任务类型", "text", s?.mode || "research", true, [
    ["research", "采集、公司研究与行业报告"],
    ["collect", "只采集证据"],
  ]);
  wsField(f, "enabled", "计划状态", "text", s?.enabled ? "yes" : "no", true, [
    ["no", "暂停"],
    ["yes", "启用"],
  ]);
  f.append(
    el(
      "p",
      "muted",
      "仅容器运行期间执行。每轮使用当时的成员名单；成员变更不改写历史研究。",
    ),
  );
  finish();
}
async function extNavigate(name, id) {
  page(name);
  await extLoad();
  $(name === "graph" ? "#graph-scope" : "#asset-target").value = id;
  if (name === "graph") await graphLoad();
  else await assetRender();
}
function assetForm() {
  const id = $("#asset-target").value;
  const { f, finish, b } = extForm("上传文件与图片", async (fd) => {
    fd.set("target_id", id);
    const response = await fetch(apiURL("/assets"), {
      method: "POST",
      headers: { "X-Crew-Stock": "1" },
      body: fd,
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "上传失败，请检查类型、页码及连接设置",
      );
  });
  f.append(
    el(
      "p",
      "",
      id
        ? "预先关联：" + extName(id)
        : "独立上传：AI 识别正文中的公司、行业和资料类型；不确定的归属留待确认。",
    ),
  );
  const file = wsField(f, "file", "选择文件", "file");
  file.accept =
    ".pdf,.png,.jpg,.jpeg,.webp,.gif,.docx,.xlsx,.txt,.md,.csv,.json";
  for (const [key, label, value] of [
    ["start_page", "PDF 起始页", 1],
    ["end_page", "PDF 结束页", 10],
  ]) {
    const n = wsField(f, key, label, "number", value);
    n.min = 1;
  }
  f.append(
    el(
      "p",
      "muted",
      "原件保存在本地数据卷；所选 PDF 页面/图片或文档文字将发送至 DeepSeek 解析。默认 PDF 前 10 页，可选连续最多 20 页。Word/Excel 读取文字表格，嵌入图片请单独上传；公式不执行。",
    ),
  );
  b.textContent = "上传并使用 AI 解析";
  finish();
}
async function assetRender() {
  const root = $("#asset-content"),
    id = $("#asset-target").value;
  const jobs = await api("/assets?target_id=" + id);
  if ($("#asset-target").value !== id) return;
  root.replaceChildren();
  if (!jobs.length) root.append(el("p", "muted", "尚未上传资料。"));
  for (const a of jobs) {
    const card = el("article", "ws-record");
    card.append(
      el("h3", "", a.filename),
      badge(a.status),
      el(
        "p",
        "muted",
        `${date(a.created_at)} · 原件 ${a.original_blob.slice(0, 12)} · 模型 ${a.model}`,
      ),
    );
    if (a.total_pages)
      card.append(
        el(
          "p",
          "muted",
          `已解析 ${a.pages?.length || 0} / ${a.total_pages} 页或工作表${a.coverage === "selected_pages" ? " · 仅选定页，非全文" : ""}`,
        ),
      );
    if (a.error) card.append(el("p", "run-error", a.error));
    const download = el("a", "text-link", "下载原件");
    download.href = apiURL(`/assets/${a.id}/original`);
    download.download = a.filename;
    card.append(download);
    if (["failed", "interrupted"].includes(a.status))
      card.append(
        actionButton("重试解析", async () => {
          await api(`/assets/${a.id}/retry`, { method: "POST" });
          await assetRender();
        }),
      );
    for (const p of a.pages || []) {
      card.append(
        el("h4", "", "页面 / 工作表：" + p.page_or_sheet),
        el("p", "", p.summary),
      );
      for (const u of p.uncertainties)
        card.append(el("p", "muted", "待核对：" + String(u)));
      card.append(
        actionButton("查看解析证据 " + p.evidence_id, () =>
          showEvidence(p.evidence_id),
        ),
      );
    }
    if (a.source_document_id) {
      const targets = a.target_ids || (a.parent_id ? [a.parent_id] : []);
      card.append(
        el(
          "p",
          "",
          "实际关联：" + (targets.map(extName).join("、") || "待归档"),
        ),
        el(
          "p",
          "muted",
          "AI 归类：" +
            (statuses[a.classification_status] ||
              a.classification_status ||
              "尚未识别"),
        ),
        actionButton("核对 / 调整归属", () => assetLinksForm(a)),
        actionButton("识别订单与履约变化", () =>
          orderExtractForm(a.evidence_ids),
        ),
        actionButton("重新识别归类", async () => {
          await api(`/assets/${a.id}/classify`, { method: "POST" });
          await assetRender();
        }),
      );
      if (a.classification_error)
        card.append(el("p", "run-error", a.classification_error));
      if (a.classification) {
        const c = a.classification;
        card.append(
          el("h4", "", c.title),
          el(
            "p",
            "muted",
            (assetKinds[c.document_type] || c.document_type) +
              " · " +
              c.tags.join("、"),
          ),
        );
        for (const m of c.matches) {
          card.append(
            el(
              "p",
              "",
              `${m.name} · ${m.target_type === "company" ? "公司" : "行业"} · ${Math.round(m.confidence * 100)}% 置信度 · ${m.target_id ? "已有档案候选" : "新对象，待建立档案"}：${m.reason}`,
            ),
            el("blockquote", "", m.quote),
            actionButton("核对来源 " + m.evidence_id, () =>
              showEvidence(m.evidence_id),
            ),
          );
        }
        for (const gap of c.gaps) card.append(el("p", "muted", "缺口：" + gap));
        card.append(el("p", "muted", c.coverage));
      }
      for (const cid of targets.filter((x) =>
        ext.companies.some((c) => c.id === x),
      ))
        card.append(
          actionButton("基于此资料研究 · " + extName(cid), async () => {
            await api(
              `/workspace/documents/${a.source_document_id}/analyze?company_id=${cid}`,
              { method: "POST" },
            );
            toast("研究已加入队列，可在运行记录查看");
          }),
        );
    }
    root.append(card);
  }
}
const assetKinds = {
  financial_report: "财务报告",
  earnings_call: "电话会",
  research_report: "研究报告",
  news: "新闻",
  industry_data: "行业数据",
  other: "其他资料",
};
async function assetLinksForm(a) {
  await extLoad();
  const { f, finish } = extForm("核对文件归属", async (fd) => {
    const ids = fd.getAll("target");
    if (a.parent_id && !ids.includes(a.parent_id)) ids.push(a.parent_id);
    await api(`/assets/${a.id}/links`, {
      method: "PUT",
      body: JSON.stringify({ target_ids: ids, note: fd.get("note") }),
    });
  });
  f.append(
    el(
      "p",
      "muted",
      "一份原件可关联多个档案；调整归属保留历史记录。新对象请先到公司/行业中心建档，再在此关联。人工核对优先于后续 AI 建议。",
    ),
  );
  const selected = a.target_ids || (a.parent_id ? [a.parent_id] : []);
  for (const target of extObjects()) {
    const label = el("label", "ws-assumption"),
      input = el("input");
    input.type = "checkbox";
    input.name = "target";
    input.value = target.id;
    input.checked = selected.includes(target.id);
    input.disabled = target.id === a.parent_id;
    label.append(
      input,
      document.createTextNode(
        " " +
          (target.type === "industry" ? "行业 · " : "公司 · ") +
          target.display_name,
      ),
    );
    f.append(label);
  }
  wsField(
    f,
    "note",
    "归属核对说明",
    "textarea",
    "已根据正文核对资料涉及的研究对象",
  );
  finish();
}
function relationForm(relation = null) {
  const objects = [
      ...extObjects(),
      ...ext.companies.flatMap((c) => c.securities || []),
    ],
    choices = objects.map((c) => [c.id, c.display_name || c.symbol || c.id]);
  const { f, finish } = extForm(
    relation ? "修改关系（保留旧版本，重新审核）" : "登记有证据的关系",
    async (fd) => {
      const scores = {};
      for (const key of Object.keys(factorLabels))
        scores[key] = {
          value: Number(fd.get(key)),
          reason: fd.get(key + "-reason"),
        };
      const payload = {
        upstream_id: fd.get("upstream"),
        downstream_id: fd.get("downstream"),
        relation_type: fd.get("kind"),
        input_category: fd.get("category"),
        rationale: fd.get("rationale"),
        as_of: fd.get("as_of"),
        confidence: Number(fd.get("confidence")),
        scores,
        citations: [
          { evidence_id: fd.get("evidence"), quote: fd.get("quote") },
          ...(relation?.citations.slice(1) || []),
        ],
      };
      await api("/relations" + (relation ? "/" + relation.id : ""), {
        method: relation ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
    },
  );
  wsField(
    f,
    "upstream",
    "影响来源",
    "text",
    relation?.upstream_id || ext.source,
    true,
    choices,
  );
  wsField(
    f,
    "downstream",
    "受影响对象",
    "text",
    relation?.downstream_id,
    true,
    choices,
  );
  wsField(
    f,
    "kind",
    "关系类型",
    "text",
    relation?.relation_type || "supply",
    true,
    [
      ["supply", "供应影响"],
      ["demand", "需求影响"],
      ["dependency", "运营依赖"],
    ],
  );
  wsField(
    f,
    "category",
    "供应品类 / 依赖环节",
    "text",
    relation?.input_category || "",
  );
  wsField(
    f,
    "rationale",
    "关系成立的理由",
    "textarea",
    relation?.rationale || "",
  );
  wsField(f, "as_of", "证据业务日期", "date", relation?.as_of || "");
  const confidence = wsField(
    f,
    "confidence",
    "置信度 0–1",
    "number",
    relation?.confidence ?? 0.5,
  );
  confidence.min = 0;
  confidence.max = 1;
  confidence.step = 0.01;
  f.append(
    el(
      "p",
      "muted",
      "下面六项均表示风险强度，越高表示传导越强；评分和置信度是研究假设。保存后仍需审核。",
    ),
  );
  for (const [k, label] of Object.entries(factorLabels)) {
    const n = wsField(
      f,
      k,
      label + " 0–1",
      "number",
      relation?.scores[k].value ?? 0.5,
    );
    n.min = 0;
    n.max = 1;
    n.step = 0.01;
    wsField(
      f,
      k + "-reason",
      label + "评分依据",
      "textarea",
      relation?.scores[k].reason || "",
    );
  }
  const ev = state.evidence.filter(
    (e) => e.blob && e.acquisition_status === "success",
  );
  wsField(
    f,
    "evidence",
    "来源证据",
    "text",
    relation?.citations[0].evidence_id,
    true,
    ev.map((e) => [e.id, e.id + " · " + (e.display_name || e.topic)]),
  );
  wsField(
    f,
    "quote",
    "对应证据原文逐字摘录",
    "textarea",
    relation?.citations[0].quote || "",
  );
  finish();
}
async function relationDetail(e) {
  const root = showDetail(
    "关系：" + extName(e.upstream_id) + " → " + extName(e.downstream_id),
  );
  root.append(
    el(
      "p",
      "",
      `${weightLabels[e.weight_status]} · 权重 ${e.dependency_weight} · 置信度 ${e.confidence} · 证据日期 ${e.as_of}`,
    ),
    el("p", "", e.rationale),
  );
  for (const [k, v] of Object.entries(e.scores))
    root.append(el("p", "", `${factorLabels[k]}：${v.value} — ${v.reason}`));
  for (const c of e.citations) {
    root.append(
      el("blockquote", "", c.quote),
      actionButton(c.evidence_id, () => showEvidence(c.evidence_id)),
    );
  }
  if (e.review_note) root.append(el("p", "", "审核依据：" + e.review_note));
  const note = wsField(
    root,
    "review-note",
    "审核依据 / 驳回原因",
    "textarea",
    "",
    false,
  );
  if (["user_proposed", "model_proposed"].includes(e.weight_status))
    for (const [value, label] of [
      ["human_confirmed", "确认此关系与权重"],
      ["rejected", "驳回建议"],
    ])
      root.append(
        actionButton(
          label,
          async () => {
            await api(`/relations/${e.id}/review`, {
              method: "POST",
              body: JSON.stringify({ decision: value, note: note.value }),
            });
            $("#detail-dialog").close();
            await graphLoad();
          },
          "button pale",
        ),
      );
  root.append(
    actionButton("修改并提交新版本", () => relationForm(e), "button pale"),
  );
  const versions = await api(`/relations/${e.id}/versions`);
  const d = el("details");
  d.append(
    el("summary", "", `查看 ${versions.length} 个历史版本`),
    el("pre", "", JSON.stringify(versions, null, 2)),
  );
  root.append(d);
}
function graphSuggest() {
  const id = $("#graph-scope").value;
  if (!id) return;
  const { f, finish, b } = extForm("从证据提取关系建议", async (fd) => {
    await api(`/graph/${id}/suggest`, {
      method: "POST",
      body: JSON.stringify({ evidence_ids: fd.getAll("evidence") }),
    });
  });
  const s = wsField(
    f,
    "evidence",
    "选择来源证据（Ctrl / ⌘ 多选，最多 15 份）",
    "text",
    "",
    true,
    state.evidence
      .filter((e) => e.blob && e.acquisition_status === "success")
      .map((e) => [e.id, e.id + " · " + (e.display_name || e.topic)]),
  );
  s.multiple = true;
  s.size = 10;
  f.append(
    el(
      "p",
      "muted",
      "AI 仅依据所选证据建议关系和评分；没有来源不会补造关系，所有建议需人工审核后才参与模拟。",
    ),
  );
  b.textContent = "提取关系建议";
  finish();
}
async function graphLoad() {
  const id = $("#graph-scope").value;
  if (!id) {
    $("#graph-status").textContent = "先建立公司或行业档案。";
    return;
  }
  const [data, jobs] = await Promise.all([
    api(
      `/graph/${id}?depth=${$("#graph-depth").value}&proposals=${$("#graph-proposals").checked}`,
    ),
    api(`/graph/${id}/jobs`),
  ]);
  if ($("#graph-scope").value !== id) return;
  ext.graph = data;
  ext.simulation = null;
  if (!data.nodes.some((n) => n.id === ext.source)) ext.source = id;
  const status = $("#graph-status");
  status.replaceChildren(
    el(
      "p",
      "muted",
      `${data.nodes.length} 个节点 · ${data.edges.length} 条运营关系。灰色行业成员线仅表示归属，不参与冲击计算。拖动节点调整位置，点击节点选择冲击源，点击关系查看证据。`,
    ),
  );
  for (const j of jobs.slice(0, 3))
    status.append(
      el(
        "p",
        "muted",
        `${j.id}：${statuses[j.status] || j.status}${j.error ? " · " + j.error : ""}${j.gaps?.length ? " · " + j.gaps.join("；") : ""}`,
      ),
    );
  graphDraw();
  const history = $("#graph-history");
  history.replaceChildren(el("h3", "", "历史情景（保留参数与关系版本）"));
  for (const s of data.simulations)
    history.append(
      actionButton(s.id + " · " + date(s.created_at), () => simulationShow(s)),
    );
}
function svgEl(name, attrs = {}, text = null) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text !== null) n.textContent = text;
  return n;
}
function graphDraw() {
  const data = ext.graph,
    host = $("#graph-canvas");
  if (!data) return;
  host.replaceChildren();
  const svg = svgEl("svg", {
      viewBox: "0 0 1100 700",
      role: "img",
      "aria-label": "公司和行业供应链有向影响图",
    }),
    defs = svgEl("defs"),
    marker = svgEl("marker", {
      id: "graph-arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 7,
      markerHeight: 7,
      orient: "auto-start-reverse",
    });
  marker.append(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#517260" }));
  defs.append(marker);
  svg.append(defs);
  host.append(svg);
  const positions = new Map(
    data.nodes.map((n, i) => [
      n.id,
      {
        x: 550 + Math.cos((2 * Math.PI * i) / data.nodes.length) * 370,
        y: 350 + Math.sin((2 * Math.PI * i) / data.nodes.length) * 250,
      },
    ]),
  );
  let dragged = null;
  const render = () => {
    svg.querySelectorAll(".graph-layer").forEach((n) => n.remove());
    const layer = svgEl("g", { class: "graph-layer" });
    svg.append(layer);
    const activeEdges = new Set(
      ext.simulation?.paths.flatMap((p) => p.relation_ids) || [],
    );
    for (const e of [
      ...data.membership.map((m) => ({
        upstream_id: m.source,
        downstream_id: m.target,
        member: true,
      })),
      ...data.edges,
    ]) {
      const a = positions.get(e.upstream_id),
        b = positions.get(e.downstream_id);
      if (!a || !b) continue;
      const dx = b.x - a.x,
        dy = b.y - a.y,
        len = Math.hypot(dx, dy) || 1;
      const line = svgEl("line", {
        x1: a.x + (dx / len) * 48,
        y1: a.y + (dy / len) * 22,
        x2: b.x - (dx / len) * 55,
        y2: b.y - (dy / len) * 25,
        stroke: e.member
          ? "#b7beb8"
          : activeEdges.has(e.id)
            ? "#bd7042"
            : "#517260",
        "stroke-width": e.member ? 1 : 2 + e.dependency_weight * 4,
        "stroke-dasharray": e.member
          ? "3 6"
          : e.weight_status === "human_confirmed"
            ? "none"
            : "8 5",
      });
      if (!e.member) {
        line.setAttribute("marker-end", "url(#graph-arrow)");
        line.setAttribute("tabindex", "0");
        line.setAttribute("role", "button");
        line.setAttribute("aria-label", e.input_category + "关系详情");
        line.addEventListener("click", () =>
          relationDetail(e).catch((err) => toast(err.message, true)),
        );
        line.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") line.dispatchEvent(new Event("click"));
        });
      }
      layer.append(line);
      if (!e.member)
        layer.append(
          svgEl(
            "text",
            {
              x: (a.x + b.x) / 2,
              y: (a.y + b.y) / 2 - 7,
              "text-anchor": "middle",
              class: "graph-edge-label",
            },
            e.input_category + " " + e.dependency_weight.toFixed(2),
          ),
        );
    }
    for (const n of data.nodes) {
      const p = positions.get(n.id),
        g = svgEl("g", {
          transform: `translate(${p.x} ${p.y})`,
          tabindex: 0,
          role: "button",
          "aria-label": "选择冲击源 " + (n.display_name || n.id),
          class: "graph-node",
        }),
        impact = ext.simulation?.impacts[n.id];
      g.append(
        svgEl("rect", {
          x: -88,
          y: -30,
          width: 176,
          height: 60,
          rx: 14,
          fill: impact
            ? `hsl(${impact < 0 ? 26 : 130} 55% ${95 - Math.min(1, Math.abs(impact)) * 42}%)`
            : n.id === ext.source
              ? "#315944"
              : "#edf2e9",
          stroke: "#71937c",
          "stroke-width": n.id === ext.source ? 3 : 1,
        }),
        svgEl(
          "text",
          {
            "text-anchor": "middle",
            y: -3,
            fill: n.id === ext.source && !impact ? "white" : "#233a2c",
          },
          (n.display_name || n.id).slice(0, 18),
        ),
        svgEl(
          "text",
          {
            "text-anchor": "middle",
            y: 17,
            fill: n.id === ext.source && !impact ? "white" : "#567360",
            "font-size": 11,
          },
          impact != null ? `情景暴露 ${(impact * 100).toFixed(1)}%` : n.id,
        ),
      );
      const select = () => {
        ext.source = n.id;
        $("#graph-detail").replaceChildren(
          el("p", "", "已选择冲击源：" + (n.display_name || n.id)),
          actionButton("查看节点档案", () => showObject(n.id)),
        );
        render();
      };
      g.addEventListener("click", select);
      g.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") select();
      });
      g.addEventListener("pointerdown", (ev) => {
        select();
        dragged = n.id;
        svg.setPointerCapture(ev.pointerId);
      });
      layer.append(g);
    }
  };
  svg.addEventListener("pointermove", (ev) => {
    if (!dragged) return;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(
      matrix.inverse(),
    );
    positions.set(dragged, {
      x: Math.max(90, Math.min(1010, point.x)),
      y: Math.max(35, Math.min(665, point.y)),
    });
    render();
  });
  svg.addEventListener("pointerup", () => {
    dragged = null;
  });
  svg.addEventListener("pointercancel", () => {
    dragged = null;
  });
  render();
  graphZoom();
}
function graphZoom() {
  const svg = $("#graph-canvas svg");
  if (svg) {
    const scale = Number($("#graph-zoom").value);
    svg.setAttribute("width", (1100 * scale) / 100);
    svg.setAttribute("height", (700 * scale) / 100);
  }
}
function simulationForm() {
  if (!ext.graph) return;
  const { f, finish, b } = extForm("设置冲击情景", async (fd) => {
    const s = await api("/simulations", {
      method: "POST",
      body: JSON.stringify({
        scope_id: $("#graph-scope").value,
        source_id: fd.get("source"),
        shock: Number(fd.get("shock")) / 100,
        horizon_days: Number(fd.get("days")),
        time_factor: Number(fd.get("time")),
        damping: Number(fd.get("damping")),
        buffer: Number(fd.get("buffer")),
        max_depth: Number(fd.get("depth")),
        assumptions: fd.get("assumptions"),
      }),
    });
    ext.pendingSimulation = s;
  });
  wsField(
    f,
    "source",
    "冲击源",
    "text",
    ext.source,
    true,
    ext.graph.nodes.map((n) => [n.id, n.display_name || n.id]),
  );
  for (const [k, l, v, min, max, step] of [
    ["shock", "冲击幅度 %（负值表示收缩）", -20, -100, 100, 1],
    ["days", "时间范围（天）", 90, 1, 730, 1],
    ["time", "时间范围传导因子（用户假设）", 1, 0, 1, 0.05],
    ["damping", "每级阻尼", 0.8, 0, 1, 0.05],
    ["buffer", "库存/替代供应缓冲比例", 0, 0, 1, 0.05],
    ["depth", "最大传导级数", 3, 1, 6, 1],
  ]) {
    const n = wsField(f, k, l, "number", v);
    n.min = min;
    n.max = max;
    n.step = step;
  }
  wsField(
    f,
    "assumptions",
    "缓冲、替代供应及时间假设",
    "textarea",
    "假设本期供应冲击沿已确认关系传导，尚未校准真实经营弹性。",
  );
  f.append(
    el(
      "p",
      "muted",
      "只使用人工确认关系。置信度与权重不是发生概率；循环路径截断，情景暴露不等于股价或收入变化。",
    ),
  );
  b.textContent = "保存并计算情景";
  finish();
}
function simulationShow(s) {
  ext.simulation = s;
  graphDraw();
  const root = $("#graph-detail");
  root.replaceChildren(
    el("h3", "", "情景结果 · " + s.id),
    el("p", "", s.parameters.assumptions),
  );
  for (const t of s.limitations) root.append(el("p", "muted", t));
  for (const [id, value] of Object.entries(s.impacts))
    root.append(
      el("p", "", `${extName(id)}：${(value * 100).toFixed(2)}% 情景暴露`),
    );
  const d = el("details");
  d.append(
    el("summary", "", "查看参数、传导路径和关系版本快照"),
    el("pre", "", JSON.stringify(s, null, 2)),
  );
  root.append(d);
}
for (const name of ["industries", "assets", "graph"])
  document
    .querySelector(`[data-page="${name}"]`)
    .addEventListener("click", () =>
      extRefresh().catch((e) => toast(e.message, true)),
    );
for (const [id, fn] of [
  ["#industry-new", () => industryForm()],
  ["#industry-refresh", extRefresh],
  ["#asset-new", assetForm],
  ["#asset-refresh", assetRender],
  ["#graph-refresh", graphLoad],
  ["#graph-add", () => relationForm()],
  ["#graph-ai", graphSuggest],
  ["#graph-sim", simulationForm],
])
  $(id).addEventListener("click", () =>
    Promise.resolve(fn()).catch((e) => toast(e.message, true)),
  );
for (const [id, fn] of [
  ["#industry-select", industryRender],
  ["#asset-target", assetRender],
  ["#graph-scope", graphLoad],
  ["#graph-depth", graphLoad],
  ["#graph-proposals", graphLoad],
])
  $(id).addEventListener("change", () =>
    fn().catch((e) => toast(e.message, true)),
  );
$("#graph-zoom").addEventListener("input", graphZoom);
setInterval(() => {
  if (document.hidden || $("#detail-dialog").open) return;
  const fn =
    state.page === "assets"
      ? assetRender
      : state.page === "industries"
        ? industryRender
        : null;
  if (fn) fn().catch(() => {});
}, 8000);
