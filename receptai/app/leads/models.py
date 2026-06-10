from pydantic import BaseModel
from typing import Optional

class LeadStatusUpdate(BaseModel):
    status: str

class LeadOut(BaseModel):
    id: str
    clinic_id: str
    email: Optional[str]
    email_method: Optional[str]
    status: str
