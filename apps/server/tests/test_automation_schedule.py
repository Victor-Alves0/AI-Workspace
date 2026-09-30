"""Horário das automações segue o FUSO pelo nome (horário de verão), não um offset fixo.

Antes o editor salvava só `tz_offset` (minutos, do dia em que a automação foi criada):
"todo dia às 9h" em Nova York rodava às 8h ou 10h metade do ano."""
from __future__ import annotations

from datetime import datetime, timezone

from aiworkspace.automation.scheduler import _next_at


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_daily_keeps_local_hour_across_dst():
    s = {"mode": "daily", "time": "09:00", "tz": "America/New_York", "tz_offset": 300}
    assert _next_at(s, _utc(2026, 1, 10, 12, 0)) == _utc(2026, 1, 10, 14, 0)   # inverno, UTC-5
    assert _next_at(s, _utc(2026, 7, 10, 12, 0)) == _utc(2026, 7, 10, 13, 0)   # verão, UTC-4


def test_without_zone_name_falls_back_to_offset():
    s = {"mode": "daily", "time": "09:00", "tz_offset": 180}
    assert _next_at(s, _utc(2026, 7, 10, 20, 0)) == _utc(2026, 7, 11, 12, 0)
    # nome inválido também cai no offset, sem quebrar
    s["tz"] = "Lugar/Inexistente"
    assert _next_at(s, _utc(2026, 7, 10, 20, 0)) == _utc(2026, 7, 11, 12, 0)


def test_weekly_and_monthly_in_zone():
    wk = {"mode": "weekly", "time": "08:30", "days": [1], "tz": "America/Sao_Paulo"}
    assert _next_at(wk, _utc(2026, 9, 30, 12, 0)) == _utc(2026, 10, 5, 11, 30)  # segunda
    mo = {"mode": "monthly", "time": "10:00", "day": 31, "tz": "America/Sao_Paulo"}
    assert _next_at(mo, _utc(2026, 2, 1, 0, 0)) == _utc(2026, 2, 28, 13, 0)      # clampa fevereiro
