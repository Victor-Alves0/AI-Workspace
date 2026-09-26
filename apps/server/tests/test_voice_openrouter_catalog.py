"""Voz pelo OpenRouter: cada modelo de fala aceita só as próprias vozes."""
from aiworkspace import voice_routes as v

CAT = [
    {"id": "fish-audio/s2-pro", "name": "Fish", "supported_voices": None},
    {"id": "google/gemini-3.8-flash-tts", "name": "Gemini", "supported_voices": ["Zephyr", "Kore"]},
    {"id": "x-ai/grok-voice-tts-1.0", "name": "Grok", "supported_voices": ["eve", "ara"]},
]


def test_catalogo_traz_as_vozes_de_cada_modelo():
    e = v._or_tts_entries(CAT)
    assert [m["voices"] for m in e] == [[], ["Zephyr", "Kore"], ["eve", "ara"]]
    assert all(m["provider"] == "OpenRouter" for m in e)


def test_padrao_vem_do_catalogo_vivo_e_tem_vozes():
    e = v._or_tts_entries(CAT)
    assert v._or_default_tts(e) == "google/gemini-3.8-flash-tts"
    assert v._or_default_tts(v._or_tts_entries([CAT[0]])) == "fish-audio/s2-pro"
    assert v._or_default_tts([]) is None


def test_voz_invalida_vira_uma_do_modelo_e_sem_lista_usa_a_dele():
    gemini, fish = v._or_tts_entries(CAT)[1], v._or_tts_entries(CAT)[0]
    assert v._or_pick_voice(gemini, "alloy") == "Zephyr"
    assert v._or_pick_voice(gemini, "kore") == "Kore"
    assert v._or_pick_voice(fish, "alloy") is None
    assert v._or_pick_voice(None, "alloy") is None


def test_pcm_vira_wav_tocavel():
    wav = v._pcm_to_wav(b"\x00\x01" * 100)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
