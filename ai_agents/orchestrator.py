"""
主智能体（Orchestrator）

模型：deepseek-ai/DeepSeek-V4-Pro（统筹编排/答案质量兜底）

职责：
1. 统一问答编排：FAQ 缓存 → 问数智能体分析 → 意图路由（data→NL2SQL / knowledge→智库RAG）
2. 答案质量兜底：答案为空/异常时生成兜底话术（LLM 不可用时用固定模板）
3. FAQ 缓存管理：问题归一化 MD5 → 答案缓存（JSONL 持久化，可预灌 FAQ 语料）
"""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from ai_agents.llm_factory import get_agent_llm

# 固定兜底话术模板（主智能体不可用时的最终防线）
FALLBACK_TEMPLATES = {
    "knowledge": "抱歉，知识库服务暂时不可用，未能检索到与「{q}」相关的内容。请稍后重试，或联系管理员检查知识库服务状态。",
    "data": "抱歉，数据查询服务暂时不可用，无法完成「{q}」的统计分析。请稍后重试，或联系管理员检查数据仓库连接。",
    "chitchat": "您好！我是河南鼎誉智数审查项目助手，可以帮您查询设备手册等知识库内容，也可以做数据统计分析。请问有什么可以帮您？",
    "empty": "抱歉，本次未能生成有效回答。请换个问法再试一次，例如：\"H3C ER2100 路由器如何配置 NAT？\"",
}


# 中英文标点归一化正则（弯引号用 \u 转义书写，避免源码字符被工具链静默转换破坏字符串）
_PUNCT_RE = re.compile("[\\s，。！？、；：《》~～!?,.;:'\"()\\[\\]{}<>\\-_/\\\\\u201c\u201d\u2018\u2019\u300c\u300d]+")


def normalize_question(q: str) -> str:
    """问题归一化：去空白/标点/大小写，用于 FAQ 缓存命中"""
    s = (q or "").strip().lower()
    s = _PUNCT_RE.sub("", s)
    return s


class Orchestrator:
    """主智能体：统一问答编排 + FAQ 缓存 + 答案兜底"""

    def __init__(self, cache_path: str | None = None):
        # FAQ 缓存持久化文件
        base = cache_path or "logs/faq_cache.json"
        self.cache_path = Path(base) if Path(base).is_absolute() else Path(__file__).parents[1] / base
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_cache()
        self._llm = None

    # ==================== LLM ====================

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_agent_llm("orchestrator", temperature=0.3)
        return self._llm

    # ==================== FAQ 缓存 ====================

    def _load_cache(self):
        try:
            if self.cache_path.exists():
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def _save_cache(self):
        try:
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass

    def cache_get(self, question: str) -> dict | None:
        key = hashlib.md5(normalize_question(question).encode()).hexdigest()
        norm = normalize_question(question)
        with self._lock:
            # 一级：精确命中（归一化 MD5）
            item = self._cache.get(key)
            if item:
                item["hit_count"] = item.get("hit_count", 0) + 1
                return dict(item)
            # 二级：模糊命中（归一化后互相包含，处理"请问X"/"X"类前缀差异）
            if norm and len(norm) >= 6:
                for k, v in self._cache.items():
                    vq = normalize_question(v.get("question", ""))
                    if vq and (norm in vq or vq in norm):
                        v["hit_count"] = v.get("hit_count", 0) + 1
                        result = dict(v)
                        result["fuzzy"] = True
                        return result
        return None

    def cache_put(self, question: str, answer: str, source: str = "qa"):
        key = hashlib.md5(normalize_question(question).encode()).hexdigest()
        with self._lock:
            self._cache[key] = {
                "question": question,
                "answer": answer,
                "source": source,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hit_count": 0,
            }
            # 缓存上限 1000 条，超出淘汰最早的一半
            if len(self._cache) > 1000:
                for k in list(self._cache.keys())[:500]:
                    self._cache.pop(k, None)
            self._save_cache()

    def cache_stats(self) -> dict:
        with self._lock:
            return {"size": len(self._cache), "path": str(self.cache_path)}

    def preload_faq(self, faq_items: list[dict]) -> int:
        """预灌 FAQ 语料（[{"question","answer"}]），返回灌入条数"""
        n = 0
        for item in faq_items:
            q, a = item.get("question"), item.get("answer")
            if q and a:
                self.cache_put(q, a, source="faq_preload")
                n += 1
        return n

    # ==================== 兜底话术 ====================

    def fallback_answer(self, question: str, intent: str = "knowledge", reason: str = "") -> str:
        """兜底话术：优先 LLM 生成，失败降级固定模板"""
        try:
            resp = self.llm.invoke(
                [(
                    "user",
                    f"用户提问：「{question}」，但系统出现异常（{reason or '服务不可用'}）。"
                    f"请生成一句简短、友好的中文兜底回复，说明暂时无法回答并给出建议。不超过 80 字。"
                )]
            )
            text = (resp.content or "").strip()
            if text:
                return text[:200]
        except Exception:
            pass
        return FALLBACK_TEMPLATES.get(intent, FALLBACK_TEMPLATES["empty"]).format(q=question[:50])

    def check_answer_quality(self, question: str, answer: str) -> dict:
        """
        主智能体答案质量检查（非流式模式用）：
        答案为空/过短 → 不可用；正常 → 可用。
        :return: {"ok": bool, "reason": str}
        """
        if not answer or len(answer.strip()) < 5:
            return {"ok": False, "reason": "答案为空或过短"}
        if re.fullmatch(r"[\s\S]{0,30}(失败|异常|错误|error)[\s\S]{0,30}", answer.strip(), re.IGNORECASE) and len(answer.strip()) < 50:
            return {"ok": False, "reason": "答案疑似错误信息"}
        return {"ok": True, "reason": ""}


# 模块级单例
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
