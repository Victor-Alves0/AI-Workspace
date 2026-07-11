"""Modelos ORM. Importa tudo para que o metadata do Alembic enxergue todas as tabelas."""

from .user import User, UserSecret
from .chat import Folder, Chat, Message, Preset, ChatCompaction
from .tool import Tool
from .model_config import ModelConfig
from .prompt import Prompt
from .skill import Skill
from .app_setting import AppSetting
from .automation import Automation, Notification
from .automation_run import AutomationRun
from .google_account import GoogleAccount
from .generated_image import GeneratedImage
from .usage_event import UsageEvent
from .whatsapp import WhatsAppConnection, WhatsAppThread
from .telegram import TelegramConnection, TelegramThread
from .push import PushSubscription
from .artifact import Artifact, ArtifactVersion
from .memory_bank import MemoryBank
from .knowledge import KnowledgeBase, KnowledgeDoc, KnowledgeChunk
from .benchmark import Benchmark, BenchmarkRun

__all__ = [
    "User",
    "UserSecret",
    "Folder",
    "Chat",
    "Message",
    "Preset",
    "ChatCompaction",
    "Tool",
    "ModelConfig",
    "Prompt",
    "Skill",
    "AppSetting",
    "Automation",
    "Notification",
    "AutomationRun",
    "GoogleAccount",
    "GeneratedImage",
    "UsageEvent",
    "WhatsAppConnection",
    "WhatsAppThread",
    "TelegramConnection",
    "TelegramThread",
    "PushSubscription",
    "Artifact",
    "ArtifactVersion",
    "MemoryBank",
    "KnowledgeBase",
    "KnowledgeDoc",
    "KnowledgeChunk",
    "Benchmark",
    "BenchmarkRun",
]
