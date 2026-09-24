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
from .whatsapp import WhatsAppChat, WhatsAppConnection, WhatsAppMessage, WhatsAppThread
from .telegram import TelegramConnection, TelegramThread
from .discord import DiscordConnection, DiscordThread
from .push import PushSubscription
from .artifact import Artifact, ArtifactVersion
from .memory_bank import MemoryBank
from .knowledge import KnowledgeBase, KnowledgeFolder, KnowledgeDoc, KnowledgeChunk
from .knowledge_enrichment import KnowledgeEnrichment
from .benchmark import Benchmark, BenchmarkRun
from .codespace_project import CodespaceProject
from .codespace_task import CodespaceTask
from .investigation import InvestigationGraph, InvestigationNode, InvestigationEdge
from .health_event import HealthEvent
from .exec_job import ExecJob
from .media_job import MediaJob
from .upload import Upload
from .sound_effect import SoundEffect
from .task_ledger import TaskLedger
from .api_key import ApiKey, ApiRequest
from .trace import ObsTrace, ObsSpan
from .remote_host import RemoteHost
from .imaginai import (
    ImaginaiActionAttempt,
    ImaginaiCampaign,
    ImaginaiEntity,
    ImaginaiEvent,
    ImaginaiFact,
    ImaginaiKnowledge,
    ImaginaiJournalEntry,
)

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
    "KnowledgeEnrichment",
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
    "WhatsAppChat",
    "WhatsAppMessage",
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
    "CodespaceTask",
    "InvestigationGraph",
    "InvestigationNode",
    "InvestigationEdge",
    "HealthEvent",
    "ExecJob",
    "MediaJob",
    "Upload",
    "SoundEffect",
    "TaskLedger",
    "ApiKey",
    "ApiRequest",
    "ObsTrace",
    "ObsSpan",
    "RemoteHost",
    "ImaginaiCampaign",
    "ImaginaiEntity",
    "ImaginaiEvent",
    "ImaginaiFact",
    "ImaginaiKnowledge",
    "ImaginaiActionAttempt",
    "ImaginaiJournalEntry",
]
