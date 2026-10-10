"""
审查模块 REST API

路由前缀：/api/review
- GET  /api/review/audit-logs           审计日志分页查询
- POST /api/review/sql/check            手动测试 SQL 审查（只审查不执行）
- GET  /api/review/stats                审查统计
- POST /api/review/sensitive-words/reload  热重载敏感词库
- GET  /api/review/manual/pending       人工审核：待审列表
- GET  /api/review/manual/all           人工审核：全部记录
- POST /api/review/manual/approve/{id}  人工审核：通过（触发继续导入）
- POST /api/review/manual/reject/{id}   人工审核：驳回
- POST /api/review/manual/batch-approve 人工审核：批量通过
- GET  /api/review/manual/stats         人工审核：统计
- GET  /api/review/agents               多智能体模型配置展示
- POST /api/review/llm-check            LLM 语义审查手动测试
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from ai_agents.reviewer_agent import get_review_agent
from review.audit_log import audit_logger
from review.content_reviewer import get_content_reviewer
from review.manual_review import get_manual_queue
from review.pipeline import _extract_text
from review.sql_reviewer import get_sql_reviewer
from web.task_runner import run_graph_task
from utils.task_utils import set_task_meta

router = APIRouter(prefix="/api/review", tags=["审查模块"])


# ==================== 审计日志查询 ====================

@router.get("/audit-logs", summary="审计日志分页查询")
async def query_audit_logs(
    limit: int = Query(50, ge=1, le=500, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    type: str | None = Query(None, description="按类型过滤：sql / content"),
    approved: bool | None = Query(None, description="按审查结果过滤"),
):
    items = audit_logger.query(limit=limit, offset=offset, action_type=type, approved=approved)
    return {"code": 200, "total_in_memory": len(audit_logger._memory), "items": items}


@router.get("/stats", summary="审查统计")
async def review_stats():
    return {"code": 200, "stats": audit_logger.stats()}


# ==================== SQL 审查手动测试 ====================

class SQLCheckRequest(BaseModel):
    sql: str = Field(..., description="待审查的 SQL（只审查不执行）")
    operator: str = Field("manual", description="操作者标识")


@router.post("/sql/check", summary="SQL 审查测试（不执行）")
async def check_sql(request: SQLCheckRequest):
    reviewer = get_sql_reviewer()
    try:
        result = reviewer.review(request.sql, operator=request.operator, action="api_check")
        return {"code": 200, **result}
    except Exception as e:
        # SQLReviewError：审查拒绝（预期路径）；其余异常向上抛
        from review.sql_reviewer import SQLReviewError
        if isinstance(e, SQLReviewError):
            return {
                "code": 200,
                "approved": False,
                "reasons": e.reasons,
                "risk_level": "high",
                "warnings": [],
                "cleaned_sql": None,
            }
        raise HTTPException(status_code=500, detail=f"审查器异常：{e}")


# ==================== 敏感词库管理 ====================

@router.post("/sensitive-words/reload", summary="热重载敏感词库")
async def reload_sensitive_words():
    count = get_content_reviewer().reload_sensitive_words()
    return {"code": 200, "message": "敏感词库已重载", "words_count": count}


# ==================== 人工审核（HITL）====================

class ReviewDecision(BaseModel):
    reviewer: str = Field("admin", description="审核人")
    note: str = Field("", description="审核意见")


@router.get("/manual/pending", summary="人工审核：待审列表")
async def pending_list(limit: int = Query(100, ge=1, le=500)):
    return {"code": 200, "items": get_manual_queue().list_items(status="pending", limit=limit)}


@router.get("/manual/all", summary="人工审核：全部记录")
async def all_items(limit: int = Query(200, ge=1, le=1000)):
    return {"code": 200, "items": get_manual_queue().list_items(limit=limit)}


@router.get("/manual/stats", summary="人工审核：统计")
async def manual_stats():
    return {"code": 200, "stats": get_manual_queue().stats()}


@router.post("/manual/approve/{review_id}", summary="人工审核：通过（通过后自动继续导入）")
async def approve(review_id: str, body: ReviewDecision, background_tasks: BackgroundTasks):
    item = get_manual_queue().decide(review_id, approve=True, reviewer=body.reviewer, note=body.note)
    if item is None:
        raise HTTPException(status_code=404, detail="审核单不存在或已处理")
    # 通过后自动调度继续导入（HITL 闭环：人工放行 → 进入智库解析）
    # 复用上传时的原 task_id，让「文档导入」页面的进度条/日志无缝恢复更新；
    # 旧审核单（无 task_id，如服务重启前遗留）则降级为新任务在后台执行。
    task_id = item.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
    set_task_meta(task_id, item.get("filename", ""))
    file_dir = str(Path(item["file_path"]).parent)
    background_tasks.add_task(run_graph_task, task_id, file_dir, item["file_path"])
    return {"code": 200, "message": "已通过并继续导入", "item": item, "task_id": task_id,
            "resumed": bool(item.get("task_id"))}


@router.post("/manual/reject/{review_id}", summary="人工审核：驳回")
async def reject(review_id: str, body: ReviewDecision):
    item = get_manual_queue().decide(review_id, approve=False, reviewer=body.reviewer, note=body.note)
    if item is None:
        raise HTTPException(status_code=404, detail="审核单不存在或已处理")
    return {"code": 200, "message": "已驳回", "item": item}


@router.post("/manual/batch-approve", summary="人工审核：批量通过全部待审")
async def batch_approve(body: ReviewDecision, background_tasks: BackgroundTasks):
    queue = get_manual_queue()
    approved, failed, resumed_tasks = 0, 0, []
    for item in queue.list_items(status="pending"):
        if queue.decide(item["review_id"], approve=True, reviewer=body.reviewer, note=body.note or "批量通过"):
            approved += 1
            # 与单条 approve 一致：通过后自动继续导入（复用原 task_id 恢复导入页进度）
            task_id = item.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
            set_task_meta(task_id, item.get("filename", ""))
            file_dir = str(Path(item["file_path"]).parent)
            background_tasks.add_task(run_graph_task, task_id, file_dir, item["file_path"])
            resumed_tasks.append({"review_id": item["review_id"], "task_id": task_id})
        else:
            failed += 1
    return {"code": 200, "approved": approved, "failed": failed, "resumed_tasks": resumed_tasks}


# ==================== 多智能体 ====================

@router.get("/agents", summary="多智能体模型配置")
async def agent_models():
    from ai_agents.llm_factory import describe_agents
    return {"code": 200, "agents": describe_agents()}


class LLMCheckRequest(BaseModel):
    title: str = Field("", description="文档标题")
    content: str = Field(..., description="待审查文本")


@router.post("/llm-check", summary="LLM 语义审查手动测试")
async def llm_check(body: LLMCheckRequest):
    result = get_review_agent().review_text(title=body.title, content=body.content, operator="manual")
    if result is None:
        return {"code": 200, "available": False, "message": "LLM 审查不可用（超时/异常），已降级"}
    return {"code": 200, "available": True, "result": result}
