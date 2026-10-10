"""
人工审核队列（Human-in-the-Loop）

参考企业级文档审核架构：AI 审查 → 风险分级 → 中风险进入人工审核 → 人工决定写回审计闭环。

数据模型：
{
  "review_id": "rv-xxxx",
  "ts": "提交时间",
  "filename": "文档名",
  "file_path": "本地保存路径（通过后由此继续导入）",
  "file_md5": "内容指纹",
  "risk_level": "medium|high",
  "rule_findings": [...],       # 规则审查发现
  "llm_findings": [...],        # LLM 审查发现
  "llm_summary": "...",
  "status": "pending|approved|rejected",
  "reviewer": "审核人",
  "review_note": "审核意见",
  "review_ts": "审核时间"
}

持久化：logs/manual_review.jsonl（追加事件）；内存维护最新状态。
"""
import json
import threading
import time
import uuid
from pathlib import Path

from review.audit_log import audit_logger


class ManualReviewQueue:
    """人工审核队列：JSONL 持久化 + 内存状态 + 审计联动"""

    def __init__(self, store_path: str | None = None):
        base = store_path or "logs/manual_review.jsonl"
        self.store_path = Path(base) if Path(base).is_absolute() else Path(__file__).parents[1] / base
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._load()

    def _load(self):
        """启动时从 JSONL 回放最新状态"""
        try:
            if self.store_path.exists():
                for line in self.store_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        rid = item.get("review_id")
                        if rid:
                            # 后写的状态覆盖先写的（事件回放）
                            self._items[rid] = item
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    def _append(self, item: dict):
        try:
            with open(self.store_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    # ==================== 队列操作 ====================

    def submit(
        self,
        filename: str,
        file_path: str,
        file_md5: str = "",
        risk_level: str = "medium",
        rule_findings: list | None = None,
        llm_findings: list | None = None,
        llm_summary: str = "",
        task_id: str = "",
    ) -> dict:
        """提交一条待人工审核记录（task_id 用于审核通过后恢复原导入任务进度）"""
        rid = f"rv-{uuid.uuid4().hex[:10]}"
        item = {
            "review_id": rid,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "filename": filename,
            "file_path": file_path,
            "file_md5": file_md5,
            "risk_level": risk_level,
            "rule_findings": rule_findings or [],
            "llm_findings": llm_findings or [],
            "llm_summary": llm_summary,
            "task_id": task_id,
            "status": "pending",
            "reviewer": "",
            "review_note": "",
            "review_ts": "",
        }
        with self._lock:
            self._items[rid] = item
        self._append(item)
        audit_logger.record(
            action_type="content",
            action="manual_review_submit",
            operator="pipeline",
            target=filename,
            approved=False,
            risk_level=risk_level,
            reasons=["进入人工审核队列"],
            details={"review_id": rid},
        )
        return item

    def list_items(self, status: str | None = None, limit: int = 100) -> list[dict]:
        """查询审核记录（新的在前）"""
        with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda x: x.get("ts", ""), reverse=True)
        if status:
            items = [x for x in items if x.get("status") == status]
        return items[:limit]

    def get(self, review_id: str) -> dict | None:
        with self._lock:
            return self._items.get(review_id)

    def decide(self, review_id: str, approve: bool, reviewer: str, note: str = "") -> dict | None:
        """
        人工审核决定（通过/驳回）。
        通过后返回完整 item（含 file_path，供调度继续导入）；不存在返回 None。
        """
        with self._lock:
            item = self._items.get(review_id)
            if item is None or item.get("status") != "pending":
                return None
            item["status"] = "approved" if approve else "rejected"
            item["reviewer"] = reviewer or "anonymous"
            item["review_note"] = note
            item["review_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            snapshot = dict(item)
        self._append(snapshot)
        audit_logger.record(
            action_type="content",
            action="manual_review_decide",
            operator=snapshot["reviewer"],
            target=snapshot["filename"],
            approved=approve,
            risk_level=snapshot.get("risk_level", "medium"),
            reasons=[note or ("人工通过" if approve else "人工驳回")],
            details={"review_id": review_id},
        )
        return snapshot

    def stats(self) -> dict:
        with self._lock:
            items = list(self._items.values())
        by_status = {"pending": 0, "approved": 0, "rejected": 0}
        for x in items:
            s = x.get("status")
            if s in by_status:
                by_status[s] += 1
        return {"total": len(items), **by_status}


# 模块级单例
_manual_queue: ManualReviewQueue | None = None


def get_manual_queue() -> ManualReviewQueue:
    global _manual_queue
    if _manual_queue is None:
        _manual_queue = ManualReviewQueue()
    return _manual_queue
