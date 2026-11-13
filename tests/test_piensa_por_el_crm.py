"""Sin LLM_API_KEY, en modo dispatch, Nea piensa por el CRM con su misma llave."""
from app.config import Settings


def _s(**kw):
    base = dict(
        vocero_mode="dispatch",
        crm_base_url="https://crm.example.com/",
        crm_bot_api_key="bot-key",
    )
    base.update(kw)
    return Settings(**base)


def test_sin_llave_de_ia_piensa_por_el_crm():
    s = _s(llm_api_key="")
    assert s.piensa_por_el_crm
    assert s.llm_api_key_efectiva == "bot-key"
    assert s.llm_base_url_efectiva == "https://crm.example.com/api/bot/llm"


def test_con_llave_propia_no_cambia_nada():
    s = _s(llm_api_key="sk-propia", llm_base_url="https://openrouter.ai/api/v1")
    assert not s.piensa_por_el_crm
    assert s.llm_api_key_efectiva == "sk-propia"
    assert s.llm_base_url_efectiva == "https://openrouter.ai/api/v1"


def test_sin_llave_de_bot_no_inventa_nada():
    s = _s(llm_api_key="", crm_bot_api_key="")
    assert not s.piensa_por_el_crm
