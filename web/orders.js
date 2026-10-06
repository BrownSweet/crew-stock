"use strict";
Object.assign(labels, { orders: "供应链订单" });
const ordersState = { data: null, evidence: [] };
const orderKinds = {
  intent: "合作意向",
  framework: "框架协议",
  firm: "确定订单",
};
const orderStatuses = {
  active: "持续跟踪中（按披露状态）",
  completed: "已有完成披露",
  cancelled: "已有取消披露",
  unknown: "状态不明确",
};
const stepKinds = {
  effective: "合同生效",
  expiry: "合同到期",
  delivery: "交付",
  acceptance: "验收",
  payment: "付款",
};
const stepStatuses = {
  planned: "计划 / 待兑现",
  partial: "部分完成",
  completed: "有证据表明已完成",
  cancelled: "有证据表明已取消",
  unknown: "未披露状态",
};
const precisionNames = {
  unknown: "不确定，仅保留原文",
  day: "明确日期",
  month: "明确月份",
  year: "明确年份",
  range: "明确起止区间",
};
const scaleNames = {
  one: "原币单位",
  thousand: "千",
  ten_thousand: "万",
  million: "百万",
  hundred_million: "亿",
  billion: "十亿",
};
function orderParty(id, fallback = "未匹配主体") {
  return ordersState.data?.parties.find((p) => p.id === id)?.name || fallback;
}
function orderAmount(d) {
  return d.amount == null
    ? "金额未披露"
    : `${d.amount} ${scaleNames[d.amount_scale]} ${d.currency}`;
}
async function orderLoad() {
  await extLoad();
  options(
    $("#order-scope"),
    extObjects().map((o) => ({
      value: o.id,
      text: (o.type === "industry" ? "行业 · " : "公司 · ") + o.display_name,
    })),
    "全部订单",
  );
  const params = new URLSearchParams();
  const scope = $("#order-scope").value,
    at = $("#order-at").value;
  if (scope) params.set("scope_id", scope);
  if (at) params.set("at", new Date(at).toISOString());
  const data = await api("/orders?" + params);
  if (scope !== $("#order-scope").value || at !== $("#order-at").value) return;
  ordersState.data = data;
  orderRender();
}
function orderRender() {
  const data = ordersState.data;
  if (!data) return;
  orderGraph();
  const notices = $("#order-alerts");
  notices.replaceChildren(el("h2", "", "时间节点待关注"));
  if (data.as_of)
    notices.append(
      el(
        "p",
        "muted",
        "历史已知时点：" + date(data.as_of) + "。" + data.historical_scope_note,
      ),
    );
  if (!data.alerts.length)
    notices.append(
      el("p", "muted", "当前范围没有 90 天内的待处理节点或模糊期限。"),
    );
  for (const a of data.alerts) {
    const o = data.orders.find((o) => o.id === a.order_id);
    const c = el("div", "ws-assumption");
    c.append(
      el("b", "", (o?.data.title || a.order_id) + " · " + a.name),
      el(
        "p",
        a.kind === "due_check" ? "run-error" : "",
        a.label + " · " + a.raw,
      ),
      actionButton("查看该时点版本", () => orderDetail(o)),
    );
    notices.append(c);
  }
  const root = $("#order-list");
  root.replaceChildren(el("h2", "", "订单档案"));
  if (!data.orders.length)
    root.append(
      el(
        "p",
        "muted",
        "还没有确认订单。可以点击“AI 自动采集订单线索”、登记有来源的订单，或从文件与证据识别候选；确认后可查看流向和履约时间节点。",
      ),
    );
  for (const o of data.orders) {
    const d = o.data,
      c = el("article", "ws-record");
    c.append(
      el("h3", "", d.title),
      el(
        "p",
        "",
        `${orderKinds[d.agreement_type]} · ${orderStatuses[d.status]} · ${orderAmount(d)}`,
      ),
      el(
        "p",
        "",
        `${orderParty(d.supplier_id, d.supplier_name)} → ${orderParty(d.buyer_id, d.buyer_name)} · ${d.product}`,
      ),
      el(
        "p",
        "muted",
        `${o.id} · ${o.version_id} · 版本确认于 ${date(o.confirmed_at)}`,
      ),
      actionButton("详情与履约时间轴", () => orderDetail(o)),
    );
    if (!data.as_of)
      c.append(
        actionButton("提出变更", () => orderForm(o)),
        actionButton("检查新资料与变化", () => orderExtractForm([], o.id)),
        actionButton("设置持续监控", () => orderWatchForm(o)),
      );
    const w = data.watches.find((w) => w.parent_id === o.id);
    if (w)
      c.append(
        el(
          "p",
          "muted",
          `监控${w.enabled ? "已启用" : "暂停"} · 每 ${w.interval_hours} 小时 · 下次 ${date(w.next_at)}${w.last_error ? " · " + w.last_error : ""}`,
        ),
      );
    root.append(c);
  }
  const proposals = $("#order-proposals");
  proposals.replaceChildren();
  if (data.as_of) {
    proposals.append(
      el("p", "muted", "历史视图不展示当前待审核建议；回到现在后审核。"),
    );
  } else
    for (const p of data.proposals.filter(
      (p) => p.review_status === "pending",
    )) {
      const c = el("article", "ws-record");
      c.append(
        el("h3", "", p.data.title),
        el(
          "p",
          "muted",
          `${p.origin === "ai" ? "AI 建议" : "人工草案"} · ${p.order_id ? "现有订单变更" : "新订单候选"} · ${date(p.created_at)}`,
        ),
        el("p", "", p.change_summary),
        el(
          "p",
          "",
          `${p.data.supplier_name} → ${p.data.buyer_name} · ${orderAmount(p.data)}`,
        ),
        actionButton("核对完整草案 / 审核", () => orderProposalDetail(p)),
        actionButton("编辑并重新提交", () => orderForm(null, p)),
      );
      proposals.append(c);
    }
  if (!data.as_of && !proposals.children.length)
    proposals.append(
      el(
        "p",
        "muted",
        "没有待核对建议。已确认、驳回和替代的建议可在分析记录或订单版本中回溯。",
      ),
    );
  if (!data.as_of) {
    const processed = el("details");
    processed.append(el("summary", "", "已处理建议与审核记录"));
    const names = {
      approved: "已确认",
      rejected: "已驳回",
      superseded: "已被新草案替代",
    };
    for (const p of data.proposals.filter((p) => p.review_status !== "pending"))
      processed.append(
        actionButton(
          `${p.data.title} · ${names[p.review_status] || p.review_status}`,
          () => orderProposalDetail(p),
        ),
      );
    proposals.append(processed);
  }
  const scans = $("#order-scans");
  scans.replaceChildren();
  if (data.as_of) {
    scans.append(el("p", "muted", "历史回放不混入之后的监控结果。"));
    return;
  }
  for (const j of data.scans) {
    const c = el("article", "ws-record");
    c.append(
      el("b", "", j.id + " · "),
      badge(j.status),
      el(
        "p",
        "muted",
        `${date(j.created_at)} · ${j.order_id || j.scope_name || "所选资料识别"}`,
      ),
    );
    if (j.error) c.append(el("p", "run-error", j.error));
    if (j.alerts_at_check?.length)
      c.append(
        el(
          "p",
          "",
          `本轮时间节点检查：${j.alerts_at_check.map((a) => a.name + " · " + a.label).join("；")}`,
        ),
      );
    for (const g of j.gaps || []) c.append(el("p", "muted", g));
    if (j.coverage) c.append(el("p", "muted", j.coverage));
    for (const id of j.proposal_ids || []) {
      const p = data.proposals.find((p) => p.id === id);
      if (p)
        c.append(
          actionButton(id + " · " + p.review_status, () =>
            orderProposalDetail(p),
          ),
        );
    }
    scans.append(c);
  }
}
function orderGraph() {
  const root = $("#order-graph"),
    data = ordersState.data;
  root.replaceChildren();
  const layer = $("#order-layer").value;
  const edges = data.edges.filter((e) => layer === "both" || e.flow === layer);
  if (!edges.length) {
    root.append(
      el("p", "muted", "确认订单后，货物流与合同资金流会在这里显示。"),
    );
    return;
  }
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg"),
    height = Math.max(240, edges.length * 85 + 70);
  svg.setAttribute("viewBox", `0 0 1050 ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    "供应链订单货物流与合同资金流；每条关系可点击查看订单",
  );
  const defs = document.createElementNS(ns, "defs");
  for (const [kind, color] of [
    ["goods", "#345c42"],
    ["cash", "#a66e31"],
  ]) {
    const m = document.createElementNS(ns, "marker");
    m.id = "order-arrow-" + kind;
    m.setAttribute("viewBox", "0 0 10 10");
    m.setAttribute("refX", "9");
    m.setAttribute("refY", "5");
    m.setAttribute("markerWidth", "7");
    m.setAttribute("markerHeight", "7");
    m.setAttribute("orient", "auto");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    p.setAttribute("fill", color);
    m.append(p);
    defs.append(m);
  }
  svg.append(defs);
  edges.forEach((e, i) => {
    const y = 55 + i * 85,
      g = document.createElementNS(ns, "g");
    g.setAttribute("role", "button");
    g.setAttribute("tabindex", "0");
    g.setAttribute(
      "aria-label",
      `${e.title} ${e.flow === "goods" ? "货物流" : "合同资金流"}`,
    );
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", e.flow === "goods" ? "260" : "790");
    line.setAttribute("x2", e.flow === "goods" ? "790" : "260");
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", e.flow === "goods" ? "#345c42" : "#a66e31");
    line.setAttribute("stroke-width", "3");
    line.setAttribute("marker-end", "url(#order-arrow-" + e.flow + ")");
    if (e.flow === "cash") line.setAttribute("stroke-dasharray", "7 5");
    g.append(line);
    for (const [x, text, anchor] of [
      [25, orderParty(e.flow === "goods" ? e.source : e.target), "start"],
      [1025, orderParty(e.flow === "goods" ? e.target : e.source), "end"],
      [
        525,
        (e.flow === "goods" ? "货物流" : "合同资金流") + " · " + e.order_id,
        "middle",
      ],
    ]) {
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", x);
      t.setAttribute("y", x === 525 ? y - 12 : y + 5);
      t.setAttribute("text-anchor", anchor);
      t.textContent = text.length > 30 ? text.slice(0, 29) + "…" : text;
      g.append(t);
    }
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", "525");
    t.setAttribute("y", y + 24);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("class", "order-graph-caption");
    const raw =
      e.milestones.map((m) => m.timing.raw).join(" / ") || "时间未披露";
    t.textContent =
      orderKinds[e.agreement_type] +
      " · " +
      orderStatuses[e.status] +
      " · " +
      raw.slice(0, 65);
    g.append(t);
    const open = () =>
      orderDetail(data.orders.find((o) => o.id === e.order_id));
    g.addEventListener("click", open);
    g.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter" || evt.key === " ") {
        evt.preventDefault();
        open();
      }
    });
    svg.append(g);
  });
  root.append(svg);
}
function orderBody(root, d) {
  root.append(
    el(
      "p",
      "",
      `${orderKinds[d.agreement_type]} · ${orderStatuses[d.status]} · ${orderAmount(d)}`,
    ),
    el(
      "p",
      "",
      `供货方：${orderParty(d.supplier_id, d.supplier_name)}；采购方：${orderParty(d.buyer_id, d.buyer_name)}`,
    ),
    el(
      "p",
      "",
      `付款方：${d.payer_id ? orderParty(d.payer_id) : "未单列，图按采购方关联"}；接收 / 使用方：${d.recipient_id ? orderParty(d.recipient_id) : "未单列，图按采购方关联"}`,
    ),
    el("p", "", `产品：${d.product}；数量：${d.quantity_text}`),
  );
  if (d.reference)
    root.append(el("p", "", "合同编号 / 识别线索：" + d.reference));
  reportSection(root, "条件、取消与续约安排", d.terms || "未披露");
  root.append(el("h3", "", "履约时间轴（按已披露安排）"));
  const ordered = [...d.milestones].sort((a, b) =>
    (a.timing.value || "z").localeCompare(b.timing.value || "z"),
  );
  if (!ordered.length)
    root.append(el("p", "muted", "尚无明确里程碑，不能推定履约期。"));
  for (const m of ordered) {
    const c = el("section", "order-milestone");
    c.append(
      el("b", "", `${stepKinds[m.kind]} · ${m.name}`),
      el(
        "p",
        "",
        `${m.timing.raw} · ${precisionNames[m.timing.precision]}${m.timing.value ? " · " + m.timing.value : ""}`,
      ),
      el(
        "p",
        "",
        stepStatuses[m.status] + (m.amount_text ? " · " + m.amount_text : ""),
      ),
      el("p", "muted", m.notes),
    );
    root.append(c);
  }
  for (const g of d.gaps) root.append(el("p", "muted", "待核实：" + g));
  root.append(el("h3", "", "来源与原句"));
  for (const c of d.citations)
    root.append(
      el("blockquote", "", c.quote),
      actionButton(c.evidence_id, () => showEvidence(c.evidence_id)),
    );
}
async function orderDetail(o) {
  const root = showDetail(o.data.title);
  orderBody(root, o.data);
  root.append(
    el(
      "p",
      "muted",
      `当前展示 ${o.version_id}，确认于 ${date(o.confirmed_at)}。收入确认和已收款不能由合同金额推定。`,
    ),
  );
  const detail = await api("/orders/" + o.id);
  if (!ordersState.data.as_of) {
    root.append(el("h3", "", "历史确认版本"));
    for (const v of detail.versions)
      root.append(
        actionButton(`${date(v.created_at)} · ${v.id} · ${v.note}`, () => {
          const r = showDetail("历史版本 · " + v.id);
          orderBody(r, v.snapshot.data);
        }),
      );
  }
}
async function orderProposalDetail(p) {
  const root = showDetail("待核对订单 · " + p.data.title);
  root.append(
    el("p", "", p.change_summary),
    el(
      "p",
      "muted",
      `状态：${{ pending: "待核对", approved: "已确认", rejected: "已驳回", superseded: "已有替代草案" }[p.review_status] || p.review_status} · 来源：${p.origin === "ai" ? "AI 提取，未经确认" : "人工草案"}`,
    ),
  );
  if (p.order_id) {
    const current = await api("/orders/" + p.order_id);
    const changed = Object.keys(p.data).filter(
      (k) => JSON.stringify(p.data[k]) !== JSON.stringify(current.data[k]),
    );
    root.append(
      el(
        "p",
        "",
        `相对当前版本变化的内容：${changed.map((k) => ({ title: "标题", reference: "合同识别线索", agreement_type: "协议确定性", supplier_id: "供货方档案", supplier_name: "供货方名称", buyer_id: "采购方档案", buyer_name: "采购方名称", payer_id: "付款方", recipient_id: "接收方", product: "产品", quantity_text: "数量", amount: "金额", currency: "币种", amount_scale: "金额单位", status: "履约状态", terms: "合同条件", milestones: "履约节点", citations: "来源引用", gaps: "待核实事项" })[k] || k).join("、") || "无"}`,
      ),
    );
    const details = el("details");
    details.append(el("summary", "", "展开当前已确认版本，与下方建议对照"));
    orderBody(details, current.data);
    root.append(details, el("h3", "", "建议的新版本"));
    if (current.version_id !== p.base_version)
      root.append(
        el(
          "p",
          "run-error",
          "此建议基于旧版本，请编辑后重新提交，不能直接确认。",
        ),
      );
  }
  orderBody(root, p.data);
  if (p.review_status === "pending") {
    const f = el("form", "ws-form");
    root.append(f);
    wsField(f, "review-note", "核对依据与审核说明", "textarea");
    const b = el("button", "button dark", "确认这份订单 / 变更");
    b.type = "submit";
    f.append(b);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      b.disabled = true;
      try {
        await api(`/orders/proposals/${p.id}/review`, {
          method: "POST",
          body: JSON.stringify({
            decision: "approve",
            note: new FormData(f).get("review-note"),
          }),
        });
        $("#detail-dialog").close();
        await orderLoad();
        toast("订单版本已确认保存");
      } catch (e) {
        toast(e.message, true);
      } finally {
        b.disabled = false;
      }
    });
    f.append(
      actionButton(
        "驳回此建议",
        async () => {
          const note = new FormData(f).get("review-note");
          if (!note) return toast("请填写审核说明", true);
          await api(`/orders/proposals/${p.id}/review`, {
            method: "POST",
            body: JSON.stringify({ decision: "reject", note }),
          });
          $("#detail-dialog").close();
          await orderLoad();
        },
        "button pale",
      ),
    );
  }
}
async function partyForm() {
  await extLoad();
  const { f, finish } = extForm("添加未上市公司 / 业务主体", async (fd) => {
    await api("/orders/parties", {
      method: "POST",
      body: JSON.stringify({
        name: fd.get("name"),
        company_id: fd.get("company") || null,
        notes: fd.get("notes"),
      }),
    });
    await orderLoad();
  });
  wsField(f, "name", "主体名称", "text");
  wsField(f, "company", "已有上市公司关联（可选）", "text", "", false, [
    ["", "独立主体，不关联证券"],
    ...ext.companies.map((c) => [c.id, c.display_name]),
  ]);
  wsField(f, "notes", "主体身份与区分说明", "textarea", "", false);
  f.append(
    el(
      "p",
      "muted",
      "用于记录采购、付款或使用方，不要求上市代码；仅凭相似名称不要合并法人主体。",
    ),
  );
  finish();
}
async function orderForm(order = null, proposal = null) {
  if (!ordersState.data) await orderLoad();
  ordersState.evidence = await api("/objects?kind=evidence");
  let current = order;
  if (proposal?.order_id) current = await api("/orders/" + proposal.order_id);
  const d = proposal?.data || current?.data;
  const choices = [
    ["", "尚未匹配，请保留名称候选"],
    ...ordersState.data.parties.map((p) => [p.id, p.name]),
  ];
  const { f, finish, b } = extForm(
    current ? "提出订单变更（原版本保留）" : "登记订单草案（待核对）",
    async (fd) => {
      const milestones = stepRows
        .filter((r) => r.node.isConnected)
        .map((r) => ({
          key: r.key,
          name: fd.get(r.key + "-name"),
          kind: fd.get(r.key + "-kind"),
          timing: {
            raw: fd.get(r.key + "-raw") || "未披露",
            precision: fd.get(r.key + "-precision"),
            value: fd.get(r.key + "-value") || "",
          },
          status: fd.get(r.key + "-status"),
          amount_text: fd.get(r.key + "-amount") || "",
          notes: fd.get(r.key + "-notes") || "",
        }));
      const citations = citeRows
        .filter((r) => r.node.isConnected)
        .map((r) => ({
          evidence_id: fd.get(r.key + "-evidence"),
          quote: fd.get(r.key + "-quote"),
        }));
      const payload = {
        order_id: current?.id || null,
        base_version: current?.version_id || null,
        supersedes: proposal?.id || null,
        change_summary: fd.get("change"),
        data: {
          title: fd.get("title"),
          reference: fd.get("reference"),
          agreement_type: fd.get("type"),
          status: fd.get("status"),
          supplier_id: fd.get("supplier") || null,
          supplier_name: fd.get("supplier-name"),
          buyer_id: fd.get("buyer") || null,
          buyer_name: fd.get("buyer-name"),
          payer_id: fd.get("payer") || null,
          recipient_id: fd.get("recipient") || null,
          product: fd.get("product"),
          quantity_text: fd.get("quantity") || "未披露",
          amount: fd.get("amount") || null,
          currency: fd.get("currency") || "",
          amount_scale: fd.get("scale"),
          terms: fd.get("terms") || "",
          gaps: (fd.get("gaps") || "").split("\n").filter(Boolean),
          milestones,
          citations,
        },
      };
      await api("/orders/proposals", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await orderLoad();
    },
  );
  wsField(f, "title", "订单标题", "text", d?.title || "");
  wsField(
    f,
    "reference",
    "合同编号 / 可识别线索（未披露可空）",
    "text",
    d?.reference || "",
    false,
  );
  wsField(
    f,
    "type",
    "协议确定性",
    "text",
    d?.agreement_type || "intent",
    true,
    Object.entries(orderKinds),
  );
  wsField(
    f,
    "status",
    "披露的履约状态",
    "text",
    d?.status || "unknown",
    true,
    Object.entries(orderStatuses),
  );
  for (const [role, label] of [
    ["supplier", "供货方"],
    ["buyer", "采购方"],
  ]) {
    const s = wsField(
      f,
      role,
      label + "档案",
      "text",
      d?.[role + "_id"] || "",
      false,
      choices,
    );
    const n = wsField(
      f,
      role + "-name",
      label + "原文名称",
      "text",
      d?.[role + "_name"] || "",
    );
    s.addEventListener("change", () => {
      if (s.value) n.value = orderParty(s.value);
    });
  }
  for (const [role, label] of [
    ["payer", "付款方（未披露可留空）"],
    ["recipient", "接收 / 使用方（未披露可留空）"],
  ])
    wsField(f, role, label, "text", d?.[role + "_id"] || "", false, [
      ["", "未单列（图按采购方关联）"],
      ...choices.slice(1),
    ]);
  wsField(f, "product", "产品 / 服务", "textarea", d?.product || "");
  wsField(
    f,
    "quantity",
    "数量与单位（可填未披露）",
    "text",
    d?.quantity_text || "未披露",
    false,
  );
  const amount = wsField(
    f,
    "amount",
    "合同金额（未知留空，不填 0）",
    "number",
    d?.amount ?? "",
    false,
  );
  amount.min = 0;
  amount.step = "any";
  wsField(
    f,
    "scale",
    "原文金额数量级",
    "text",
    d?.amount_scale || "one",
    true,
    Object.entries(scaleNames),
  );
  wsField(
    f,
    "currency",
    "金额币种（有金额时必填）",
    "text",
    d?.currency || "",
    false,
  );
  wsField(
    f,
    "terms",
    "生效条件、取消与续约条款",
    "textarea",
    d?.terms || "",
    false,
  );
  wsField(
    f,
    "gaps",
    "未披露 / 待核实事项（每行一项）",
    "textarea",
    (d?.gaps || []).join("\n"),
    false,
  );
  f.append(
    el("h3", "", "履约节点"),
    el(
      "p",
      "muted",
      "模糊的“2027年前”只填原文并选择不确定。明确日期用 YYYY-MM-DD；月份 YYYY-MM；年份 YYYY；区间 YYYY-MM-DD/YYYY-MM-DD。完成或取消需有对应披露。",
    ),
  );
  const stepBox = el("div"),
    stepRows = [];
  f.append(stepBox);
  const addStep = (m = null) => {
    const key = m?.key || "m-" + crypto.randomUUID(),
      node = el("fieldset", "ws-assumption");
    stepRows.push({ key, node });
    node.append(el("legend", "", "履约节点"));
    wsField(node, key + "-name", "节点名称", "text", m?.name || "");
    wsField(
      node,
      key + "-kind",
      "节点类型",
      "text",
      m?.kind || "delivery",
      true,
      Object.entries(stepKinds),
    );
    wsField(node, key + "-raw", "时间原文", "text", m?.timing.raw || "未披露");
    wsField(
      node,
      key + "-precision",
      "日期精度",
      "text",
      m?.timing.precision || "unknown",
      true,
      Object.entries(precisionNames),
    );
    wsField(
      node,
      key + "-value",
      "结构化日期（不确定时留空）",
      "text",
      m?.timing.value || "",
      false,
    );
    wsField(
      node,
      key + "-status",
      "节点披露状态",
      "text",
      m?.status || "planned",
      true,
      Object.entries(stepStatuses),
    );
    wsField(
      node,
      key + "-amount",
      "该节点数量 / 金额原文",
      "text",
      m?.amount_text || "",
      false,
    );
    wsField(
      node,
      key + "-notes",
      "条件 / 状态依据",
      "textarea",
      m?.notes || "",
      false,
    );
    node.append(actionButton("移除此节点", () => node.remove(), "button pale"));
    stepBox.append(node);
  };
  for (const m of d?.milestones || []) addStep(m);
  f.append(
    actionButton(
      "＋ 添加交付 / 付款 / 合同节点",
      () => addStep(),
      "button pale",
    ),
  );
  const citeBox = el("div"),
    citeRows = [];
  f.append(el("h3", "", "证据原句"), citeBox);
  const addCite = (c = null) => {
    const key = "c-" + crypto.randomUUID(),
      node = el("fieldset", "ws-assumption");
    citeRows.push({ key, node });
    wsField(
      node,
      key + "-evidence",
      "来源证据",
      "text",
      c?.evidence_id || null,
      true,
      ordersState.evidence
        .filter((e) => e.acquisition_status === "success" && e.blob)
        .map((e) => [
          e.id,
          e.id + " · " + (e.display_name || e.topic || "资料"),
        ]),
    );
    wsField(
      node,
      key + "-quote",
      "逐字引用（须出现在所选资料）",
      "textarea",
      c?.quote || "",
    );
    node.append(actionButton("移除此引用", () => node.remove(), "button pale"));
    citeBox.append(node);
  };
  for (const c of d?.citations || [null]) addCite(c);
  f.append(actionButton("＋ 添加来源引用", () => addCite(), "button pale"));
  wsField(
    f,
    "change",
    "本次登记 / 变更说明",
    "textarea",
    proposal?.change_summary || (current ? "" : "初次登记，待核对来源与交易方"),
  );
  b.textContent = "保存待核对草案";
  finish();
}
async function orderExtractForm(evidenceIds = [], oid = null) {
  if (!ordersState.data) await orderLoad();
  const ev = await api("/objects?kind=evidence");
  const { f, finish, b } = extForm(
    oid ? "检查订单变化" : "从资料识别订单与变更",
    async (fd) => {
      await api("/orders/scans", {
        method: "POST",
        body: JSON.stringify({
          order_id: fd.get("order") || null,
          evidence_ids: fd.getAll("evidence"),
          pull_latest: fd.get("pull") === "yes",
        }),
      });
      page("orders");
      await orderLoad();
    },
  );
  wsField(f, "order", "目标订单", "text", oid || "", false, [
    ["", "识别新订单或匹配已有订单"],
    ...ordersState.data.orders.map((o) => [o.id, o.data.title]),
  ]);
  wsField(f, "pull", "数据检查方式", "text", oid ? "yes" : "no", true, [
    ["no", "只检查已有 / 所选资料"],
    ["yes", "同时采集目标订单关联公司的最新长桥新闻与公告"],
  ]);
  const select = wsField(
    f,
    "evidence",
    "选择证据（可多选）",
    "text",
    null,
    !oid,
    ev
      .filter((e) => e.acquisition_status === "success" && e.blob)
      .map((e) => [e.id, e.id + " · " + (e.display_name || e.topic || "资料")]),
  );
  select.multiple = true;
  select.size = 8;
  for (const opt of select.options)
    opt.selected = evidenceIds.includes(opt.value);
  f.append(
    el(
      "p",
      "muted",
      "调用 DeepSeek 识别；只生成候选，不自动改写订单。新对象需要匹配档案后才能确认。指定订单可不选资料，程序会检查双方档案中的已有资料。",
    ),
  );
  b.textContent = "开始识别 / 检查";
  finish();
}
async function orderDiscover() {
  if ($("#order-at").value)
    throw new Error("请先回到现在，再采集最新订单线索");
  const button = $("#order-discover");
  button.disabled = true;
  try {
    const job = await api("/orders/scans", {
      method: "POST",
      body: JSON.stringify({
        scope_id: $("#order-scope").value || null,
        evidence_ids: [],
        pull_latest: true,
      }),
    });
    toast(`已排队采集 ${job.scope_name}；候选将在下方待核对区显示`);
    await orderLoad();
  } finally {
    button.disabled = false;
  }
}
function orderWatchForm(o) {
  const w = ordersState.data.watches.find((w) => w.parent_id === o.id);
  const { f, finish } = extForm("持续监控 · " + o.data.title, async (fd) => {
    await api(`/orders/${o.id}/watch`, {
      method: "PUT",
      body: JSON.stringify({
        enabled: fd.get("enabled") === "yes",
        interval_hours: Number(fd.get("hours")),
        pull_latest: fd.get("pull") === "yes",
      }),
    });
    await orderLoad();
  });
  wsField(f, "enabled", "监控状态", "text", w?.enabled ? "yes" : "no", true, [
    ["no", "暂停"],
    ["yes", "启用"],
  ]);
  const h = wsField(
    f,
    "hours",
    "检查间隔（小时）",
    "number",
    w?.interval_hours || 24,
  );
  h.min = 1;
  h.max = 720;
  wsField(
    f,
    "pull",
    "采集范围",
    "text",
    w?.pull_latest === false ? "no" : "yes",
    true,
    [
      ["yes", "长桥新闻 / 公告 + 已归档文件"],
      ["no", "只检查已归档资料"],
    ],
  );
  f.append(
    el(
      "p",
      "muted",
      "时间节点提示显示在本页。启用后立即排队一次，此后按间隔运行并消耗数据/模型调用；仅容器运行时执行，任务繁忙可能延迟。不会发送邮件或外部消息。无新披露不代表未付款或违约。",
    ),
  );
  finish();
}
for (const [id, fn] of [
  ["#order-new", () => orderForm()],
  ["#party-new", partyForm],
  ["#order-extract", () => orderExtractForm()],
  ["#order-discover", orderDiscover],
  ["#order-refresh", orderLoad],
  [
    "#order-present",
    () => {
      $("#order-at").value = "";
      return orderLoad();
    },
  ],
])
  $(id).addEventListener("click", () =>
    Promise.resolve(fn()).catch((e) => toast(e.message, true)),
  );
for (const id of ["#order-scope", "#order-at"])
  $(id).addEventListener("change", () =>
    orderLoad().catch((e) => toast(e.message, true)),
  );
$("#order-layer").addEventListener("change", orderGraph);
document
  .querySelector('[data-page="orders"]')
  .addEventListener("click", () =>
    orderLoad().catch((e) => toast(e.message, true)),
  );
setInterval(() => {
  if (state.page === "orders" && !document.hidden && !$("#detail-dialog").open)
    orderLoad().catch(() => {});
}, 10000);
