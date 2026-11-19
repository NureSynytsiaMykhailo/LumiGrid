import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import (
    acts, auth, device, groups, luminaires, monitoring, overrides, profiles, rules, users,
)
from app.worker import worker_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()

app = FastAPI(title="LumiGrid API", version="0.3.0", lifespan=lifespan)

for module in (auth, users, profiles, groups, luminaires, rules,
               overrides, monitoring, device, acts):
    app.include_router(module.router)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}