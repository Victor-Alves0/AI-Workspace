"""Modelos ORM. Importa tudo para que o metadata do Alembic enxergue todas as tabelas."""

from .user import AuditEvent, User, UserSecret
from .chat import Folder, Chat, Message, Preset, ChatCompaction
from .tool import Tool
from .model_config import ModelConfig
from .prompt import Prompt
from .skill import Skill
from .skill_proposal import SkillProposal
from .app_setting import AppSetting
from .automation import Automation, Notification
from .automation_run import AutomationRun
from .google_account import GoogleAccount
from .github_account import GithubAccount
from .notion_account import NotionAccount
from .slack_account import SlackAccount
from .slack_channel import SlackChannelConnection, SlackChannelThread
from .generated_image import GeneratedImage
from .usage_event import UsageEvent
from .whatsapp import WhatsAppConnection, WhatsAppThread
from .telegram import TelegramConnection, TelegramThread
from .discord import DiscordConnection, DiscordThread
from .push import PushSubscription
from .artifact import Artifact, ArtifactVersion
from .memory_bank import MemoryBank
from .knowledge import KnowledgeBase, KnowledgeFolder, KnowledgeDoc, KnowledgeChunk
from .benchmark import Benchmark, BenchmarkRun
from .codespace_project import CodespaceProject
from .api_key import ApiKey, ApiRequest
from .trace import ObsTrace, ObsSpan

__all__ = [
    "AuditEvent",
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
    "SkillProposal",
    "AppSetting",
    "Automation",
    "Notification",
    "AutomationRun",
    "GoogleAccount",
    "GithubAccount",
    "NotionAccount",
    "SlackAccount",
    "SlackChannelConnection",
    "SlackChannelThread",
    "GeneratedImage",
    "UsageEvent",
    "WhatsAppConnection",
    "WhatsAppThread",
    "TelegramConnection",
    "TelegramThread",
    "DiscordConnection",
    "DiscordThread",
    "PushSubscription",
    "Artifact",
    "ArtifactVersion",
    "MemoryBank",
    "KnowledgeBase",
    "KnowledgeFolder",
    "KnowledgeDoc",
    "KnowledgeChunk",
    "Benchmark",
    "BenchmarkRun",
    "CodespaceProject",
    "ApiKey",
    "ApiRequest",
    "ObsTrace",
    "ObsSpan",
]
