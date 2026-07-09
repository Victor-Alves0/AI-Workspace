"""Modelos ORM. Importa tudo para que o metadata do Alembic enxergue todas as tabelas."""

from .user import User, UserSecret
from .chat import Folder, Chat, Message, Preset, ChatCompaction
from .tool import Tool
from .model_config import ModelConfig
from .prompt import Prompt
from .skill import Skill
from .app_setting import AppSetting
from .automation import Automation, Notification
from .google_account import GoogleAccount
from .generated_image import GeneratedImage
from .usage_event import UsageEvent
from .whatsapp import WhatsAppConnection, WhatsAppThread

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
    "GoogleAccount",
    "GeneratedImage",
    "UsageEvent",
]
