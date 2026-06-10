from pydantic import BaseModel

class CronJobCreate(BaseModel):
    name: str
    niche: str = "dental"
    location: str
    limit_count: int = 10
    cron_expr: str     # 5-part cron: "0 9 * * 1"
