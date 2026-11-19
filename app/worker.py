import asyncio
import logging
from datetime import datetime, timezone

from app.db import SessionLocal
from app.logic import expire_commands, expire_overrides, mark_offline

log = logging.getLogger("lumigrid.worker")

def run_maintenance() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        expire_overrides(db, now)
        expire_commands(db, now)
        mark_offline(db, now)
        db.commit()

async def worker_loop(interval: int = 10) -> None:
    while True:
        try:
            await asyncio.to_thread(run_maintenance)
        except Exception:
            log.exception("Помилка фонового обслуговування")
        await asyncio.sleep(interval)
