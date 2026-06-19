from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    niche: str
    city: str
    country: str
    initial_radius_km: float = Field(default=1.0, gt=0, le=50.0)
    max_depth: int = Field(default=4, ge=1, le=6)
    clinics_per_cell: int = Field(default=20, ge=1, le=100)


class CampaignOut(BaseModel):
    id: str
    status: str
    niche_id: str
    location_id: str
    initial_radius_km: float
    max_depth: int
    clinics_per_cell: int
    created_at: str
