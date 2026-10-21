from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

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
    IndexJob,
    RetrievalEngine,
    SyncChange,
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
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.index_jobs import (
    IndexBusyError,
    JobStore,
    index_writer,
    queue_path,
    queue_scope,
)
from app.services.library import discover_documents, summarize_courses
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.state import load_state
from app.services.study_agent import Confirmation, PlanRequest, StudyAgent, TaskUpdate
from app.services.study_feedback import FeedbackAgent
from app.services.study_feedback import FeedbackRequest as QuizAnswerRequest
from app.services.study_store import StudyConflict, StudyStore

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
            file_search_cost_per_thousand=config.openai_file_search_cost_per_thousand,
            max_retries=config.openai_max_retries,
            timeout_seconds=config.openai_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_chat_store(config: Settings = Depends(get_settings)) -> ChatStore:
    return ChatStore(config.conversation_db_path)


def get_hybrid_base(config: Settings = Depends(get_settings)) -> HybridKnowledgeBase:
    return HybridKnowledgeBase(config)


def get_job_store(config: Settings = Depends(get_settings)) -> JobStore:
    return JobStore(queue_path(config))


def get_study_store(config: Settings = Depends(get_settings)) -> StudyStore:
    return StudyStore(config.conversation_db_path.with_name("study_agent.sqlite3"))


def get_study_agent(config: Settings = Depends(get_settings),
                    store: StudyStore = Depends(get_study_store)) -> StudyAgent:
    return StudyAgent(config, store)


def get_feedback_agent(config: Settings = Depends(get_settings),
                       store: StudyStore = Depends(get_study_store)) -> FeedbackAgent:
    return FeedbackAgent(config, store)


@app.exception_handler(StudyConflict)
def study_conflict_handler(_request: Request, exc: StudyConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(IndexBusyError)
def index_busy_handler(_request: Request, _exc: IndexBusyError):
    return JSONResponse(status_code=409, content={"detail": "索引正在更新，请稍后重试。"},
                        headers={"Retry-After": "2"})


def index_state(config: Settings, engine: RetrievalEngine) -> dict:
    if engine == "hybrid":
        return {"files": HybridKnowledgeBase(config).manifest()}
    return load_state(config.index_state_path)


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
def courses(config: Settings = Depends(get_settings),
            engine: Optional[RetrievalEngine] = None) -> list[dict]:
    documents = discover_documents(config.knowledge_base_dir)
    state = index_state(config, engine or config.rag_engine)
    return summarize_courses(documents, state["files"].keys())


@app.get("/api/documents", response_model=list[DocumentRecord])
def documents(config: Settings = Depends(get_settings),
              engine: Optional[RetrievalEngine] = None) -> list[dict]:
    state = index_state(config, engine or config.rag_engine)
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
    hybrid_base: HybridKnowledgeBase = Depends(get_hybrid_base),
) -> DeleteDocumentResponse:
    try:
        target = resolve_document_path(config.knowledge_base_dir, request.path)
    except DocumentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with index_writer(config):
        index_deleted = knowledge_base.delete_document(request.path)
        index_deleted = hybrid_base.delete_document(request.path) or index_deleted
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
    engine: Optional[RetrievalEngine] = None,
) -> SyncReport:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    if (engine or config.rag_engine) == "hybrid":
        return HybridKnowledgeBase(config).sync(config.knowledge_base_dir, dry_run=True)
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
            SyncChange(
                path=document.relative_path,
                course=document.course,
                material_type=document.material_type,
                action=action,
            )
        )
    return report


@app.post("/api/sync", response_model=SyncReport)
def sync_documents(
    _rate_limit: None = Depends(enforce_admin_rate_limit),
    config: Settings = Depends(get_settings),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
    hybrid_base: HybridKnowledgeBase = Depends(get_hybrid_base),
    engine: Optional[RetrievalEngine] = None,
) -> SyncReport:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    base = hybrid_base if (engine or config.rag_engine) == "hybrid" else knowledge_base
    with index_writer(config):
        return base.sync(config.knowledge_base_dir)


@app.post("/api/index-jobs", response_model=IndexJob, status_code=202,
          dependencies=[Depends(enforce_admin_rate_limit)])
def create_index_job(
    config: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
    idempotency_key: Optional[str] = Header(default=None, min_length=1, max_length=128),
    engine: str = Query(default="hybrid", pattern="^hybrid$"),
) -> dict:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    return store.enqueue(queue_scope(config), idempotency_key)


@app.get("/api/index-jobs", response_model=list[IndexJob])
def index_jobs(config: Settings = Depends(get_settings),
               store: JobStore = Depends(get_job_store)) -> list:
    return store.recent(queue_scope(config))


@app.get("/api/index-jobs/{job_id}", response_model=IndexJob)
def index_job(job_id: str, config: Settings = Depends(get_settings),
              store: JobStore = Depends(get_job_store)) -> dict:
    job = store.get(job_id, queue_scope(config))
    if not job:
        raise HTTPException(status_code=404, detail="Index job not found")
    return job


