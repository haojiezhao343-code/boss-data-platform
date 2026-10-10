"""
问数智能体（问题分析理解）

模型：deepseek-ai/DeepSeek-V4-Flash（轻量快速，负责问题理解与意图路由）

职责（智库为主、问数为辅的定位下）：
1. 意图分类：knowledge（知识问答，走智库 RAG）/ data（数据查询，走 NL2SQL 问数）/ chitchat（闲聊）
2. 实体与时间要素抽取（商品名、地区、时间范围等）
3. 查询改写（指代消解、口语规范化），供智库检索增强

兜底策略：LLM 超时/失败/输出不合法 → 返回默认判定（intent=knowledge, 原查询透传），不阻断。
"""
import json
import re

from ai_agents.llm_factory import get_agent_llm

ANALYSIS_SYSTEM_PROMPT = """你是智能知识库平台的问题分析智能体。分析用户问题并输出严格 JSON：
{
  "intent": "knowledge" 或 "data" 或 "chitchat",
  "entities": ["关键实体1", "实体2"],
  "time_range": "时间要素，没有则为空字符串",
  "rewritten_query": "改写后的查询（指代消解、口语规范化，保持原意）",
  "reason": "一句话判定理由"
}
判定标准：
- knowledge：问概念/方法/配置/故障处理等知识类问题 → 走知识库检索
- data：问数量/统计/汇总/对比等需要查数据表的问题（如"统计华北地区销售总额"）→ 走 NL2SQL 问数
- chitchat：打招呼、闲聊、与业务无关
只输出 JSON，不要其他文字。"""

ANALYSIS_USER_TEMPLATE = """用户问题：{query}
对话历史（最近 3 轮，供指代消解）：
{history}
请输出分析 JSON："""


class DataAnalysisAgent:
    """问数智能体：问题分析理解与意图路由"""

    def __init__(self):
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_agent_llm("data_analyst", temperature=0.1)
        return self._llm

    def analyze(self, query: str, history: str = "") -> dict:
        """
        分析用户问题。

        :return: {"intent","entities","time_range","rewritten_query","reason","model_ok"}
                 LLM 失败时返回兜底判定（model_ok=False）
        """
        fallback = {
            "intent": "knowledge",
            "entities": [],
            "time_range": "",
            "rewritten_query": query,
            "reason": "分析智能体不可用，已降级为直接检索",
            "model_ok": False,
        }
        if not query or not query.strip():
            return {**fallback, "intent": "chitchat", "rewritten_query": query}

        try:
            prompt = ANALYSIS_USER_TEMPLATE.format(
                query=query.strip(), history=(history or "(无)")[:1500]
            )
            resp = self.llm.invoke(
                [("system", ANALYSIS_SYSTEM_PROMPT), ("user", prompt)]
            )
            parsed = self._parse(resp.content)
            if parsed is None:
                return fallback
            parsed["model_ok"] = True
            # 改写为空时透传原查询
            if not parsed.get("rewritten_query"):
                parsed["rewritten_query"] = query
            return parsed
        except Exception:
            return fallback

    @staticmethod
    def _parse(raw: str) -> dict | None:
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
            intent = data.get("intent")
            if intent not in ("knowledge", "data", "chitchat"):
                return None
            return {
                "intent": intent,
                "entities": [str(e) for e in (data.get("entities") or [])][:10],
                "time_range": str(data.get("time_range", ""))[:100],
                "rewritten_query": str(data.get("rewritten_query", ""))[:500],
                "reason": str(data.get("reason", ""))[:200],
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


# 模块级单例
_data_analysis_agent: DataAnalysisAgent | None = None


def get_data_analysis_agent() -> DataAnalysisAgent:
    global _data_analysis_agent
    if _data_analysis_agent is None:
        _data_analysis_agent = DataAnalysisAgent()
    return _data_analysis_agent
