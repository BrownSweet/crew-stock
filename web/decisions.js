"use strict";
Object.assign(labels, { decisions: "决策与模拟" });
Object.assign(statuses, {
  analyzing: "AI 分析中",
  pending: "待成交",
  partially_filled: "部分成交",
  filled: "已模拟成交",
  cancelled: "已撤销",
  expired: "已过期",
});
const decisionNames = {
  conditional_buy: "满足条件可模拟买入",
  watch: "继续观察",
  avoid: "暂时回避",
};
const scenarioNames = { bear: "悲观", base: "基准", bull: "乐观" };
const paperState = { accounts: [], data: null };
const money = (v) =>
  Number(v || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
async function decisionRefresh() {
  await extLoad();
  options(
    $("#decision-company"),
    ext.companies.map((c) => ({ value: c.id, text: c.display_name })),
  );
  const cid = $("#decision-company").value;
  const [data, accounts] = await Promise.all([
    api("/decisions" + (cid ? "?company_id=" + cid : "")),
    api("/decisions/accounts"),
  ]);
  if ($("#decision-company").value !== cid) return;
  paperState.accounts = accounts;
  paperState.data = data;
  const root = $("#plan-content");
  root.replaceChildren();
  if (!data.trade_plan.length)
    root.append(
      el(
        "p",
        "muted",
        "选择公司生成研究方案；资料不足时会保持观察，不强行给出买入价格。",
      ),
    );
  for (const j of data.plan_job.filter((j) => j.status !== "completed")) {
    const c = el("article", "ws-record");
    c.append(
      el("b", "", j.symbol + " · "),
      badge(j.status),
      el("p", "", j.error || "等待后台采集长桥行情、盈利与估值资料并分析。"),
    );
    root.append(c);
  }
  for (const p of data.trade_plan) {
    const c = el("article", "ws-record"),
      q = p.proposal;
    c.append(
      el("h3", "", p.symbol + " · " + decisionNames[q.decision]),
      el("p", "", q.thesis),
      el(
        "p",
        "muted",
        `生成于 ${date(p.created_at)} · 有效至 ${date(p.expires_at)} · ${p.model}`,
      ),
      el(
        "p",
        "",
        `参考成交价 ${money(p.market.price)} ${p.market.currency} · 行情时间 ${date(p.market.timestamp)}`,
      ),
    );
    const grid = el("div", "ws-grid");
    for (const s of q.scenarios) {
      const sc = el("section", "ws-assumption");
      sc.append(
        el(
          "b",
          "",
          scenarioNames[s.name] +
            " · " +
            money(p.scenario_prices[s.name]) +
            " " +
            q.currency,
        ),
        el("p", "", `假设每股盈利 ${s.forward_eps} × 市盈率 ${s.pe}`),
        el("p", "muted", s.assumptions),
      );
      grid.append(sc);
    }
    c.append(grid);
    if (q.entry_low)
      c.append(
        el(
          "p",
          "",
          `条件买入区间 ${q.entry_low}–${q.entry_high} · 止损 ${q.stop_loss} · 止盈 ${q.take_profit} · 收益风险比 ${money(p.reward_risk)}`,
        ),
      );
    c.append(actionButton("阅读完整逻辑与证据", () => planDetail(p)));
    if (q.decision === "conditional_buy")
      c.append(
        actionButton(
          "测算仓位 / 模拟开仓",
          () => paperBuyForm(p),
          "button dark",
        ),
      );
    for (const gap of q.gaps) c.append(el("p", "muted", "缺口：" + gap));
    root.append(c);
  }
  paperRender(accounts);
  journalRender(data);
}
function planDetail(p) {
  const r = showDetail(p.symbol + " · 开仓研究方案"),
    q = p.proposal;
  for (const [title, value] of [
    ["投资逻辑", q.thesis],
    ["经营变化 → 每股盈利", q.business_to_earnings],
    ["盈利 → 估值", q.earnings_to_valuation],
    ["现价已反映什么", q.priced_in],
    ["持有观察周期", q.horizon_months + " 个月"],
    ["催化剂", q.catalysts.join("\n")],
    ["逻辑失效条件", q.invalidation.join("\n")],
    ["反方证据", q.counter_evidence.join("\n")],
    ["数据缺口", q.gaps.join("\n")],
  ])
    reportSection(r, title, value);
  r.append(
    el(
      "p",
      "muted",
      p.market.price_basis + "；研究结论与未来 EPS、PE 均需人工判断。",
    ),
  );
  for (const eid of p.evidence_ids)
    r.append(actionButton(eid, () => showEvidence(eid)));
}
async function planForm() {
  const cid = $("#decision-company").value,
    c = ext.companies.find((c) => c.id === cid);
  if (!c) return toast("请先创建公司档案", true);
  const { f, finish, b } = extForm("生成开仓研究方案", async (fd) => {
    await api("/decisions/plans", {
      method: "POST",
      body: JSON.stringify({
        company_id: cid,
        symbol: fd.get("symbol"),
        question: fd.get("question"),
      }),
    });
    await decisionRefresh();
  });
  wsField(
    f,
    "symbol",
    "证券",
    "text",
    null,
    true,
    (c.securities || []).map((s) => [s.symbol, s.symbol]),
  );
  wsField(
    f,
    "question",
    "决策研究问题",
    "textarea",
    "基于公司与行业研究，判断变化如何影响未来每股盈利和估值，现价已经反映哪些预期？给出有证据的条件买入或继续观察方案。",
  );
  f.append(
    el(
      "p",
      "muted",
      "读取现有研究档案并新采集行情、财报、一致预期和估值。需要 DeepSeek 与长桥连接；本操作不会创建委托。",
    ),
  );
  b.textContent = "采集与生成方案";
  finish();
}
function paperAccountForm() {
  const { f, finish } = extForm("建立虚拟资金账户", async (fd) => {
    await api("/decisions/accounts", {
      method: "POST",
      body: JSON.stringify({
        name: fd.get("name"),
        currency: fd.get("currency"),
        initial_cash: fd.get("cash"),
        fee_bps: fd.get("fee"),
        slippage_bps: fd.get("slippage"),
      }),
    });
    await decisionRefresh();
  });
  wsField(f, "name", "模拟账户名称", "text", "研究验证账户");
  wsField(
    f,
    "currency",
    "账户币种",
    "text",
    "USD",
    true,
    ["USD", "HKD", "CNY", "SGD"].map((x) => [x, x]),
  );
  for (const [k, label, value, min, max] of [
    ["cash", "初始虚拟资金", 100000, 0.01, 100000000],
    ["fee", "每次成交费用（基点，5 = 0.05%）", 5, 0, 100],
    ["slippage", "不利滑点（基点）", 5, 0, 100],
  ]) {
    const n = wsField(f, k, label, "number", value);
    n.min = min;
    n.max = max;
    n.step = "0.01";
  }
  f.append(
    el(
      "p",
      "muted",
      "不连接券商资产。一个账户使用一种币种，不自动换汇。费用与滑点为简化假设，不是券商收费表。",
    ),
  );
  finish();
}
async function paperBuyForm(p) {
  paperState.accounts = await api("/decisions/accounts");
  const accounts = paperState.accounts.filter(
    (a) => a.currency === p.market.currency,
  );
  if (!accounts.length)
    return toast("请先创建 " + p.market.currency + " 模拟账户", true);
  const root = showDetail(p.symbol + " · 风险预算与模拟委托"),
    f = el("form", "ws-form");
  root.append(f);
  wsField(
    f,
    "account_id",
    "虚拟账户",
    "text",
    accounts[0].id,
    true,
    accounts.map((a) => [a.id, a.name + " · " + a.currency]),
  );
  const limit = wsField(
    f,
    "limit_price",
    "买入限价（须在方案区间内）",
    "number",
    p.proposal.entry_high,
  );
  limit.step = "any";
  for (const [key, label, value, max] of [
    ["risk_percent", "计划风险占权益 %", 1, 5],
    ["position_percent", "单笔资金占权益上限 %", 10, 50],
  ]) {
    const n = wsField(f, key, label, "number", value);
    n.min = 0.01;
    n.max = max;
    n.step = 0.01;
  }
  const preview = el("div", "ws-record"),
    calc = el("button", "button pale", "测算可买数量");
  calc.type = "button";
  f.append(calc, preview);
  const quantity = wsField(f, "quantity", "委托股数（可调低）", "number", "");
  quantity.min = 1;
  const confirm = wsField(
    f,
    "confirm",
    "确认已审核方案和失效条件；此委托仅使用虚拟资金",
    "checkbox",
  );
  confirm.value = "yes";
  const submit = el("button", "button dark", "提交模拟限价委托");
  submit.type = "submit";
  submit.disabled = true;
  f.append(submit);
  f.append(
    el(
      "p",
      "muted",
      "限价委托提交后等待后续新成交快照。止损/止盈只有刷新行情时才检查；可在账户启用每分钟跟踪，后台研究可能延迟。",
    ),
  );
  let measured = null,
    requestId = crypto.randomUUID();
  const payload = () => {
    const fd = new FormData(f);
    return {
      account_id: fd.get("account_id"),
      limit_price: fd.get("limit_price"),
      risk_percent: fd.get("risk_percent"),
      position_percent: fd.get("position_percent"),
    };
  };
  f.addEventListener("input", (e) => {
    if (!["quantity", "confirm"].includes(e.target.name)) {
      measured = null;
      submit.disabled = true;
      preview.replaceChildren(el("p", "muted", "参数变化，请重新测算。"));
    }
  });
  calc.addEventListener("click", async () => {
    calc.disabled = true;
    try {
      const result = await api(`/decisions/plans/${p.id}/budget`, {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      measured = JSON.stringify(payload());
      requestId = crypto.randomUUID();
      quantity.value = result.quantity;
      quantity.step = result.lot_size;
      quantity.max = result.quantity;
      preview.replaceChildren(
        el(
          "p",
          "",
          `最多 ${result.quantity} 股 · 每手 ${result.lot_size} 股 · 预计占用 ${money(result.capital)} ${p.market.currency}`,
        ),
        el(
          "p",
          "",
          `风险预算 ${money(result.risk_budget)} · 按最多股数估算的止损损失 ${money(result.planned_loss)} · 可用现金 ${money(result.available_cash)}`,
        ),
        el("p", "muted", result.limitations),
      );
      submit.disabled = result.quantity <= 0;
    } catch (e) {
      toast(e.message, true);
    } finally {
      calc.disabled = false;
    }
  });
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!measured || measured !== JSON.stringify(payload()))
      return toast("请先重新测算", true);
    submit.disabled = true;
    try {
      await api(`/decisions/plans/${p.id}/orders`, {
        method: "POST",
        body: JSON.stringify({
          ...payload(),
          quantity: Number(quantity.value),
          request_id: requestId,
          confirm_plan: confirm.checked,
        }),
      });
      $("#detail-dialog").close();
      await decisionRefresh();
      toast("模拟委托已提交，尚未成交；请刷新账户行情或启用跟踪");
    } catch (e) {
      toast(e.message, true);
      submit.disabled = false;
    }
  });
}
function paperRender(accounts) {
  const root = $("#paper-content");
  root.replaceChildren();
  if (!accounts.length)
    root.append(
      el(
        "p",
        "muted",
        "尚无模拟账户。建立账户后可将已审核方案放入虚拟资金组合。",
      ),
    );
  for (const a of accounts) {
    const c = el("article", "ws-record");
    c.append(
      el("h3", "", a.name + " · " + a.currency),
      el(
        "p",
        "",
        `估算权益 ${money(a.equity)} · 现金 ${money(a.cash)} · 可用现金 ${money(a.available_cash)} · 已实现盈亏 ${money(a.realized_pnl)}`,
      ),
      el(
        "p",
        "muted",
        `初始资金 ${money(a.initial_cash)} · 费用 ${a.fee_bps} 基点 / 滑点 ${a.slippage_bps} 基点 · 最近刷新 ${a.last_refresh_at ? date(a.last_refresh_at) : "尚未刷新"}`,
      ),
    );
    c.append(
      actionButton(
        a.refresh_requested || a.refresh_status === "running"
          ? "行情刷新排队 / 处理中"
          : "获取行情并模拟撮合",
        async () => {
          await api(`/decisions/accounts/${a.id}/refresh`, { method: "POST" });
          await decisionRefresh();
        },
      ),
      actionButton(
        a.auto_refresh ? "暂停每分钟跟踪" : "启用每分钟跟踪",
        async () => {
          await api(`/decisions/accounts/${a.id}/tracking`, {
            method: "PUT",
            body: JSON.stringify({ enabled: !a.auto_refresh }),
          });
          await decisionRefresh();
        },
      ),
    );
    for (const result of a.last_results || [])
      c.append(
        el(
          "p",
          "muted",
          result.symbol +
            "：" +
            (result.filled
              ? "本次模拟成交 " + result.filled + " 股"
              : result.reason),
        ),
      );
    for (const [symbol, p] of Object.entries(a.positions).filter(
      ([, p]) => p.quantity > 0,
    )) {
      const pos = el("section", "ws-assumption");
      pos.append(
        el("b", "", `${symbol} · ${p.quantity} 股`),
        el(
          "p",
          "",
          `含费用成本 ${money(p.average_cost)} · 标记价 ${money(p.last_price)} · 未实现盈亏 ${money((Number(p.last_price) - Number(p.average_cost)) * p.quantity)}`,
        ),
        el(
          "p",
          "muted",
          `标记时间 ${date(p.mark_timestamp)} · 止损 ${p.stop_loss} / 止盈 ${p.take_profit}`,
        ),
        actionButton("提交模拟平仓", async () => {
          await api(
            `/decisions/accounts/${a.id}/close/${encodeURIComponent(symbol)}`,
            { method: "POST" },
          );
          await decisionRefresh();
          toast("模拟平仓已排队，等待下单后的新行情");
        }),
      );
      c.append(pos);
    }
    c.append(el("h4", "", "委托（提交不等于成交）"));
    for (const o of a.orders) {
      const row = el("div", "ws-assumption");
      row.append(
        el(
          "p",
          "",
          `${o.id} · ${o.symbol} · ${o.side === "buy" ? "买入" : "卖出"} · ${o.limit_price ? "限价 " + o.limit_price : "模拟市价"} · ${o.filled_quantity}/${o.quantity} 股`,
        ),
        badge(o.status),
      );
      if (o.trigger)
        row.append(
          el("p", "muted", o.trigger === "stop_loss" ? "止损触发" : "止盈触发"),
        );
      if (["pending", "partially_filled"].includes(o.status))
        row.append(
          actionButton("撤销剩余委托", async () => {
            await api(`/decisions/orders/${o.id}/cancel`, { method: "POST" });
            await decisionRefresh();
          }),
        );
      c.append(row);
    }
    const fills = el("details");
    fills.append(el("summary", "", `成交回放 · 最近 ${a.fills.length} 条`));
    for (const f of a.fills)
      fills.append(
        el(
          "p",
          "",
          `${date(f.market_timestamp)} · ${f.symbol} ${f.side === "buy" ? "买入" : "卖出"} ${f.quantity} 股 × ${money(f.price)} · 费用 ${money(f.fee)} · 本笔已实现盈亏 ${money(f.realized_pnl)}`,
        ),
        actionButton("行情依据 " + f.evidence_id, () =>
          showEvidence(f.evidence_id),
        ),
      );
    c.append(fills);
    root.append(c);
  }
}
function journalRender(data) {
  const root = $("#journal-content");
  root.replaceChildren();
  for (const d of data.paper_decision) {
    const c = el("article", "ws-record");
    c.append(
      el("h3", "", `${d.plan_snapshot.symbol} · ${d.quantity} 股模拟买入决策`),
      el(
        "p",
        "muted",
        `${date(d.created_at)} · 委托 ${d.order_id} · 决策时的方案、证据和预算已留存`,
      ),
      el("p", "", d.plan_snapshot.proposal.thesis),
      actionButton("查看当时的方案", () => planDetail(d.plan_snapshot)),
      actionButton("记录复盘", () => paperReviewForm(d)),
    );
    for (const r of data.paper_review.filter((r) => r.decision_id === d.id))
      c.append(
        el("h4", "", "复盘 · " + date(r.reviewed_at)),
        el("p", "", "逻辑变化：" + r.thesis_change),
        el("p", "", "过程检查：" + r.process_review),
        el("p", "", "下一步：" + r.next_action),
      );
    root.append(c);
  }
  if (!data.paper_decision.length)
    root.append(
      el(
        "p",
        "muted",
        "提交模拟委托后，自动保留当时的研究和资金预算，可持续追加复盘。",
      ),
    );
}
function paperReviewForm(d) {
  const { f, finish } = extForm("复盘模拟决策", async (fd) => {
    await api(`/decisions/journal/${d.id}/review`, {
      method: "POST",
      body: JSON.stringify({
        thesis_change: fd.get("thesis"),
        process_review: fd.get("process"),
        next_action: fd.get("next"),
      }),
    });
    await decisionRefresh();
  });
  wsField(f, "thesis", "哪些证据支持或推翻原逻辑？", "textarea");
  wsField(f, "process", "决策过程：价格、仓位、执行和偏差", "textarea");
  wsField(f, "next", "下一步行动与观察条件", "textarea");
  finish();
}
for (const [id, fn] of [
  ["#plan-new", planForm],
  ["#paper-new", paperAccountForm],
  ["#decision-refresh", decisionRefresh],
])
  $(id).addEventListener("click", () =>
    Promise.resolve(fn()).catch((e) => toast(e.message, true)),
  );
$("#decision-company").addEventListener("change", () =>
  decisionRefresh().catch((e) => toast(e.message, true)),
);
document
  .querySelector('[data-page="decisions"]')
  .addEventListener("click", () =>
    decisionRefresh().catch((e) => toast(e.message, true)),
  );
setInterval(() => {
  if (
    state.page === "decisions" &&
    !document.hidden &&
    !$("#detail-dialog").open
  )
    decisionRefresh().catch(() => {});
}, 8000);
