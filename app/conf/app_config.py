from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv
from omegaconf import OmegaConf

# 加载项目根目录 .env（敏感信息外移：API Key / 数据库密码等）
load_dotenv(Path(__file__).parents[2] / ".env")


# 日志配置
@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


# 数据库配置
@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    host: str
    port: int
    embedding_size: int


@dataclass
class EmbeddingConfig:
    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    model_name: str
    api_key: str
    base_url: str


@dataclass
class AppConfig:
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig

# Path(__file__).parents[1] / 'conf' / 'app_config.yaml'
config_file = Path(__file__).parents[2] / 'conf' / 'app_config.yaml'
context = OmegaConf.load(config_file)
schema = OmegaConf.structured(AppConfig)

# 敏感配置外移：环境变量优先于 yaml（.env 中 DA_LLM_* / MYSQL_*）
if os.getenv("DA_LLM_MODEL"):
    context.llm.model_name = os.getenv("DA_LLM_MODEL")
if os.getenv("DA_LLM_API_KEY"):
    context.llm.api_key = os.getenv("DA_LLM_API_KEY")
if os.getenv("DA_LLM_BASE_URL"):
    context.llm.base_url = os.getenv("DA_LLM_BASE_URL")
if os.getenv("MYSQL_USER"):
    context.db_meta.user = os.getenv("MYSQL_USER")
    context.db_dw.user = os.getenv("MYSQL_USER")
if os.getenv("MYSQL_PASSWORD"):
    context.db_meta.password = os.getenv("MYSQL_PASSWORD")
    context.db_dw.password = os.getenv("MYSQL_PASSWORD")

app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))