@app.post("/api/index-jobs/{job_id}/retry", response_model=IndexJob, status_code=202,
          dependencies=[Depends(enforce_admin_rate_limit)])
def retry_index_job(job_id: str, config: Settings = Depends(get_settings),
                    store: JobStore = Depends(get_job_store)) -> dict:
    try:
        job = store.retry(job_id, queue_scope(config))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="Index job not found")
    return job


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    _rate_limit: None = Depends(enforce_chat_rate_limit),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
    chat_store: ChatStore = Depends(get_chat_store),
    hybrid_base: HybridKnowledgeBase = Depends(get_hybrid_base),
    config: Settings = Depends(get_settings),
) -> ChatResponse:
    try:
        kwargs = dict(
            course=request.course,
            material_type=request.material_type,
            question=request.question,
            previous_response_id=request.previous_response_id,
        )
        engine = request.engine or config.rag_engine
        if engine == "hybrid":
            history = []
            if request.conversation_id:
                conversations = {item.id: item for item in chat_store.list_conversations()}
                conversation = conversations.get(request.conversation_id)
                if not conversation or (conversation.course, conversation.material_type) != (
                    request.course, request.material_type
                ):
                    raise ValueError("Conversation scope does not match the request")
                history = [{"role": item.role, "content": item.content[:1000]}
                           for item in chat_store.get_messages(request.conversation_id)[-4:]]
            response = hybrid_base.chat(**kwargs, history=history)
        else:
            response = knowledge_base.chat(**kwargs)
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
        raise HTTPException(status_code=502, detail="AI 服务暂时不可用，请稍后再试。") from exc


@app.get("/api/sources/{chunk_id}")
def source_chunk(chunk_id: str, base: HybridKnowledgeBase = Depends(get_hybrid_base)) -> dict:
    source = base.source(chunk_id)
    if not source:
        raise HTTPException(status_code=404, detail="原文已变化或删除，请重新建立索引。")
    return source


@app.get("/api/sources/{chunk_id}/file")
def source_file(
    chunk_id: str, base: HybridKnowledgeBase = Depends(get_hybrid_base)
) -> FileResponse:
    source = base.source(chunk_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found or changed")
    path = resolve_document_path(base.config.knowledge_base_dir, source["path"])
    # Only the indexed PDF can be embedded inline. Other formats are downloads.
    return FileResponse(path, filename=source["filename"],
                        content_disposition_type="inline" if path.suffix.lower() == ".pdf"
                        else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@app.get("/api/conversations", response_model=list[ConversationSummary])
def conversations(chat_store: ChatStore = Depends(get_chat_store)) -> list[ConversationSummary]:
    return chat_store.list_conversations()


@app.post("/api/study-plans", dependencies=[Depends(enforce_chat_rate_limit)])
def draft_study_plan(request: PlanRequest, agent: StudyAgent = Depends(get_study_agent)):
    return agent.run(request)


@app.get("/api/study-plans")
def study_plans(course: str = Query(min_length=1, max_length=80),
                store: StudyStore = Depends(get_study_store)):
    return store.list(course)


@app.get("/api/study-plans/{plan_id}")
def study_plan(plan_id: str, store: StudyStore = Depends(get_study_store)):
    try:
        return store.get(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="计划不存在。") from exc


@app.post("/api/study-plans/{plan_id}/confirm",
          dependencies=[Depends(enforce_admin_rate_limit)])
def confirm_study_plan(plan_id: str, request: Confirmation,
                       store: StudyStore = Depends(get_study_store)):
    try:
        return store.confirm(plan_id, request.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="计划不存在。") from exc


@app.patch("/api/study-plans/{plan_id}/tasks/{task_id}",
           dependencies=[Depends(enforce_admin_rate_limit)])
def update_study_task(plan_id: str, task_id: str, request: TaskUpdate,
                      store: StudyStore = Depends(get_study_store)):
    try:
        return store.set_task(plan_id, task_id, request.done, request.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="计划或任务不存在。") from exc


@app.post("/api/study-plans/{plan_id}/tasks/{task_id}/answers",
          dependencies=[Depends(enforce_chat_rate_limit)])
def submit_study_answer(plan_id: str, task_id: str, request: QuizAnswerRequest,
                        agent: FeedbackAgent = Depends(get_feedback_agent)):
    try:
        return agent.run(plan_id, task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="计划或任务不存在。") from exc


@app.post("/api/study-plans/{plan_id}/feedbacks/{feedback_id}/confirm",
          dependencies=[Depends(enforce_admin_rate_limit)])
def confirm_study_feedback(plan_id: str, feedback_id: str, request: Confirmation,
                           store: StudyStore = Depends(get_study_store)):
    try:
        return store.confirm_feedback(plan_id, feedback_id, request.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="计划或反馈不存在。") from exc


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
