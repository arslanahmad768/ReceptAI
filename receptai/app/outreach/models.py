from pydantic import BaseModel


class GenerateEmailRequest(BaseModel):
    lead_id: str
    niche: str = "dental"


class CampaignCreate(BaseModel):
    name: str
    niche: str = "dental"


class BulkGenerateRequest(BaseModel):
    lead_ids: list[str]
    niche: str = "dental"
