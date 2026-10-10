"""
多智能体 LLM 客户端工厂

三角色模型配置（.env）：
- orchestrator（主智能体）  : AGENT_ORCH_MODEL，默认 deepseek-ai/DeepSeek-V4-Pro
- data_analyst（问数智能体）: AGENT_DATA_MODEL，默认 deepseek-ai/DeepSeek-V4-Flash
- reviewer（审查智能体）    : AGENT_REVIEW_MODEL，默认 Qwen/Qwen3.8-27B

共用 SiliconFlow 的 OPENAI_API_KEY / OPENAI_API_BASE（缺省回落项目 .env 中已有的 key）。
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# 确保项目根 .env 已加载（幂等）
load_dotenv(Path(__file__).parents[1] / ".env")

# 角色名 -> .env 变量名 -> 默认模型
AGENT_ROLES: dict[str, dict] = {
    "orchestrator": {
        "env_model": "AGENT_ORCH_MODEL",
        "default_model": "deepseek-ai/DeepSeek-V4-Pro",
        "desc": "主智能体（统筹编排/答案生成）",
    },
    "data_analyst": {
        "env_model": "AGENT_DATA_MODEL",
        "default_model": "deepseek-ai/DeepSeek-V4-Flash",
        "desc": "问数智能体（问题分析理解/NL2SQL）",
    },
    "reviewer": {
        "env_model": "AGENT_REVIEW_MODEL",
        "default_model": "Qwen/Qwen3.8-27B",
        "desc": "审查智能体（语义合规审查）",
    },
}


@lru_cache(maxsize=8)
def get_agent_llm(role: str, temperature: float = 0.1) -> ChatOpenAI:
    """
    获取指定角色的 LLM 客户端（带缓存，同角色复用连接）。

    :param role: orchestrator / data_analyst / reviewer
    :param temperature: 采样温度（审查/分析建议 0.1，生成可调高）
    """
    if role not in AGENT_ROLES:
        raise ValueError(f"未知智能体角色: {role}，可选: {list(AGENT_ROLES)}")
    conf = AGENT_ROLES[role]

    model = os.getenv(conf["env_model"], conf["default_model"])
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.siliconflow.cn/v1")

    if not api_key:
        raise RuntimeError(f"缺少 OPENAI_API_KEY（.env），无法初始化{conf['desc']}")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        timeout=60,
        max_retries=1,
    )


def describe_agents() -> dict:
    """返回当前三智能体模型配置（供 API 展示）"""
    result = {}
    for role, conf in AGENT_ROLES.items():
        result[role] = {
            "model": os.getenv(conf["env_model"], conf["default_model"]),
            "desc": conf["desc"],
        }
    return result
