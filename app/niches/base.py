from dataclasses import dataclass, field

@dataclass
class NicheConfig:
    niche_id: str
    location: str
    limit: int = 10
    cutoff_months: int = 12

    search_term: str = ""
    categorize_system_prompt: str = ""
    cold_email_context: str = ""
    value_proposition: str = ""
    email_generator_persona: str = ""
    pain_points: list[str] = field(default_factory=list)
