"""El marcador interno «[Nota de voz enviada]» nunca llega al lead como texto."""
import pytest

from app.turn import quitar_marcador_de_voz


def test_con_prefijo_lo_quita_y_deja_el_resto():
    assert quitar_marcador_de_voz("[Nota de voz enviada] Gracias por el dato") == ("Gracias por el dato", True)


def test_sin_prefijo_no_toca_nada():
    assert quitar_marcador_de_voz("Hola, ¿cómo estás?") == ("Hola, ¿cómo estás?", False)


def test_solo_prefijo_queda_vacio():
    assert quitar_marcador_de_voz("[Nota de voz enviada]") == ("", True)
    assert quitar_marcador_de_voz("  [Nota de voz enviada]  ") == ("", True)


@pytest.mark.parametrize(
    "texto",
    ["[NOTA DE VOZ ENVIADA] Listo", "  [nota de voz enviada]   Listo", "[ Nota  de voz enviada ]Listo", "\n[Nota de voz enviada]\nListo"],
)
def test_tolera_mayusculas_y_espacios(texto):
    assert quitar_marcador_de_voz(texto) == ("Listo", True)


def test_prefijo_repetido():
    assert quitar_marcador_de_voz("[Nota de voz enviada] [Nota de voz enviada] Listo") == ("Listo", True)


def test_el_marcador_en_medio_no_se_toca():
    texto = "Te dije [Nota de voz enviada] antes"
    assert quitar_marcador_de_voz(texto) == (texto, False)
