"""Tuya / Smart Life Cloud: controle de dispositivos (luzes, tomadas, ar, cenas).

Conexão por HMAC-SHA256 (access_id/access_secret de um projeto Tuya Cloud com a conta
Smart Life vinculada). Config é POR-USUÁRIO (cada um liga a própria casa — self-hosted
com família/amigos), em `app_settings` sob a chave `tuya:{user_id}` (secret cifrado):

    {base_url, access_id, access_secret_enc, devices[], aliases{}, scenes[]}

Descoberta automática (o usuário NÃO digita device_id/códigos):
  - `sync()` lista os dispositivos via `/v1.0/iot-01/associated-users/devices` e as cenas
    tap-to-run (via as casas do usuário) — popula `devices[]` e `scenes[]`.
  - O controle usa a ESPECIFICAÇÃO real de cada dispositivo
    (`/v1.2/iot-03/devices/{id}/specification`) — códigos de função e faixas exatos do
    hardware — cacheada em memória. Sem `profiles` preenchido à mão.
  - O modelo só vê nome/categoria/estado compacto; os códigos DP ficam escondidos na tool
    (economia de tokens).

Chamadas HTTP SÍNCRONAS (httpx.Client): a tool SIFT roda no threadpool de dispatch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.tuyaus.com"
COMMAND_PATH = "/v1.0/iot-03/devices/{device_id}/commands"

# access_id -> (access_token, expiry_epoch)
_token_cache: dict[str, tuple[str, float]] = {}
# device_id -> spec parseado {category, functions:{code:{type,values}}, status:[codes]}
_spec_cache: dict[str, dict] = {}

# categorias Tuya tratadas como ar-condicionado/climatização (têm temp+modo)
_AC_CATEGORIES = {"kt", "ktkzq", "ktqyz", "ktkg", "qn", "rs"}


def _key(user_id: str) -> str:
    """Chave por-usuário no app_settings (config individual, não global)."""
    return f"tuya:{user_id}"


def _norm_devices(v: Any) -> list[dict]:
    """Aceita a forma nova (lista [{id,name,category,online}]) E a legada
    (dict {nome: device_id}, da config manual anterior à descoberta)."""
    if isinstance(v, list):
        return [d for d in v if isinstance(d, dict) and d.get("id")]
    if isinstance(v, dict):
        return [{"id": str(i), "name": str(n), "category": "", "online": True}
                for n, i in v.items() if i]
    return []


def _norm_scenes(v: Any) -> list[dict]:
    """Nova (lista [{id,name}]) ou legada (dict {nome: rule_id})."""
    if isinstance(v, list):
        return [s for s in v if isinstance(s, dict) and s.get("id")]
    if isinstance(v, dict):
        return [{"id": str(i), "name": str(n)} for n, i in v.items() if i]
    return []


# --------------------------------------------------------------------------- #
# Config por-usuário (app_settings)
# --------------------------------------------------------------------------- #
async def get_config(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """Config completa do usuário (secret decifrado). None = sem creds."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict):
        return None
    access_id = (raw.get("access_id") or "").strip()
    enc = raw.get("access_secret_enc") or ""
    if not access_id or not enc:
        return None
    try:
        secret = crypto.decrypt(enc)
    except Exception:  # noqa: BLE001 - APP_SECRET trocado invalida o ciphertext
        return None
    return {
        "base_url": (raw.get("base_url") or DEFAULT_BASE_URL).strip(),
        "access_id": access_id,
        "access_secret": secret,
        "devices": _norm_devices(raw.get("devices")),
        "aliases": raw.get("aliases") or {},
        "scenes": _norm_scenes(raw.get("scenes")),
    }


async def set_config(
    db: AsyncSession,
    user_id: str,
    *,
    base_url: str | None = None,
    access_id: str | None = None,
    access_secret: str | None = None,
    devices: list | None = None,
    aliases: dict | None = None,
    scenes: list | None = None,
) -> None:
    """Atualização PARCIAL: só sobrescreve os campos passados (None = mantém).
    Assim salvar creds não apaga o catálogo sincronizado, e sincronizar não apaga
    creds/apelidos. `access_secret=''/None` mantém o secret atual."""
    cur = await get_setting(db, _key(user_id))
    out = dict(cur) if isinstance(cur, dict) else {}
    if base_url is not None:
        out["base_url"] = (base_url or DEFAULT_BASE_URL).strip()
    if access_id is not None:
        out["access_id"] = access_id.strip()
    if access_secret:
        out["access_secret_enc"] = crypto.encrypt(access_secret.strip())
        _token_cache.pop((access_id or out.get("access_id") or "").strip(), None)
    if devices is not None:
        out["devices"] = devices
    if aliases is not None:
        out["aliases"] = aliases
    if scenes is not None:
        out["scenes"] = scenes
    out.setdefault("base_url", DEFAULT_BASE_URL)
    out.setdefault("access_secret_enc", "")
    out.setdefault("devices", [])
    out.setdefault("aliases", {})
    out.setdefault("scenes", [])
    await set_setting(db, _key(user_id), out)


