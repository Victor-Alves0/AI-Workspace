"""Normalização de tags de artefato 'alternativas' (ex.: tokens de controle do
DeepSeek: <｜DSML｜tool artifact="true" …>) para a tag canônica <artifact …>."""
from __future__ import annotations

from aiworkspace.chat import artifacts


def test_normalize_alt_deepseek_artifact_tag():
    raw = (
        'Aqui está: <｜DSML｜tool artifact="true" identifier="leads" '
        'title="Leads CG">linha1\nlinha2</｜DSML｜tool> pronto'
    )
    out = artifacts._normalize_alt_artifact_tags(raw)
    assert "<artifact " in out
    assert 'identifier="leads"' in out
    assert 'title="Leads CG"' in out
    assert "</artifact>" in out
    assert "DSML" not in out  # o token de controle sumiu
    # e a extração canônica agora enxerga o artefato
    _clean, ops = artifacts.extract(out)
    assert len(ops) == 1
    assert ops[0]["identifier"] == "leads"
    assert "linha1" in ops[0]["content"] and "linha2" in ops[0]["content"]


def test_normalize_empty_alt_tag_is_stripped():
    # corpo vazio = só lixo do modelo: some da resposta (sem criar artefato vazio)
    raw = 'texto <｜DSML｜tool artifact="true" identifier="x" title="Y"> </｜DSML｜tool> fim'
    out = artifacts._normalize_alt_artifact_tags(raw)
    assert "DSML" not in out
    assert "<artifact" not in out
    assert "texto" in out and "fim" in out


def test_canonical_artifact_is_untouched():
    raw = '<artifact identifier="a" type="code" language="py">print(1)</artifact>'
    # a âncora é artifact="true" (que a tag canônica nunca emite) → nada muda
    assert artifacts._normalize_alt_artifact_tags(raw) == raw


def test_plain_text_untouched():
    assert artifacts._normalize_alt_artifact_tags("só um texto normal") == "só um texto normal"
