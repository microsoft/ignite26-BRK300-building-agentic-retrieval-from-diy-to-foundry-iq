from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx2
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import AzureDeveloperCliCredential, ManagedIdentityCredential
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, is_running_in_production
from .diy_agents import DEMOS, DIY_STEPS_BY_NAME, DiyAgentUnavailableError
from .diy_agents import ask as ask_diy_agent
from .diy_agents import list_steps as list_diy_steps
from .foundry_iq import FoundryIqService
from .naming import KNOWLEDGE_SOURCES, KNOWLEDGE_SOURCES_BY_KEY, KnowledgeSourceSpec

STATIC_DIR = Path(__file__).parent / "static"


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class DiyAskRequest(BaseModel):
    step: str
    question: str = Field(min_length=3, max_length=2000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_environment()
    credential = (
        ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        if is_running_in_production()
        else AzureDeveloperCliCredential()
    )
    app.state.settings = settings
    app.state.credential = credential
    app.state.service = FoundryIqService(settings, credential)
    yield
    app.state.service.index_client.close()
    app.state.service.blob_service.close()
    credential.close()


app = FastAPI(title="Agentic retrieval explorer", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.exception_handler(AzureError)
async def azure_service_error(_: Request, error: AzureError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Foundry IQ is unavailable. Verify FOUNDRY_IQ_SEARCH_ENDPOINT, authenticate with azd, "
                "and confirm the shared corpus has been provisioned."
            )
        },
    )


@app.exception_handler(DiyAgentUnavailableError)
async def diy_agent_unavailable(_: Request, error: DiyAgentUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                f"{error.step.name} is not running on port {error.step.port}. "
                "Start the DIY agents locally with: python -m scripts.run_diy_agents"
            )
        },
    )


@app.exception_handler(httpx2.HTTPError)
async def diy_agent_error(_: Request, error: httpx2.HTTPError) -> JSONResponse:
    detail = (
        "The DIY agent took too long to answer."
        if isinstance(error, httpx2.TimeoutException)
        else "The DIY agent returned an error. Check its output in the run_diy_agents terminal."
    )
    return JSONResponse(status_code=502, content={"detail": detail})


@app.get("/", include_in_schema=False)
@app.get("/knowledge-base/configuration", include_in_schema=False)
@app.get("/knowledge-base/query", include_in_schema=False)
@app.get("/diy-agents", include_in_schema=False)
@app.get("/demos", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sources/{key}/{page}", include_in_schema=False)
async def source_page(key: str, page: str) -> FileResponse:
    if key not in KNOWLEDGE_SOURCES_BY_KEY or page not in {"documents", "chunks", "search"}:
        raise HTTPException(status_code=404, detail="Page not found.")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def get_source(key: str) -> KnowledgeSourceSpec:
    source = KNOWLEDGE_SOURCES_BY_KEY.get(key)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge source: {key}")
    return source


@app.get("/api/sources")
async def sources() -> dict[str, Any]:
    return {"sources": [source.as_dict() for source in KNOWLEDGE_SOURCES]}


@app.get("/api/sources/{key}/documents")
async def source_documents(key: str, request: Request) -> dict[str, Any]:
    source = get_source(key)
    return await run_in_threadpool(
        request.app.state.service.list_documents,
        source.container,
        f"/api/sources/{source.key}/documents/content",
    )


@app.get("/api/sources/{key}/documents/content/{blob_name:path}")
async def source_document_content(key: str, blob_name: str, request: Request) -> Response:
    source = get_source(key)
    if not blob_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="Document not found.")
    content, _ = await run_in_threadpool(
        request.app.state.service.download_document,
        blob_name,
        source.container,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{Path(blob_name).name}"'},
    )


@app.get("/api/sources/{key}/chunks")
async def source_chunks(key: str, request: Request, document: str, limit: int = 100) -> dict[str, Any]:
    source = get_source(key)
    return await run_in_threadpool(
        request.app.state.service.list_chunks,
        source.index,
        document,
        min(limit, 500),
    )


@app.post("/api/sources/{key}/search")
async def search_source(key: str, payload: SearchRequest, request: Request) -> dict[str, Any]:
    source = get_source(key)
    return await run_in_threadpool(
        request.app.state.service.search_documents,
        source.index,
        payload.query,
        payload.limit,
    )


@app.get("/api/knowledge-base")
async def knowledge_base(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(request.app.state.service.get_knowledge_base_configuration)


@app.post("/api/retrieve")
async def retrieve(payload: RetrievalRequest, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(request.app.state.service.retrieve, payload.question)


@app.get("/api/diy/agents")
async def diy_agents(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"steps": await list_diy_steps(), "demos": [asdict(demo) for demo in DEMOS]}


@app.post("/api/diy/ask")
async def diy_ask(payload: DiyAskRequest) -> dict[str, Any]:
    step = DIY_STEPS_BY_NAME.get(payload.step)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Unknown DIY step: {payload.step}")
    return await ask_diy_agent(step, payload.question)


def find_cited_document(service: FoundryIqService, name: str) -> bytes:
    for source in KNOWLEDGE_SOURCES:
        try:
            content, _ = service.download_document(name, source.container)
        except ResourceNotFoundError:
            continue
        return content
    raise HTTPException(status_code=404, detail="Document not found.")


@app.get("/api/diy/documents/{name}")
async def diy_document(name: str, request: Request) -> Response:
    if not name.lower().endswith(".pdf") or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="Document not found.")
    content = await run_in_threadpool(find_cited_document, request.app.state.service, name)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )
