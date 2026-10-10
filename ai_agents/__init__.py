"""
BOSS数智平台 —— 多智能体协作层

三模型分工（参考企业级多智能体文档审核架构：Controller + 专业 Agent + HITL）：
- 主智能体（deepseek-ai/DeepSeek-V4-Pro）：统筹问答编排、最终答案生成与兜底话术
- 问数智能体（deepseek-ai/DeepSeek-V4-Flash）：问题分析理解、意图路由、NL2SQL
- 审查智能体（Qwen/Qwen3.8-27B）：文档语义合规审查

所有 LLM 客户端统一从 .env 加载，带超时与失败兜底（降级不阻断主流程）。
"""
from ai_agents.llm_factory import get_agent_llm, AGENT_ROLES, describe_agents
from ai_agents.reviewer_agent import LLMReviewAgent, get_review_agent
from ai_agents.data_agent_proxy import DataAnalysisAgent, get_data_analysis_agent
from ai_agents.orchestrator import Orchestrator, get_orchestrator

__all__ = [
    "get_agent_llm",
    "AGENT_ROLES",
    "describe_agents",
    "LLMReviewAgent",
    "get_review_agent",
    "DataAnalysisAgent",
    "get_data_analysis_agent",
    "Orchestrator",
    "get_orchestrator",
]
