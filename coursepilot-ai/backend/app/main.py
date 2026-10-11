from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.models import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationSummary,
    CourseSummary,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    DocumentRecord,
    FeedbackRequest,
    FeedbackResponse,
    SyncReport,
    UploadResponse,
)
from app.services.chat_store import ChatStore
from app.services.documents import (
    DocumentValidationError,
    list_documents,
    resolve_document_path,
    save_upload,
)
from app.services.library import discover_documents, summarize_courses
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.state import load_state

app = FastAPI(
    title="CoursePilot AI API",
    description="Grounded course Q&A with incremental document indexing.",
    version="0.1.0",
)

settings = get_settings()
chat_limiter = SlidingWindowRateLimiter(settings.chat_requests_per_minute)
admin_limiter = SlidingWindowRateLimiter(settings.admin_requests_per_minute)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        {
            settings.frontend_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        }
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_knowledge_base(config: Settings = Depends(get_settings)) -> OpenAIKnowledgeBase:
    try:
        return OpenAIKnowledgeBase(
            api_key=config.openai_api_key,
            model=config.openai_model,
            state_path=config.index_state_path,
            max_results=config.max_search_results,
            telemetry_path=config.telemetry_path,
            input_cost_per_million=config.openai_input_cost_per_million,
            output_cost_per_million=config.openai_output_cost_per_million,
            max_retries=config.openai_max_retries,
            timeout_seconds=config.openai_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_chat_store(config: Settings = Depends(get_settings)) -> ChatStore:
    return ChatStore(config.conversation_db_path)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "local"


def enforce_chat_rate_limit(request: Request) -> None:
    retry_after = chat_limiter.check(_client_key(request))
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many questions. Please wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def enforce_admin_rate_limit(request: Request) -> None:
    retry_after = admin_limiter.check(_client_key(request))
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many document operations. Please wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


@app.get("/health")
def health(config: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "api_key_configured": bool(config.openai_api_key),
        "knowledge_base_exists": config.knowledge_base_dir.is_dir(),
    }


@app.get("/api/courses", response_model=list[CourseSummary])
def courses(config: Settings = Depends(get_settings)) -> list[dict]:
    documents = discover_documents(config.knowledge_base_dir)
    state = load_state(config.index_state_path)
    return summarize_courses(documents, state["files"].keys())


@app.get("/api/documents", response_model=list[DocumentRecord])
def documents(config: Settings = Depends(get_settings)) -> list[dict]:
    state = load_state(config.index_state_path)
    return list_documents(config.knowledge_base_dir, state)


@app.post(
    "/api/documents/upload",
    response_model=UploadResponse,
    dependencies=[Depends(enforce_admin_rate_limit)],
)
async def upload_document(
    request: Request,
    course: str = Query(min_length=1, max_length=80),
    material_type: str = Query(pattern="^(lecture|tutorial|other)$"),
    filename: str = Query(min_length=1, max_length=200),
    config: Settings = Depends(get_settings),
) -> UploadResponse:
    max_bytes = config.max_upload_mb * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {config.max_upload_mb} MB")
    try:
        target, size = await save_upload(
            config.knowledge_base_dir,
            course,
            material_type,
            filename,
            request.stream(),
            max_bytes,
        )
    except DocumentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    relative_path = target.relative_to(config.knowledge_base_dir.resolve()).as_posix()
    saved_course = Path(relative_path).parts[0]
    return UploadResponse(
        document=DocumentRecord(
            path=relative_path,
            filename=target.name,
            course=saved_course,
            material_type=material_type,
            size_bytes=size,
            status="pending",
        ),
        message="Upload complete. Run indexing to make it searchable.",
    )


@app.delete(
    "/api/documents",
    response_model=DeleteDocumentResponse,
    dependencies=[Depends(enforce_admin_rate_limit)],
)
def delete_document(
    request: DeleteDocumentRequest,
    config: Settings = Depends(get_settings),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
) -> DeleteDocumentResponse:
    try:
        target = resolve_document_path(config.knowledge_base_dir, request.path)
    except DocumentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    index_deleted = knowledge_base.delete_document(request.path)
    local_deleted = False
    if target.is_file():
        target.unlink()
        local_deleted = True
    if not local_deleted and not index_deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return DeleteDocumentResponse(
        path=request.path,
        local_deleted=local_deleted,
        index_deleted=index_deleted,
    )


@app.post("/api/sync/preview", response_model=SyncReport)
def preview_sync(
    config: Settings = Depends(get_settings),
) -> SyncReport:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    state = load_state(config.index_state_path)
    report = SyncReport()
    for document in discover_documents(config.knowledge_base_dir):
        existing = state["files"].get(document.relative_path)
        if existing and existing.get("sha256") == document.sha256:
            action = "skip"
            report.skipped += 1
        elif existing:
            action = "replace"
            report.replaced += 1
        else:
            action = "upload"
            report.uploaded += 1
        report.changes.append(
            {
                "path": document.relative_path,
                "course": document.course,
                "material_type": document.material_type,
                "action": action,
            }
        )
    return report


@app.post("/api/sync", response_model=SyncReport)
def sync_documents(
    _rate_limit: None = Depends(enforce_admin_rate_limit),
    config: Settings = Depends(get_settings),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
) -> SyncReport:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    return knowledge_base.sync(config.knowledge_base_dir)


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    _rate_limit: None = Depends(enforce_chat_rate_limit),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
    chat_store: ChatStore = Depends(get_chat_store),
) -> ChatResponse:
    try:
        response = knowledge_base.chat(
            course=request.course,
            material_type=request.material_type,
            question=request.question,
            previous_response_id=request.previous_response_id,
        )
        conversation_id, message_id = chat_store.save_exchange(
            conversation_id=request.conversation_id,
            course=request.course,
            material_type=request.material_type,
            question=request.question,
            answer=response,
        )
        response.conversation_id = conversation_id
        response.message_id = message_id
        return response
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}") from exc


@app.get("/api/conversations", response_model=list[ConversationSummary])
def conversations(chat_store: ChatStore = Depends(get_chat_store)) -> list[ConversationSummary]:
    return chat_store.list_conversations()


@app.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessage],
)
def conversation_messages(
    conversation_id: str, chat_store: ChatStore = Depends(get_chat_store)
) -> list[ConversationMessage]:
    return chat_store.get_messages(conversation_id)


@app.post("/api/feedback", response_model=FeedbackResponse)
def feedback(
    request: FeedbackRequest, chat_store: ChatStore = Depends(get_chat_store)
) -> FeedbackResponse:
    try:
        return chat_store.set_feedback(request.message_id, request.rating, request.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
