from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

MaterialType = Literal["lecture", "tutorial", "other", "all"]
RetrievalEngine = Literal["file_search", "hybrid"]


class CourseSummary(BaseModel):
    course: str
    lecture_count: int = 0
    tutorial_count: int = 0
    other_count: int = 0
    indexed_count: int = 0


class DocumentRecord(BaseModel):
    path: str
    filename: str
    course: str
    material_type: Literal["lecture", "tutorial", "other"]
    size_bytes: int = 0
    status: Literal["indexed", "pending", "missing"]


class DeleteDocumentRequest(BaseModel):
    path: str = Field(min_length=3, max_length=500)


class DeleteDocumentResponse(BaseModel):
    path: str
    local_deleted: bool
    index_deleted: bool


class SyncChange(BaseModel):
    path: str
    course: str
    material_type: str
    action: Literal["upload", "replace", "skip"]


class SyncReport(BaseModel):
    uploaded: int = 0
    replaced: int = 0
    skipped: int = 0
    failed: int = 0
    changes: List[SyncChange] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    removed: int = 0
    chunk_count: int = 0
    embedding_tokens: int = 0
    estimated_cost_usd: float = 0.0
    retryable_errors: int = 0
    retry_after_seconds: float = 0
    blocked_reason: Optional[str] = None


class IndexJob(BaseModel):
    id: str
    engine: Literal["hybrid"] = "hybrid"
    status: Literal["queued", "running", "succeeded", "failed"]
    phase: str
    processed: int
    total: int
    attempts: int
    max_attempts: int
    created_at: float
    updated_at: float
    available_at: float
    error_code: Optional[str] = None
    report: Optional[SyncReport] = None
    estimated_cost_usd: float = 0


class UploadResponse(BaseModel):
    document: DocumentRecord
    message: str
    index_job: Optional[IndexJob] = None


class ChatRequest(BaseModel):
    course: str = Field(min_length=1, max_length=80)
    material_type: MaterialType = "all"
    question: str = Field(min_length=2, max_length=4000)
    previous_response_id: Optional[str] = None
    conversation_id: Optional[str] = None
    engine: Optional[RetrievalEngine] = None


class SourceLocation(BaseModel):
    chunk_id: Optional[str] = None
    path: Optional[str] = None
    page: Optional[int] = None
    locator_type: Optional[Literal["page", "slide", "section"]] = None
    week: Optional[int] = None
    document_version: Optional[str] = None
    version_status: Optional[Literal["current", "stale", "unknown"]] = None


class Citation(SourceLocation):
    filename: str
    file_id: Optional[str] = None
    source_id: Optional[str] = None
    quote: str = ""


class Evidence(SourceLocation):
    filename: str
    score: Optional[float] = None
    text: str = ""
    source_id: Optional[str] = None
    bm25_score: Optional[float] = None
    vector_score: Optional[float] = None
    rerank_score: Optional[float] = None


class AnswerDiagnostics(BaseModel):
    reason: Literal["answered", "no_relevant_evidence", "generation_no_citations",
                    "invalid_model_output", "unknown_source_id", "quote_not_found",
                    "quote_too_short", "inline_citation_mismatch", "provider_refusal",
                    "response_incomplete", "source_changed"]
    stage: Literal["retrieval", "rerank", "generation", "validation"]
    evidence_count: int = 0
    citation_count: int = 0
    citation_markers_repaired: bool = False


class ChatResponse(BaseModel):
    answer: str
    response_id: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    diagnostics: Optional[AnswerDiagnostics] = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    course: str
    material_type: MaterialType
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationMessage(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    response_id: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    feedback: Optional[Literal["helpful", "unhelpful"]] = None
    feedback_note: Optional[str] = None
    created_at: datetime


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["helpful", "unhelpful"]
    note: Optional[str] = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    message_id: str
    rating: Literal["helpful", "unhelpful"]
