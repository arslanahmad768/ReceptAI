from app.niches.dental import DentalConfig
from app.niches.base import NicheConfig

def test_dental_is_niche_config():
    cfg = DentalConfig(niche_id="abc", location="Tampa FL", limit=10)
    assert isinstance(cfg, NicheConfig)

def test_dental_has_all_required_fields():
    cfg = DentalConfig(niche_id="abc", location="Tampa FL")
    assert cfg.search_term
    assert cfg.categorize_system_prompt
    assert cfg.cold_email_context
    assert cfg.value_proposition
    assert len(cfg.pain_points) > 0

def test_dental_email_generator_persona_contains_key_elements():
    cfg = DentalConfig(niche_id="abc", location="Tampa FL")
    assert "dental" in cfg.email_generator_persona.lower()
    assert "missed" in cfg.email_generator_persona.lower()

def test_niche_config_defaults():
    cfg = DentalConfig(niche_id="x", location="Miami FL")
    assert cfg.limit == 10
    assert cfg.cutoff_months == 12
