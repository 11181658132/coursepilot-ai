from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.models import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationSummary,
    CourseSummary,
    FeedbackRequest,
    FeedbackResponse,
    SyncReport,
)
from app.services.chat_store import ChatStore
from app.services.library import discover_documents, summarize_courses
from app.services.openai_kb import OpenAIKnowledgeBase
from app.services.state import load_state

app = FastAPI(
    title="CoursePilot AI API",
    description="Grounded course Q&A with incremental document indexing.",
    version="0.1.0",
)

settings = get_settings()
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_chat_store(config: Settings = Depends(get_settings)) -> ChatStore:
    return ChatStore(config.conversation_db_path)


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
    config: Settings = Depends(get_settings),
    knowledge_base: OpenAIKnowledgeBase = Depends(get_knowledge_base),
) -> SyncReport:
    if not config.knowledge_base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Knowledge base directory not found")
    return knowledge_base.sync(config.knowledge_base_dir)


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
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
