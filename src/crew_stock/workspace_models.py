"""Durable research records. Model suggestions and human decisions have separate states."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Sourced(Strict):
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    source_quote: str = Field(
        min_length=1, max_length=4000, description="逐字摘录原文；不得翻译或改写"
    )


class QA(Strict):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)
    speaker: str = Field(default="未披露", max_length=200)


class Disclosure(Sourced):
    kind: Literal["financial_report", "earnings_call"]
    title: str = Field(min_length=1, max_length=240)
    period: str = Field(max_length=120)
    published_at: date | None = None
    coverage: Literal["full_text", "excerpt", "news_summary"]
    summary: str = Field(min_length=1, max_length=6000)
    key_points: list[str] = Field(default_factory=list, max_length=20)
    questions_answers: list[QA] = Field(default_factory=list, max_length=15)
    unanswered_questions: list[str] = Field(default_factory=list, max_length=15)


class Metric(Sourced):
    metric: str = Field(
        min_length=1,
        max_length=100,
        description="稳定指标名，例如 revenue / eps / gross_margin",
    )
    period: str = Field(min_length=1, max_length=100)
    period_start: date
    period_end: date
    basis: str = Field(
        min_length=1, max_length=100, description="GAAP / non-GAAP 等；不得混用"
    )
    unit: str = Field(
        min_length=1, max_length=80, description="原始单位，比较时必须相同"
    )
    currency: str = Field(
        min_length=1, max_length=20, description="ISO 币种或非金额 N/A"
    )
    value: Decimal = Field(max_digits=30, decimal_places=10)
    category: Literal["actual", "consensus", "guidance", "industry"]
    published_at: datetime
    direction: Literal["higher", "lower"] = "higher"

    @model_validator(mode="after")
    def chronology(self):
        if self.period_start > self.period_end:
            raise ValueError("期间起止日期颠倒")
        if self.published_at.tzinfo is None:
            raise ValueError("发布时间必须带时区")
        return self


class Promise(Sourced):
    title: str = Field(min_length=1, max_length=240)
    speaker: str = Field(min_length=1, max_length=200)
    promised_at: date
    due_date: date
    conditions: str = Field(default="", max_length=2000)
    metric: str = Field(default="", max_length=100)
    target: Decimal | None = Field(default=None, max_digits=30, decimal_places=10)
    operator: Literal["at_least", "at_most", "equal", "qualitative"] = "qualitative"
    period_start: date | None = None
    period_end: date | None = None
    basis: str = Field(default="", max_length=100)
    unit: str = Field(default="", max_length=80)
    currency: str = Field(default="", max_length=20)

    @model_validator(mode="after")
    def target_fields(self):
        if self.due_date < self.promised_at:
            raise ValueError("承诺截止日期早于提出日期")
        if self.operator != "qualitative" and (
            self.target is None
            or not all(
                [
                    self.metric,
                    self.period_start,
                    self.period_end,
                    self.basis,
                    self.unit,
                    self.currency,
                ]
            )
        ):
            raise ValueError("量化承诺必须填写目标、期间、指标、币种、单位与口径")
        if (
            self.period_start
            and self.period_end
            and self.period_start > self.period_end
        ):
            raise ValueError("期间起止日期颠倒")
        return self


class Extraction(Strict):
    disclosures: list[Disclosure] = Field(default_factory=list, max_length=6)
    promises: list[Promise] = Field(default_factory=list, max_length=12)
    metrics: list[Metric] = Field(default_factory=list, max_length=24)

    validation_issues: list[str] = Field(
        default_factory=list, max_length=30, description="程序校验提示；模型应留空"
    )

    @model_validator(mode="before")
    @classmethod
    def keep_valid_candidates(cls, value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        issues = []
        for field, schema in (
            ("disclosures", Disclosure),
            ("promises", Promise),
            ("metrics", Metric),
        ):
            retained = []
            for index, item in enumerate(result.get(field) or []):
                try:
                    retained.append(schema.model_validate(item))
                except ValueError:
                    issues.append(
                        f"{field} 第 {index + 1} 条候选字段不完整或格式无效，未进入台账"
                    )
            result[field] = retained
        result["validation_issues"] = issues[:30]
        return result


class RecordInput(Strict):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    kind: Literal["disclosure", "promise", "metric"]
    data: dict


class ReviewInput(Strict):
    decision: Literal["confirmed", "rejected"]
    note: str = Field(min_length=1, max_length=4000)


class ImportInput(Strict):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    title: str = Field(min_length=1, max_length=240)
    kind: Literal["financial_report", "earnings_call", "industry_data"]
    text: str = Field(min_length=20, max_length=200000)
    source_url: str = Field(default="", max_length=2000)
    published_at: date | None = None
    analyze: bool = True


class GroupInput(Strict):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    company_ids: list[str] = Field(min_length=2, max_length=6)

    @field_validator("company_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("公司不能重复")
        return value


class Assumption(Strict):
    text: str = Field(min_length=1, max_length=2000)
    invalidate_if: str = Field(min_length=1, max_length=2000)
    check_date: date


class InvestmentDecision(Strict):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    action: Literal["buy", "add", "reduce", "sell", "hold", "watch", "pass"]
    decided_at: datetime
    retrospective: bool = False
    rationale: str = Field(min_length=1, max_length=5000)
    alternatives: str = Field(min_length=1, max_length=3000)
    risks: str = Field(min_length=1, max_length=3000)
    assumptions: list[Assumption] = Field(min_length=1, max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    review_date: date

    @field_validator("decided_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("决策时间必须带时区")
        return value


class AssumptionResult(Strict):
    index: int = Field(ge=0, le=19)
    status: Literal["supported", "refuted", "unresolved"]
    explanation: str = Field(min_length=1, max_length=3000)


class DecisionReview(Strict):
    observed_at: date
    outcome: str = Field(min_length=1, max_length=4000)
    process_quality: str = Field(min_length=1, max_length=4000)
    lessons: str = Field(min_length=1, max_length=4000)
    assumption_results: list[AssumptionResult] = Field(min_length=1, max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)


class PromiseEvaluation(Strict):
    actual_metric_id: str | None = None
    status: Literal["fulfilled", "missed", "partial", "delayed", "unresolved"] = (
        "unresolved"
    )
    note: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
