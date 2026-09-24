from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    raw_content: str | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    error: str | None = None


class PageContent(BaseModel):
    url: str
    title: str
    text: str
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    error: str | None = None


class Fact(BaseModel):
    text: str
    source: str
    date: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))


class EntityRecord(BaseModel):
    name: str
    entity_type: str = "unknown"
    facts: list[Fact] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)
    last_updated: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))


class Claim(BaseModel):
    text: str
    citation: str | None = None
    confidence: str = "medium"


class CostRecord(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0

    def cost_inr(self, rate: float = 83.0) -> float:
        return self.cost_usd * rate


class AnalystAnswer(BaseModel):
    question: str
    plan: str = ""
    claims: list[Claim] = Field(default_factory=list)
    summary: str = ""
    sources_used: list[str] = Field(default_factory=list)
    tool_trace: list[dict] = Field(default_factory=list)
    cost: CostRecord = Field(default_factory=CostRecord)


class AuditVerdict(BaseModel):
    claim: Claim
    verdict: str  # supported / unsupported / contradicted / no_citation
    evidence: str = ""
    source_excerpt: str = ""


class AuditReport(BaseModel):
    analyst_question: str
    verdicts: list[AuditVerdict] = Field(default_factory=list)
    summary: str = ""
    cost: CostRecord = Field(default_factory=CostRecord)
