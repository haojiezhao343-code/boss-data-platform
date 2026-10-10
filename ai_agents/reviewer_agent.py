"""
审查智能体（LLM 语义合规审查）

模型：Qwen/Qwen3.8-27B（审查专用，规则审查之外的语义层）

职责：对规则审查通过的文档做语义级合规判断：
- 敏感信息泄露（身份证号/手机号/银行卡/内部机密等）
- 违规内容（政治敏感/暴力/色情/违法）
- 文档质量（乱码/无意义内容/与标题不符）

输出结构化 JSON 判定 + 置信度，由上层与规则审查融合定级。
兜底策略：LLM 超时/失败/输出不合法 → 返回 None（降级为纯规则审查结果，不阻断）。
"""
import json
import re

from ai_agents.llm_factory import get_agent_llm

# 语义审查提示词（要求严格 JSON 输出）
REVIEW_SYSTEM_PROMPT = """你是一名企业知识库的内容合规审查专家。请审查以下文档内容是否存在合规风险，从三个维度判断：
1. sensitive：敏感信息泄露（身份证号、手机号、银行卡号、内部机密数据、未公开财务信息等）
2. illegal：违规违法内容（政治敏感、暴力、色情、赌博、诈骗等）
3. quality：质量问题（大面积乱码、无意义字符堆砌、内容与文档主题严重不符）

严格按以下 JSON 格式输出，不要输出任何其他文字：
{
  "verdict": "pass" 或 "suspect" 或 "reject",
  "confidence": 0.0到1.0之间的小数,
  "risk_level": "low" 或 "medium" 或 "high",
  "findings": ["发现的问题描述1", "问题描述2"],
  "summary": "一句话审查结论"
}
判定标准：
- 无任何问题 → verdict=pass, risk_level=low
- 存在可疑但不确定的内容 → verdict=suspect, risk_level=medium
- 明确违规违法或明确泄露敏感信息 → verdict=reject, risk_level=high"""

REVIEW_USER_TEMPLATE = """文档标题：{title}
文档内容（前 {max_chars} 字符）：
---
{content}
---
请输出审查 JSON："""


class LLMReviewAgent:
    """审查智能体：LLM 语义合规审查"""

    def __init__(self, max_chars: int = 6000):
        self.max_chars = max_chars
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_agent_llm("reviewer", temperature=0.1)
        return self._llm

    def review_text(self, title: str, content: str, operator: str = "reviewer_agent") -> dict | None:
        """
        语义审查一段文本。

        :return: {"verdict","confidence","risk_level","findings","summary","model"}
                 LLM 失败时返回 None（调用方降级为纯规则结果）
        """
        if not content or not content.strip():
            return None
        try:
            prompt = REVIEW_USER_TEMPLATE.format(
                title=title or "(无标题)",
                max_chars=min(len(content), self.max_chars),
                content=content[: self.max_chars],
            )
            resp = self.llm.invoke(
                [("system", REVIEW_SYSTEM_PROMPT), ("user", prompt)]
            )
            return self._parse(resp.content, operator)
        except Exception:
            # 兜底：LLM 审查失败不阻断，返回 None 由上层降级
            return None

    def _parse(self, raw: str, operator: str) -> dict | None:
        """解析 LLM 输出 JSON（容忍 ```json 包裹与前后杂文本），失败返回 None"""
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
            verdict = data.get("verdict")
            if verdict not in ("pass", "suspect", "reject"):
                return None
            confidence = float(data.get("confidence", 0.5))
            return {
                "verdict": verdict,
                "confidence": max(0.0, min(1.0, confidence)),
                "risk_level": data.get("risk_level", "medium"),
                "findings": list(data.get("findings", []))[:10],
                "summary": str(data.get("summary", ""))[:200],
                "model": "reviewer_agent",
                "operator": operator,
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


# 模块级单例
_review_agent: LLMReviewAgent | None = None


def get_review_agent() -> LLMReviewAgent:
    global _review_agent
    if _review_agent is None:
        _review_agent = LLMReviewAgent()
    return _review_agent
