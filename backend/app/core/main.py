from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import get_current_user, UserInfo
from app.core.config import settings
from app.agents.router import router as agents_router
from app.mcps.router import router as mcps_router
from app.prompts.router import router as prompts_router
from app.skills.router import router as skills_router

app = FastAPI(title="Agent Platform API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(skills_router)
app.include_router(mcps_router)
app.include_router(prompts_router)
app.include_router(agents_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/me")
async def get_me(user: UserInfo = Depends(get_current_user)):
    return {
        "oid": user.oid,
        "name": user.name,
        "email": user.email,
        "tenant_id": user.tenant_id,
        "roles": user.roles,
    }
