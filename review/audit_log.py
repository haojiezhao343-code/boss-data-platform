"""
审查审计日志

- 持久化：JSONL 追加写入 logs/audit/audit_YYYYMMDD.jsonl（按天分文件）
- 内存：deque 保留最近 500 条供 API 快速查询
- 线程安全：threading.Lock 保护文件写入与内存队列

记录结构：
{
    "ts": "2026-10-10 14:00:00",
    "type": "sql" | "content",
    "action": "execute_sql" | "import_document" | ...,
    "operator": "data_agent_graph" | "kb_import" | "manual",
    "target": "SQL 前 200 字符 / 文件名",
    "approved": true/false,
    "risk_level": "low" | "medium" | "high",
    "reasons": ["拒绝/告警原因", ...],
    "details": {...}
}
"""
import json
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

# 内存中保留的最近记录条数
_MEMORY_KEEP = 500


class AuditLogger:
    """审查审计日志：JSONL 持久化 + 内存队列"""

    def __init__(self, audit_dir: str | None = None):
        # 审计目录：环境变量优先，默认 logs/audit（相对项目根）
        env_dir = os.getenv("REVIEW_AUDIT_DIR")
        base = audit_dir or env_dir or "logs/audit"
        # 相对路径锚定到项目根（本文件位于 review/ 下，parents[1] 即项目根）
        self.audit_dir = Path(base) if os.path.isabs(base) else Path(__file__).parents[1] / base
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._memory: deque = deque(maxlen=_MEMORY_KEEP)

    def record(
        self,
        action_type: str,
        action: str,
        operator: str,
        target: str,
        approved: bool,
        risk_level: str = "low",
        reasons: list[str] | None = None,
        details: dict | None = None,
    ) -> dict:
        """写入一条审计记录（文件 + 内存），返回完整记录"""
        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": action_type,           # sql / content
            "action": action,              # execute_sql / import_document / manual_check ...
            "operator": operator,          # data_agent_graph / kb_import / manual
            "target": (target or "")[:200],
            "approved": bool(approved),
            "risk_level": risk_level,
            "reasons": reasons or [],
            "details": details or {},
        }
        with self._lock:
            self._memory.append(entry)
            try:
                file_path = self.audit_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except OSError:
                # 审计写盘失败不应阻断主流程（内存中仍有记录）
                pass
        return entry

    # ==================== 查询接口 ====================

    def query(
        self,
        limit: int = 50,
        offset: int = 0,
        action_type: str | None = None,
        approved: bool | None = None,
    ) -> list[dict]:
        """从内存查询最近的审计记录（新的在前）"""
        with self._lock:
            items = list(self._memory)
        items.reverse()
        if action_type:
            items = [x for x in items if x["type"] == action_type]
        if approved is not None:
            items = [x for x in items if x["approved"] == approved]
        return items[offset: offset + limit]

    def stats(self) -> dict:
        """统计：总数 / 通过数 / 拒绝数 / 按类型与风险等级分布"""
        with self._lock:
            items = list(self._memory)
        total = len(items)
        approved = sum(1 for x in items if x["approved"])
        by_type: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for x in items:
            by_type[x["type"]] = by_type.get(x["type"], 0) + 1
            by_risk[x["risk_level"]] = by_risk.get(x["risk_level"], 0) + 1
        return {
            "total": total,
            "approved": approved,
            "rejected": total - approved,
            "approval_rate": round(approved / total, 4) if total else 1.0,
            "by_type": by_type,
            "by_risk": by_risk,
        }


# 模块级单例
audit_logger = AuditLogger()
