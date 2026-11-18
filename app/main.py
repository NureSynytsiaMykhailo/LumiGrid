from fastapi import FastAPI
from app.routers import auth, groups, luminaires, profiles, users
app = FastAPI(title="LumiGrid API", version="0.2.0")

for module in (auth, users, profiles, groups, luminaires):
    app.include_router(module.router)

@app.get("/health")
def health():
    return {"status": "ok"}