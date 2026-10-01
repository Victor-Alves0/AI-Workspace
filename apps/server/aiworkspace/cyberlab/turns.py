"""Ponte entre o loop de chat e o caso CyberLab.

O modelo recebe UMA ferramenta autoritativa (`cyberlab`): ele conduz a conversa em
linguagem natural, mas o escopo, a fase, o log de passos e os achados só mudam por
aqui — como no World Kernel do Imaginai."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..chat.orchestrator import NativeToolOpts
from ..db import SessionLocal
from ..models import CyberLabCase
from . import service
from .protocol import protocol_for

_CYBERLAB_TOOL = {
    "type": "function",
    "function": {
        "name": "cyberlab",
        "description": (
            "Authoritative state of this security-assessment case. Use `scope` to save "
            "the target, the operator's authorization note and the objective (do this "
            "first); `phase` to advance scoping→recon→enum→analysis→reporting; `step` to "
            "record each loop step (the command you told the operator, their pasted "
            "output, your short interpretation); `finding` to record something that "
            "matters; `state` to re-read the case."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["scope", "phase", "step", "finding", "state"],
                },
                "target": {"type": "string", "description": "scope: what is being assessed (host/app/scope)."},
                "authorization": {
                    "type": "string",
                    "description": "scope: the operator's note that this target is theirs or authorized.",
                },
                "objective": {"type": "string", "description": "scope: the goal of the assessment."},
                "phase": {
                    "type": "string",
                    "enum": service.ALL_PHASES,
                    "description": "phase: the phase to move to (valid set depends on the case mode).",
                },
                "command": {"type": "string", "description": "step: the command run (by the operator in blackbox, or by you in the sandbox in autofuzz)."},
                "output": {"type": "string", "description": "step: the resulting output (trim huge dumps)."},
                "note": {"type": "string", "description": "step: your short interpretation of the output."},
                "title": {"type": "string", "description": "finding: short title."},
                "severity": {
                    "type": "string",
                    "enum": ["info", "low", "medium", "high", "critical"],
                    "description": "finding: severity.",
                },
                "detail": {"type": "string", "description": "finding: what it is, the evidence, and the impact."},
                "crash": {
                    "type": "string",
                    "description": "finding (autofuzz): sanitizer crash class, e.g. heap-buffer-overflow READ, use-after-free, stack-overflow.",
                },
                "repro": {
                    "type": "string",
                    "description": "finding (autofuzz): how to reproduce — the minimized crashing input (path/base64) and the exact command.",
                },
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "finding: references (CVE ids, URLs).",
                },
            },
            "required": ["action"],
        },
    },
}


def _public_state(case: CyberLabCase) -> dict[str, Any]:
    s = case.settings or {}
    log = s.get("log") or []
    return {
        "mode": case.mode,
        "phase": case.phase,
        "target": case.target,
        "authorization": case.authorization,
        "objective": case.objective,
        "steps": len(log),
        "recent_steps": log[-6:],
        "findings": s.get("findings") or [],
    }


async def apply_action(
    db: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID, args: dict[str, Any]
) -> dict[str, Any]:
    """Aplica uma ação do tool `cyberlab` ao caso do chat. Recebe `db` (como os
    serviços do Imaginai) para ser testável com a sessão do teste."""
    action = str(args.get("action") or "").strip()
    case = await service.get_case(db, user_id, chat_id)
    if case is None:
        return {"error": "No active case in this chat."}
    s = dict(case.settings or {})
    s.setdefault("log", [])
    s.setdefault("findings", [])

    if action == "state":
        return {"kind": "cyberlab_state", **_public_state(case)}

    if action == "scope":
        if "target" in args:
            case.target = str(args.get("target") or "")
        if "authorization" in args:
            case.authorization = str(args.get("authorization") or "")
        if "objective" in args:
            case.objective = str(args.get("objective") or "")
        phases = service.phases_for(case.mode)
        if case.phase == "scoping" and len(phases) > 1:
            case.phase = phases[1]  # escopo salvo → próxima fase do modo (recon/harness)

    elif action == "phase":
        phase = str(args.get("phase") or "").strip()
        phases = service.phases_for(case.mode)
        if phase not in phases:
            return {"error": f"Unknown phase for mode {case.mode}. Use one of: {', '.join(phases)}"}
        case.phase = phase

    elif action == "step":
        s["log"] = list(s["log"]) + [{
            "seq": len(s["log"]) + 1,
            "phase": case.phase,
            "command": str(args.get("command") or ""),
            "output": str(args.get("output") or "")[:8000],
            "note": str(args.get("note") or ""),
        }]
        case.settings = s

    elif action == "finding":
        title = str(args.get("title") or "").strip()
        if not title:
            return {"error": "A finding needs a title."}
        refs = args.get("refs")
        finding = {
            "seq": len(s["findings"]) + 1,
            "title": title,
            "severity": str(args.get("severity") or "info"),
            "detail": str(args.get("detail") or ""),
            "refs": [str(r) for r in refs] if isinstance(refs, list) else [],
        }
        if args.get("crash"):
            finding["crash"] = str(args.get("crash"))
        if args.get("repro"):
            finding["repro"] = str(args.get("repro"))
        s["findings"] = list(s["findings"]) + [finding]
        case.settings = s

    else:
        return {"error": "Invalid cyberlab action"}

    await db.commit()
    await db.refresh(case)
    return {"kind": f"cyberlab_{action}", **_public_state(case)}


@dataclass(slots=True)
class CyberLabBridge:
    user_id: uuid.UUID
    chat_id: uuid.UUID

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "cyberlab":
            return {"error": "Unknown tool"}
        async with SessionLocal() as db:
            return await apply_action(db, self.user_id, self.chat_id, args)


async def prepare_turn_tools(
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
) -> NativeToolOpts | None:
    """Monta o protocolo do modo + o estado atual do caso + a tool autoritativa."""
    async with SessionLocal() as db:
        case = await service.get_case(db, user_id, chat_id)
        if case is None:
            return None
        state_json = json.dumps(_public_state(case), ensure_ascii=False, default=str)
        mode = case.mode
    bridge = CyberLabBridge(user_id=user_id, chat_id=chat_id)
    protocol = protocol_for(mode)
    return NativeToolOpts(
        specs=[_CYBERLAB_TOOL],
        prompt=f"{protocol}\n\n## Case state at the start of this turn\n```json\n{state_json}\n```",
        run=bridge.run,
    )
