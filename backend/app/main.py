from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from app.rag.indexer import knowledge_health
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.datasources import router as datasources_router
from app.api.elements import router as elements_router
from app.api.documents import router as documents_router
from app.api.sessions import router as sessions_router
from app.api.traces import router as traces_router

@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(knowledge_health)
    yield


app = FastAPI(title="小云助手", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sessions_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(datasources_router, prefix="/api")
app.include_router(elements_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(traces_router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
