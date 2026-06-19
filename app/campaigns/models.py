from pydantic import BaseModel


class CampaignCreate(BaseModel):
    niche: str
    city: str
    country: str
    initial_radius_km: float = 1.0
    max_depth: int = 4
    clinics_per_cell: int = 20


class CampaignOut(BaseModel):
    id: str
    status: str
    niche_id: str
    location_id: str
    initial_radius_km: float
    max_depth: int
    clinics_per_cell: int
    created_at: str
