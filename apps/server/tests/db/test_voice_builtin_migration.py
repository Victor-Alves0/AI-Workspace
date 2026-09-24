"""0081: a Voz Local no endereço do antigo serviço Kokoro vira voz embutida; um servidor
de voz próprio (outro endereço) fica como estava."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from .conftest import migrar, pytestmark  # noqa: F401


def test_voz_local_do_kokoro_antigo_vira_embutida(engine):
    migrar(engine, "0080_whatsapp_history")
    velho, proprio = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        for uid in (velho, proprio):
            c.execute(text("INSERT INTO users (id, email, hashed_password) VALUES (:i, :e, 'x')"),
                      {"i": uid, "e": f"{uid.hex[:8]}@t.local"})
        for uid, base in ((velho, "http://localhost:8880/v1"), (proprio, "http://192.168.1.50:8000/v1")):
            c.execute(text("INSERT INTO app_settings (id, key, value) VALUES (:i, :k, CAST(:v AS jsonb))"),
                      {"i": uuid.uuid4(), "k": f"voice:{uid}",
                       "v": json.dumps({"v": {"enabled": True, "base_url": base, "tts_model": "kokoro"}})})
            c.execute(text("INSERT INTO model_configs (id, user_id, name, base_model, filter_config) "
                           "VALUES (:i, :u, 'm', 'x/y', CAST(:f AS jsonb))"),
                      {"i": uuid.uuid4(), "u": uid, "f": json.dumps(
                          {"voice": {"tts_provider": "local", "tts_model": "kokoro", "tts_enabled": True},
                           "outro": 1})})

    migrar(engine, "0081_voice_local_to_builtin")

    with engine.connect() as c:
        chaves = set(c.execute(text("SELECT key FROM app_settings WHERE key LIKE 'voice:%'")).scalars())
        vozes = dict(c.execute(text("SELECT user_id, filter_config FROM model_configs")).all())
    assert chaves == {f"voice:{proprio}"}
    assert vozes[velho] == {"voice": {"tts_provider": "builtin", "tts_enabled": True}, "outro": 1}
    assert vozes[proprio]["voice"]["tts_provider"] == "local"  # servidor próprio intacto