async def public_config(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Config do usuário sem o secret, para a UI."""
    raw = await get_setting(db, _key(user_id))
    raw = raw if isinstance(raw, dict) else {}
    access_id = (raw.get("access_id") or "").strip()
    return {
        "configured": bool(access_id and (raw.get("access_secret_enc") or "")),
        "base_url": (raw.get("base_url") or DEFAULT_BASE_URL).strip(),
        "access_id": access_id,
        "has_secret": bool(raw.get("access_secret_enc")),
        "devices": _norm_devices(raw.get("devices")),
        "aliases": raw.get("aliases") or {},
        "scenes": _norm_scenes(raw.get("scenes")),
    }


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    return await get_config(db, user_id) is not None


# --------------------------------------------------------------------------- #
# Assinatura HMAC + requisição (síncrono)
# --------------------------------------------------------------------------- #
def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sign_headers(conn: dict, method: str, path_with_query: str, body_str: str,
                  access_token: str = "") -> dict[str, str]:
    t = str(int(time.time() * 1000))
    string_to_sign = f"{method.upper()}\n{_sha256(body_str)}\n\n{path_with_query}"
    if access_token:
        sign_source = f"{conn['access_id']}{access_token}{t}{string_to_sign}"
    else:
        sign_source = f"{conn['access_id']}{t}{string_to_sign}"
    sign = hmac.new(
        conn["access_secret"].encode("utf-8"),
        sign_source.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    headers = {
        "client_id": conn["access_id"],
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256",
        "Content-Type": "application/json",
    }
    if access_token:
        headers["access_token"] = access_token
    return headers


def _raw_request(conn: dict, method: str, path_with_query: str,
                 body: dict | None = None, access_token: str = "") -> dict[str, Any]:
    if not conn.get("access_id") or not conn.get("access_secret"):
        return {"success": False, "msg": "Tuya não configurado (access_id/secret)."}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
    headers = _sign_headers(conn, method, path_with_query, body_str, access_token)
    url = conn["base_url"].rstrip("/") + path_with_query
    try:
        with httpx.Client(timeout=25) as client:
            resp = client.request(
                method.upper(), url, headers=headers,
                content=body_str.encode("utf-8") if body_str else None,
            )
    except httpx.RequestError as e:
        return {"success": False, "msg": f"erro de rede: {e}"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"success": False, "status_code": resp.status_code, "text": resp.text[:300]}
    if resp.status_code >= 400 and "success" not in data:
        data["success"] = False
        data["status_code"] = resp.status_code
    return data


def _get_token(conn: dict) -> str:
    access_id = conn["access_id"]
    now = time.time()
    hit = _token_cache.get(access_id)
    if hit and now < hit[1] - 60:
        return hit[0]
    result = _raw_request(conn, "GET", "/v1.0/token?grant_type=1")
    if not result.get("success"):
        raise RuntimeError(f"erro ao obter token Tuya: {result.get('msg') or result.get('code') or result}")
    token_data = result.get("result", {})
    token = token_data.get("access_token")
    if not token:
        raise RuntimeError("resposta de token sem access_token")
    _token_cache[access_id] = (token, now + int(token_data.get("expire_time", 7200)))
    return token


_TOKEN_ERR = {"1010", "1011", "1012", "1013", "1014", "1015"}


def _request(conn: dict, method: str, path_with_query: str,
             body: dict | None = None) -> dict[str, Any]:
    try:
        token = _get_token(conn)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "msg": str(e)}
    result = _raw_request(conn, method, path_with_query, body, token)
    if str(result.get("code")) in _TOKEN_ERR:
        _token_cache.pop(conn["access_id"], None)
        try:
            token = _get_token(conn)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "msg": str(e)}
        result = _raw_request(conn, method, path_with_query, body, token)
    return result


# --------------------------------------------------------------------------- #
# Descoberta (dispositivos + cenas)
# --------------------------------------------------------------------------- #
def _discover_devices(conn: dict) -> tuple[list[dict], set[str]]:
    """Todos os dispositivos das contas vinculadas ao projeto. Devolve
    ([{id,name,category,online}], {uids})."""
    out: list[dict] = []
    uids: set[str] = set()
    last = ""
    for _ in range(20):  # teto de páginas
        path = "/v1.0/iot-01/associated-users/devices?size=100"
        if last:
            path += f"&last_row_key={last}"
        r = _request(conn, "GET", path)
        res = r.get("result") or {}
        for d in res.get("devices", []) or []:
            did = d.get("id")
            if not did:
                continue
            out.append({
                "id": did,
                "name": d.get("name") or did,
                "category": d.get("category", ""),
                "online": bool(d.get("online")),
            })
            if d.get("uid"):
                uids.add(str(d["uid"]))
        if not res.get("has_next"):
            break
        last = res.get("last_row_key") or ""
        if not last:
            break
    return out, uids


def _discover_scenes(conn: dict, uids: set[str]) -> list[dict]:
    """Cenas tap-to-run das casas do usuário (best-effort; [] se indisponível)."""
    scenes: list[dict] = []
    seen_home: set[str] = set()
    seen_scene: set[str] = set()
    for uid in uids:
        homes = _request(conn, "GET", f"/v1.0/users/{uid}/homes").get("result") or []
        for h in homes if isinstance(homes, list) else []:
            hid = str(h.get("home_id") or h.get("id") or "")
            if not hid or hid in seen_home:
                continue
            seen_home.add(hid)
            r = _request(conn, "GET", f"/v2.0/cloud/scene/rule?space_id={hid}&type=scene&page_size=100")
            res = r.get("result") or {}
            items = res.get("list") or res.get("rules") or (res if isinstance(res, list) else [])
            for s in items if isinstance(items, list) else []:
                sid = s.get("id") or s.get("rule_id")
                if not sid or sid in seen_scene:
                    continue
                seen_scene.add(sid)
                scenes.append({"id": sid, "name": s.get("name", "") or sid})
    return scenes


def sync(conn: dict) -> dict[str, Any]:
    """Descobre dispositivos + cenas. Levanta em falha de rede/creds crítica."""
    devices, uids = _discover_devices(conn)
    try:
        scenes = _discover_scenes(conn, uids)
    except Exception as e:  # noqa: BLE001 - cenas são bônus, não quebram o sync
        logger.info("Descoberta de cenas Tuya falhou (segue sem cenas): %s", e)
        scenes = []
    _spec_cache.clear()  # catálogo mudou → re-descobre specs sob demanda
    return {"devices": devices, "scenes": scenes}


# --------------------------------------------------------------------------- #
# Especificação (códigos reais do hardware) — cacheada
# --------------------------------------------------------------------------- #
def _get_spec(conn: dict, device_id: str) -> dict:
    """Spec parseado do device: {category, functions:{code:{type,values}}}. Cacheado."""
    if device_id in _spec_cache:
        return _spec_cache[device_id]
    r = _request(conn, "GET", f"/v1.2/iot-03/devices/{device_id}/specification")
    res = r.get("result") or {}
    funcs: dict[str, dict] = {}
    for f in res.get("functions", []) or []:
        code = f.get("code")
        if not code:
            continue
        vals = f.get("values")
        try:
            parsed = json.loads(vals) if isinstance(vals, str) else (vals or {})
        except Exception:  # noqa: BLE001
            parsed = {}
        funcs[code] = {"type": f.get("type", ""), "values": parsed}
    spec = {"category": res.get("category", ""), "functions": funcs}
    if funcs:
        _spec_cache[device_id] = spec
    return spec


def _find_switch(spec: dict, channel: int = 1) -> str | None:
    """Código booleano de liga/desliga (luz/tomada/interruptor/power do ar)."""
    funcs = spec.get("functions", {})
    for code in (f"switch_{channel}", "switch_led", "switch", "PowerSwitch", "power_switch"):
        if funcs.get(code, {}).get("type") == "Boolean":
            return code
    for code, f in funcs.items():
        if f.get("type") == "Boolean" and (code.startswith("switch") or "power" in code.lower()):
            return code
    return None


def _find_func(spec: dict, ftype: str, names: tuple[str, ...]) -> tuple[str, dict] | None:
    """Primeira função do tipo `ftype` cujo código bate (exato, depois substring)."""
    funcs = spec.get("functions", {})
    for n in names:
        f = funcs.get(n)
        if f and f.get("type") == ftype:
            return n, f.get("values", {})
    for code, f in funcs.items():
        if f.get("type") == ftype and any(n in code for n in names):
            return code, f.get("values", {})
    return None


_MODE_SYN = {
    "frio": "cold", "gelar": "cold", "cool": "cold", "cold": "cold",
    "quente": "hot", "heat": "hot", "hot": "hot",
    "auto": "auto", "automatico": "auto",
    "seco": "wet", "dry": "wet", "wet": "wet", "desumidificar": "wet",
    "ventilar": "wind", "fan": "wind", "wind": "wind", "ventilador": "wind",
}
_FAN_SYN = {
    "auto": "auto", "baixo": "low", "low": "low", "min": "low",
    "media": "middle", "medio": "middle", "middle": "middle", "mid": "middle", "medium": "middle",
    "alto": "high", "high": "high", "max": "high", "forte": "high",
}


def _pick_enum(rng: list, wanted: str, syn: dict) -> str | None:
    if not rng:
        return None
    w = syn.get(_normalize(wanted), _normalize(wanted))
    for v in rng:
        if _normalize(v) == w or _normalize(v) == _normalize(wanted):
            return v
    return rng[0]


# --------------------------------------------------------------------------- #
# Resolução de dispositivos / cenas
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def resolve_device(conn: dict, text: str) -> dict | None:
    """Device {id,name,category,online} a partir de nome/apelido/id; None se não achar."""
    devices: list = conn.get("devices") or []
    aliases: dict = conn.get("aliases") or {}
    if not devices:
        return None
    raw = str(text or "").strip()
    norm = _normalize(raw)

    def _match_target(target: str) -> dict | None:
        tn = _normalize(str(target))
        for d in devices:
            if str(d.get("id")) == str(target) or _normalize(d.get("name", "")) == tn:
                return d
        return None

    # apelido → alvo (id ou nome)
    target = aliases.get(raw)
    if target is None:
        for a, t in aliases.items():
            if _normalize(a) == norm:
                target = t
                break
    if target is not None:
        hit = _match_target(target)
        if hit:
            return hit
    # id direto
    for d in devices:
        if str(d.get("id")) == raw:
            return d
    # nome exato (normalizado)
    for d in devices:
        if _normalize(d.get("name", "")) == norm:
            return d
    # sobreposição de tokens (fuzzy)
    nt = {t for t in norm.split("_") if t}
    best = None
    for d in devices:
        dt = set(_normalize(d.get("name", "")).split("_"))
        ov = nt & dt
        if ov and len(ov) >= min(2, len(nt), len(dt)):
            best = d
    return best


def _resolve_scene(conn: dict, text: str) -> dict | None:
    raw = str(text or "").strip()
    norm = _normalize(raw)
    for s in conn.get("scenes") or []:
        if str(s.get("id")) == raw or _normalize(s.get("name", "")) == norm:
            return s
    for s in conn.get("scenes") or []:  # fuzzy: substring
        if norm and norm in _normalize(s.get("name", "")):
            return s
    return None


def _is_ac(device: dict, spec: dict) -> bool:
    if (device.get("category") or "") in _AC_CATEGORIES:
        return True
    # heurística: tem função de temperatura → tratamos como climatizador
    return _find_func(spec, "Integer", ("temp_set", "temp_value", "TempSet")) is not None


# --------------------------------------------------------------------------- #
# Operações de alto nível (usadas pela tool SIFT) — retornos ENXUTOS
# --------------------------------------------------------------------------- #
def list_devices(conn: dict) -> dict[str, Any]:
    """Catálogo compacto (não faz rede). Sem ids/códigos → poucos tokens."""
    devices = [
        {"name": d.get("name"), "category": d.get("category", ""), "online": bool(d.get("online"))}
        for d in (conn.get("devices") or [])
    ]
    scenes = [s.get("name") for s in (conn.get("scenes") or [])]
    return {"devices": devices, "scenes": scenes}


def _send(conn: dict, device_id: str, commands: list[dict]) -> dict[str, Any]:
    return _request(conn, "POST", COMMAND_PATH.format(device_id=device_id), {"commands": commands})


def _temp_factor(values: dict) -> int:
    try:
        return 10 ** int(values.get("scale", 0) or 0)
    except (TypeError, ValueError):
        return 1


def device_state(conn: dict, device: str) -> dict[str, Any]:
    """Estado compacto de um dispositivo (ligado, e p/ ar: temp/modo/ventilação)."""
    d = resolve_device(conn, device)
    if not d:
        return {"error": f"dispositivo '{device}' não encontrado"}
    spec = _get_spec(conn, d["id"])
    r = _request(conn, "GET", f"/v1.0/iot-03/devices/{d['id']}/status")
    status = {s.get("code"): s.get("value") for s in (r.get("result") or [])}
    out: dict[str, Any] = {"device": d["name"], "online": bool(d.get("online"))}
    sw = _find_switch(spec)
    if sw and sw in status:
        out["on"] = bool(status[sw])
    if _is_ac(d, spec):
        tf = _find_func(spec, "Integer", ("temp_set", "temp_value", "TempSet"))
        if tf and tf[0] in status:
            out["temperature"] = status[tf[0]] / _temp_factor(tf[1])
        mf = _find_func(spec, "Enum", ("mode",))
        if mf and mf[0] in status:
            out["mode"] = status[mf[0]]
        ff = _find_func(spec, "Enum", ("windspeed", "fan_speed", "fan", "speed"))
        if ff and ff[0] in status:
            out["fan"] = status[ff[0]]
    return out


def set_switch(conn: dict, device: str, on: bool, channel: int = 1) -> dict[str, Any]:
    """Liga/desliga luz, tomada, interruptor ou o power de um ar."""
    d = resolve_device(conn, device)
    if not d:
        return {"error": f"dispositivo '{device}' não encontrado"}
    spec = _get_spec(conn, d["id"])
    code = _find_switch(spec, channel)
    if not code:
        return {"error": f"não encontrei um interruptor em '{d['name']}' (veja o status do dispositivo)."}
    res = _send(conn, d["id"], [{"code": code, "value": bool(on)}])
    if res.get("success") is True:
        return {"ok": True, "device": d["name"], "action": "on" if on else "off"}
    return {"error": f"falha ao controlar {d['name']}: {res.get('msg') or res.get('code') or res}"}


def configure_ac(conn: dict, temperature: int = 23, mode: str = "frio",
                 fan: str = "auto", device: str = "ar") -> dict[str, Any]:
    """Liga e ajusta o ar usando os códigos REAIS do dispositivo (spec)."""
    d = resolve_device(conn, device)
    if not d:
        return {"error": f"dispositivo '{device}' não encontrado"}
    spec = _get_spec(conn, d["id"])
    commands: list[dict[str, Any]] = []
    power = _find_switch(spec)
    if power:
        commands.append({"code": power, "value": True})
    tf = _find_func(spec, "Integer", ("temp_set", "temp_value", "TempSet"))
    if not tf:
        return {"error": f"'{d['name']}' não expõe controle de temperatura — use ligar/desligar."}
    code, vals = tf
    factor = _temp_factor(vals)
    val = int(round(float(temperature) * factor))
    mn, mx = vals.get("min"), vals.get("max")
    if isinstance(mn, (int, float)) and isinstance(mx, (int, float)) and not (mn <= val <= mx):
        return {"error": f"temperatura {temperature}°C fora do limite ({mn / factor:.0f}-{mx / factor:.0f}°C)"}
    commands.append({"code": code, "value": val})
    mf = _find_func(spec, "Enum", ("mode",))
    if mf:
        picked = _pick_enum(mf[1].get("range", []), mode, _MODE_SYN)
        if picked:
            commands.append({"code": mf[0], "value": picked})
    ff = _find_func(spec, "Enum", ("windspeed", "fan_speed", "fan", "speed"))
    if ff:
        picked = _pick_enum(ff[1].get("range", []), fan, _FAN_SYN)
        if picked:
            commands.append({"code": ff[0], "value": picked})
    res = _send(conn, d["id"], commands)
    if res.get("success") is True:
        return {"ok": True, "device": d["name"], "temperature": temperature, "mode": mode, "fan": fan}
    return {"error": f"falha ao configurar {d['name']}: {res.get('msg') or res.get('code') or res}"}


def trigger_scene(conn: dict, scene: str) -> dict[str, Any]:
    s = _resolve_scene(conn, scene)
    if not s:
        available = ", ".join(x.get("name", "") for x in (conn.get("scenes") or [])) or "nenhuma"
        return {"error": f"cena '{scene}' não encontrada. Disponíveis: {available}"}
    res = _request(conn, "POST", f"/v2.0/cloud/scene/rule/{s['id']}/actions/trigger")
    if res.get("success") is True:
        return {"ok": True, "scene": s.get("name")}
    return {"error": f"falha ao disparar a cena {s.get('name')}: {res.get('msg') or res.get('code') or res}"}


def test_connection(conn: dict) -> dict[str, Any]:
    """Fura o cache e força um token novo — valida creds/base_url de verdade."""
    _token_cache.pop(conn.get("access_id", ""), None)
    try:
        _get_token(conn)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
