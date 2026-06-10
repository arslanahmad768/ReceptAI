from pydantic import BaseModel
from typing import Optional

class CronJobCreate(BaseModel):
    name: str
    niche: str = "dental"
    location: str
    limit_count: int = 10
    cron_expr: str     # 5-part cron: "0 9 * * 1"

class CronJobOut(BaseModel):
    id: str
    name: str
    niche_id: str
    location: str
    limit_count: int
    cron_expr: str
    is_active: bool
    last_run_at: Optional[str]
    next_run_at: Optional[str]
