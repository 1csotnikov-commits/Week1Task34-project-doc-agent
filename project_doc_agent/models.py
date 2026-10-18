"""Pydantic-модели домена."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_doc_agent.utils import is_slug, normalize_ws, slugify, today_iso, truncate


class Profile(StrEnum):
    task = "task"
    integration = "integration"


class Direction(StrEnum):
    kis_to_wms = "КИС>WMS"
    wms_to_kis = "WMS>КИС"
    both = "both"
    none = "none"


class Confidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class FactKind(StrEnum):
    integration_object = "integration_object"
    flow = "flow"
    requirement = "requirement"
    error_handling = "error_handling"
    auth = "auth"
    scope = "scope"
    glossary = "glossary"
    other = "other"


class ReviewStatus(StrEnum):
    ok = "ok"
    insufficient_data = "insufficient_data"
    needs_review = "needs_review"


class CardType(StrEnum):
    integration = "integration"
    reference = "reference"
    flow = "flow"
    requirement = "requirement"
    error = "error"
    auth = "auth"
    scope = "scope"
    glossary = "glossary"
    other = "other"
    project = "project"


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str
    file: str
    ext: str
    sha256: str
    size: int = 0
    modified: str = ""
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    block_count: int = 0
    table_count: int = 0
    image_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str
    file: str
    kind: Literal["heading", "paragraph", "list", "table", "code", "image"]
    text: str
    heading_path: list[str] = Field(default_factory=list)
    block_index: int
    table_index: int | None = None
    level: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def block_id(self) -> str:
        return f"{self.document_id}:b-{self.block_index:03d}"

    @property
    def section(self) -> str:
        return " / ".join(self.heading_path) if self.heading_path else "(без раздела)"

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip("\n")


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str
    section: str = ""
    block_id: str
    sha256: str | None = None


class Chunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    document_id: str
    file: str
    chunk_index: int
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    text: str = ""
    chars: int = 0
    table_count: int = 0

    @property
    def section(self) -> str:
        return " / ".join(self.heading_path) if self.heading_path else "(без раздела)"


class FactDraft(BaseModel):
    """Факт в том виде, в котором его вернула LLM (до стабилизации ID)."""

    model_config = ConfigDict(extra="ignore")

    fact_id: str = ""
    card_topic: str = "other"
    kind: FactKind = FactKind.other
    title: str
    summary: str = ""
    details: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    direction: Direction = Direction.none
    methods: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.medium
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("title", "summary")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return normalize_ws(value)

    @field_validator("card_topic")
    @classmethod
    def _slug_topic(cls, value: str) -> str:
        value = value.strip().lower()
        return value if is_slug(value) else slugify(value, fallback="other")

    @field_validator("details", "systems", "methods", "objects", "open_questions")
    @classmethod
    def _clean_lists(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = normalize_ws(str(item))
            if cleaned and cleaned.casefold() not in seen:
                seen.add(cleaned.casefold())
                result.append(cleaned)
        return result


class FactsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[FactDraft] = Field(default_factory=list)


class Fact(FactDraft):
    fact_id: str
    source_refs: list[SourceRef] = Field(min_length=1)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str
    why: str = ""
    cards: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    status: Literal["open", "answered"] = "open"
    answer: str | None = None


class Conflict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    title: str
    description: str
    statements: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class Card(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    client: str
    client_name: str
    project: str
    project_name: str
    bases: list[str] = Field(min_length=1)
    title: str
    type: CardType = CardType.other
    profile: Profile = Profile.integration
    status: str = "draft"
    risk: Literal["low", "medium", "high"] = "medium"
    sources: list[SourceRef] = Field(min_length=1)
    review_status: ReviewStatus = ReviewStatus.ok
    review_note: str | None = None
    tags: list[str] = Field(default_factory=list)
    created: str = Field(default_factory=today_iso)
    updated: str = Field(default_factory=today_iso)
    parent: str | None = None
    body: str = ""

    @field_validator("id", "client", "project")
    @classmethod
    def _slug_fields(cls, value: str) -> str:
        if not is_slug(value):
            raise ValueError(f"Ожидается slug (латиница, цифры, дефисы): {value!r}")
        return value

    @field_validator("bases")
    @classmethod
    def _bases_slugs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("bases не может быть пустым")
        for item in value:
            if not is_slug(item):
                raise ValueError(f"Некорректный slug базы: {item!r}")
        return value

    @model_validator(mode="after")
    def _project_id_matches(self) -> Card:
        prefix = f"{self.client}-{self.project}-"
        if self.type is not CardType.project and not self.id.startswith(prefix):
            raise ValueError(f"id карточки должен начинаться с {prefix!r}")
        return self

    def front_matter(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"body"})

    def to_markdown(self) -> str:
        front = yaml.safe_dump(
            self.front_matter(),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )
        return f"---\n{front}---\n\n{self.body.strip()}\n"


class RunStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class LLMCallRecord(BaseModel):
    provider: str
    model: str
    purpose: str
    prompt_sha256: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    cached: bool = False
    schema_name: str = ""


class ToolCallRecord(BaseModel):
    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    duration_ms: int = 0
    error: str | None = None


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    command: str
    started_at: str
    finished_at: str | None = None
    app_version: str
    status: RunStatus = RunStatus.running
    errors: list[str] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 8192
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    outputs: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    goal: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    def add_llm_call(self, record: LLMCallRecord) -> None:
        self.llm_calls.append(record)
        if record.cached:
            self.cache_hits += 1
        else:
            self.input_tokens += record.input_tokens
            self.output_tokens += record.output_tokens
            self.cost_usd = round(self.cost_usd + record.cost_usd, 6)


def short(value: str, limit: int = 80) -> str:
    return truncate(normalize_ws(value), limit)


class ManualReviewItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str
    section: str = ""
    reason: str
    kind: Literal[
        "image",
        "diagram",
        "unreadable",
        "too_large",
        "ambiguous",
        "skipped_chunk",
        "stale_card",
        "other",
    ] = "other"
    block_id: str | None = None

