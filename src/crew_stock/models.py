import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workspace_models import Extraction

SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}\.(US|HK|SH|SZ|SG)$")


def normalize_symbol(value: str) -> str:
    value = value.strip().upper()
    if not SYMBOL.fullmatch(value):
        raise ValueError("证券代码格式应为 NVDA.US、700.HK、600519.SH 等")
    return value


class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    symbols: list[str] = Field(min_length=1, max_length=10)
    purpose: str = Field(default="长期跟踪基本面、预期变化与反方证据", max_length=2000)

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, values):
        return list(dict.fromkeys(normalize_symbol(x) for x in values))


class RunInput(BaseModel):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    symbol: str
    mode: Literal["research", "collect", "archive"] = "research"
    source_document_id: str | None = Field(default=None, pattern=r"^src-\d{6}$")
    question: str = Field(
        default="分析最新基本面、市场预期与风险，明确反方证据和数据缺口。",
        min_length=1,
        max_length=4000,
    )

    @field_validator("symbol")
    @classmethod
    def symbol_valid(cls, value):
        return normalize_symbol(value)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: Literal[
        "fact", "inference", "sentiment", "opinion", "user_annotation"
    ]
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    as_of: str | None = Field(
        description="证据中的业务日期；未提供则为 null，禁止用采集时间替代"
    )
    confidence: Literal["low", "medium", "high"]


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=5000)
    claims: list[Claim] = Field(min_length=1, max_length=30)
    changes: list[str] = Field(max_length=20)
    counter_evidence: list[str] = Field(max_length=20)
    gaps: list[str] = Field(max_length=20)
    thesis_proposal: str = Field(
        max_length=5000,
        description="待用户确认的投资逻辑建议，不能标为已确认或交易指令",
    )
    next_questions: list[str] = Field(max_length=15)

    extraction: Extraction = Field(default_factory=Extraction)
