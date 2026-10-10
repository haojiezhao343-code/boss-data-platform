# main.py — 河南鼎誉智数审查项目 统一入口
# = data_agent（BOSS问数 NL2SQL）+ knowledge_base（BOSS智库知识库）+ review（审查模块）
# 端口 7954（沿用原 BOSS智库端口）
#
# 路由总览：
#   智库：/upload /status/{task_id} /query /stream/{sid} /history/{sid} /chat.html /import.html
#   问数：POST /api/data/query
#   审查：/api/review/*
#   健康：/health

import asyncio
import os
import threading

# 本地基础设施（Qdrant/ES/TEI/MySQL）必须直连，不能走系统代理：
# 若环境存在 HTTP_PROXY/HTTPS_PROXY（如沙箱/梯子），localhost 请求会被代理劫持返回 502。
_no_proxy = os.environ.get("NO_PROXY", "")
_hosts = "localhost,127.0.0.1,::1"
for _k in ("NO_PROXY", "no_proxy"):
    os.environ[_k] = (_hosts + "," + _no_proxy) if _no_proxy else _hosts

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, StreamingResponse

# ---- BOSS智库（knowledge_base）----
from config.minio_config import minio_config
from processor.import_processor.main_graph import KBImportworkflow
from processor.query_processor.main_graph import KBQueryWorkflow
from tool.logger import logger
from utils.minio_utils import get_minio_client
from utils.task_utils import (
    update_task_status, add_done_task, add_running_task,
    get_task_status, get_done_task_list, get_running_task_list,
    TASK_STATUS_PROCESSING, TASK_STATUS_COMPLETED, TASK_STATUS_FAILED,
    get_task_result, set_task_meta, get_recent_tasks
)
from utils.mongo_history_utils import clear_history, get_recent_messages
from utils.sse_utils import create_sse_queue, SSEEvent, push_to_session, sse_generator

# ---- 审查模块 ----
from review import SQLReviewError
from review.api import router as review_router
from review.pipeline import review_document_pipeline
from review.manual_review import get_manual_queue

# ---- 多智能体 ----
from ai_agents import get_data_analysis_agent, get_orchestrator

# ---- 导入调度（共享）----
from web.task_runner import run_graph_task

# ==================== 创建应用 ====================
app = FastAPI(
    title="河南鼎誉智数审查项目",
    description="BOSS问数（NL2SQL 智能问数）+ BOSS智库（知识库导入与问答）+ 审查模块（SQL审查/内容审查）"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载审查模块路由
app.include_router(review_router)


# ==================== 静态页面路由 ====================

@app.get("/chat.html")
async def chat():
    current_dir_parent_path = Path(__file__).absolute().parent
    html_path = current_dir_parent_path / "web" / "page" / "chat.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"没有查询到页面：{html_path}")
    return FileResponse(html_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/import.html")
async def get_import_page():
    current_dir_parent_path = Path(__file__).absolute().parent
    html_path = current_dir_parent_path / "web" / "page" / "import.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"没有查询到页面：{html_path}")
    return FileResponse(html_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/review.html")
async def review_page():
    current_dir_parent_path = Path(__file__).absolute().parent
    html_path = current_dir_parent_path / "web" / "page" / "review.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"没有查询到页面：{html_path}")
    return FileResponse(html_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/")
async def root():
    """统一控制台单页应用（合并问答/导入/审核三页面）"""
    html_path = Path(__file__).absolute().parent / "web" / "page" / "index.html"
    if html_path.exists():
        return FileResponse(html_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return {
        "message": "河南鼎誉智数审查项目 API",
        "pages": {"/": "统一控制台", "/chat.html": "知识库问答", "/import.html": "文件导入", "/review.html": "审核管理"},
        "apis": {
            "智库问答": "/query", "智库导入": "/upload",
            "智能问数": "POST /api/data/query",
            "审查模块": "/api/review/audit-logs",
        },
    }


# ==================== 智库：导入相关 API ====================


@app.post("/upload", summary="文件上传接口（三道防线：规则审查→LLM语义审查→人工审核）")
async def upload_files(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    """
    完整审查管线（review/pipeline.py）：
      规则硬拦截 → LLM 语义审查(Qwen3.8-27B) → 风险分级流转：
      - 低风险（LLM 未发现违规）→ 直接进入智库导入（跳过人工审查）
      - 可疑但未定性（中风险）→ 放行入库，风险留痕（2026-10-10 策略：仅高风险拦截）
      - LLM 不可用（超时/异常）→ 人工审核队列兜底（通过后自动继续导入）
      - 高风险（敏感词/违规判定）→ 直接拒绝
    """
    data_based_root_dir = os.getenv("DATA_BASED_ROOT_DIR")
    data_dir = os.path.join(data_based_root_dir, datetime.now().strftime("%Y%m%d"))
    results = []

    for file in files:
        task_id = str(uuid.uuid4())
        set_task_meta(task_id, file.filename)
        logger.info(f"[{task_id}] 开始处理上传文件，文件名：{file.filename}")
        add_running_task(task_id, "upload_file")

        file_dir = os.path.join(data_dir, task_id)
        os.makedirs(file_dir, exist_ok=True)
        import_file_path = os.path.join(file_dir, file.filename)

        with open(import_file_path, "wb") as file_buffer:
            shutil.copyfileobj(file.file, file_buffer)
        logger.info(f"[{task_id}] 文件已保存至本地，路径：{import_file_path}")

        # ==================== 三道防线审查管线 ====================
        add_done_task(task_id, "content_review")
        try:
            pipeline_result = review_document_pipeline(import_file_path, filename=file.filename, task_id=task_id)
        except Exception as e:
            # 管线自身异常的最终兜底：不阻断服务，标记失败
            logger.error(f"[{task_id}] 审查管线异常：{e}", exc_info=True)
            update_task_status(task_id, "failed")
            results.append({"task_id": task_id, "filename": file.filename, "decision": "rejected", "reasons": [f"审查管线异常：{e}"]})
            continue

        decision = pipeline_result["decision"]

        if decision == "rejected":
            # 高风险：直接拒绝
            logger.warning(f"[{task_id}] 审查拒绝：{pipeline_result['reasons']}")
            add_done_task(task_id, "content_review_rejected")
            update_task_status(task_id, "failed")
            results.append({
                "task_id": task_id, "filename": file.filename,
                "decision": "rejected", "risk_level": pipeline_result["risk_level"],
                "reasons": pipeline_result["reasons"],
            })
            continue

        if decision == "pending":
            # 中风险：进入人工审核队列（通过后由审核 API 自动调度导入）
            logger.info(f"[{task_id}] 进入人工审核队列，审核单：{pipeline_result['review_id']}")
            add_done_task(task_id, "manual_review_pending")
            update_task_status(task_id, "pending_review")
            results.append({
                "task_id": task_id, "filename": file.filename,
                "decision": "pending", "risk_level": pipeline_result["risk_level"],
                "review_id": pipeline_result["review_id"],
                "reasons": pipeline_result["reasons"],
            })
            continue

        # 低风险：自动通过 → 上传 MinIO 备份 → 进入导入工作流
        logger.info(f"[{task_id}] 审查自动通过（规则+LLM），进入导入流程")
        minio_object_name = f"pdf_files/{datetime.now().strftime('%Y%m%d')}/{file.filename}"
        try:
            minio_client = get_minio_client()
            minio_bucket_name = minio_config.bucket_name
            minio_client.fput_object(
                bucket_name=minio_bucket_name,
                object_name=minio_object_name,
                file_path=import_file_path,
                content_type=file.content_type
            )
            logger.info(f"[{task_id}] 文件已成功上传至MinIO，桶名：{minio_bucket_name}，对象名：{minio_object_name}")
        except Exception as e:
            logger.warning(f"[{task_id}] 文件上传MinIO失败，将继续执行本地处理流程，异常信息：{str(e)}", exc_info=True)

        add_done_task(task_id, "upload_file")
        background_tasks.add_task(run_graph_task, task_id, file_dir, import_file_path)
        logger.info(f"[{task_id}] 已将LangGraph全流程加入后台任务")
        results.append({
            "task_id": task_id, "filename": file.filename,
            "decision": "auto_approved", "risk_level": "low", "reasons": [],
        })

    return {"code": 200, "message": f"文件上传成功, total: {len(files)}", "results": results}


@app.get("/tasks/recent", summary="最近导入任务列表（供导入页刷新按钮）")
async def recent_tasks(limit: int = 50):
    """返回内存中最近的导入任务及其实时状态（新任务在前）"""
    return {"code": 200, "tasks": get_recent_tasks(limit=limit)}


@app.get("/status/{task_id}", summary="任务状态查询")
async def get_task_progress(task_id: str):
    task_status_info: Dict[str, Any] = {
        "code": 200,
        "task_id": task_id,
        "status": get_task_status(task_id),
        "done_list": get_done_task_list(task_id),
        "running_list": get_running_task_list(task_id)
    }
    logger.info(f"[{task_id}] 任务状态查询，当前状态：{task_status_info['status']}")
    return task_status_info


# ==================== 智库：问答相关 API ====================

class QueryRequest(BaseModel):
    query: str = Field(..., description="查询内容")
    session_id: str = Field(None, description="会话ID")
    is_stream: bool = Field(False, description="是否流式返回")


def run_query_graph(session_id: str, user_query: str, is_stream: bool = True):
    print(f"开始流程图处理...{session_id} {user_query} {is_stream}")
    init_state = {
        "original_query": user_query,
        "session_id": session_id,
        "is_stream": is_stream
    }
    try:
        workflow = KBQueryWorkflow()
        for chunk in workflow.run(init_state, stream=is_stream):
            logger.debug(chunk)
        update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)
    except Exception as e:
        print(f"流程执行异常: {e}")
        update_task_status(session_id, TASK_STATUS_FAILED, is_stream)
        if is_stream:
            push_to_session(session_id, SSEEvent.ERROR, {"error": str(e)})


# ==================== 多智能体问答编排 ====================

# 问数链路专用常驻事件循环（进程级单例）：aiohttp/ES 连接池绑定创建时的循环，
# 若每次 asyncio.run 新建循环，第二次查询会报 "Event loop is closed"
_data_loop = None
_DATA_LOOP_LOCK = threading.Lock()


def _fmt_num(v):
    """数字友好化：整数千分位、小数保留两位"""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return f"{int(v):,}"
    if isinstance(v, (int, float)):
        return f"{v:,.2f}"
    return str(v)


def _format_data_result(result_val) -> str:
    """把 NL2SQL 执行结果格式化为人类可读中文（替代原始 [{'GMV': 28251.0}] 形式）"""
    try:
        if isinstance(result_val, list) and result_val and all(isinstance(r, dict) for r in result_val):
            rows = ["；".join(f"{k}：{_fmt_num(v)}" for k, v in row.items()) for row in result_val]
            if len(rows) == 1:
                return f"查询结果：{rows[0]}"
            return "查询完成，共 {} 行结果：\n".format(len(rows)) + "\n".join(f"• {r}" for r in rows)
        if isinstance(result_val, dict):
            return "查询结果：" + "；".join(f"{k}：{_fmt_num(v)}" for k, v in result_val.items())
        if isinstance(result_val, list):
            return "查询结果：" + "；".join(_fmt_num(v) for v in result_val)
        return f"查询结果：{_fmt_num(result_val)}"
    except Exception:
        return f"查询结果：{result_val}"


def _run_data_query(query: str, on_stage=None) -> tuple[str, str]:
    """
    数据查询意图：走 NL2SQL 问数链路，返回 (精简答案文本, 生成SQL)。
    on_stage(stage_label)：每执行到一个节点实时回调（用于 SSE 进度推送），
    中间过程不再堆进最终答案，用户等待期间能看到步骤流转。
    异常时抛出由上层兜底。
    """
    import asyncio
    global _data_loop
    from app.agent.graph import graph
    from app.agent.state import DataAgentState
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.es_client_manager import es_client_manager
    from app.clients.mysql_client_manager import dw_mysql_client_manager, meta_mysql_client_manager
    from app.clients.qdrant_client_manager import qdrant_client_manager
    from app.repositories.es.value_es_repository import ValueEsRepository
    from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
    from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
    from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
    from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository

    async def _inner():
        if embedding_client_manager.client is None:
            embedding_client_manager.init()
        if qdrant_client_manager.client is None:
            qdrant_client_manager.init()
        if es_client_manager.client is None:
            es_client_manager.init()
        if dw_mysql_client_manager.engine is None:
            dw_mysql_client_manager.init()
        if meta_mysql_client_manager.engine is None:
            meta_mysql_client_manager.init()

        async with dw_mysql_client_manager.session_factory() as dw_session, \
                   meta_mysql_client_manager.session_factory() as meta_session:
            context = {
                "embedding_client": embedding_client_manager.client,
                "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
                "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
                "value_es_repository": ValueEsRepository(es_client_manager.client),
                "meta_mysql_repository": MetaMysqlRepository(session=meta_session),
                "dw_mysql_repository": DwMysqlRepository(session=dw_session),
            }
            state = DataAgentState(query=query)
            events = []
            # custom：节点内 writer 上报的进度事件；updates：节点状态更新（含 generate_sql 产出的 sql）
            async for chunk in graph.astream(input=state, context=context, stream_mode=["custom", "updates"]):
                events.append(chunk)
            return events

    # 进程级常驻事件循环：aiohttp（ES 客户端）连接池绑定在创建它的事件循环上，
    # 若每次查询用 asyncio.run 新建循环，第二次调用会报 "Event loop is closed"。
    # 改为单例后台循环，所有问数请求复用（兼容 FastAPI async 端点与同步后台线程两种调用环境）。
    if _data_loop is None or _data_loop.is_closed():
        with _DATA_LOOP_LOCK:
            if _data_loop is None or _data_loop.is_closed():
                _data_loop = asyncio.new_event_loop()
                threading.Thread(target=_data_loop.run_forever, daemon=True, name="data-agent-loop").start()
    future = asyncio.run_coroutine_threadsafe(_inner(), _data_loop)
    events = future.result(timeout=300)
    # 提取结果与 SQL，组装精简答案；中间步骤经 on_stage 实时上报（不进答案文本）
    result_val = None
    sql_text = ""
    seen_stages: set = set()

    def _handle_custom(d: dict):
        nonlocal result_val
        s = str(d.get("stage", ""))
        if s and s not in seen_stages:
            seen_stages.add(s)
            if on_stage:
                try:
                    on_stage(s)
                except Exception:
                    pass
        if "result" in d:
            result_val = d["result"]

    for ev in events:
        if isinstance(ev, tuple) and len(ev) == 2:
            mode, payload = ev
            if mode == "custom" and isinstance(payload, dict):
                _handle_custom(payload)
            elif mode == "updates" and isinstance(payload, dict):
                for node_out in payload.values():
                    if isinstance(node_out, dict) and node_out.get("sql"):
                        sql_text = str(node_out["sql"])
        elif isinstance(ev, dict):
            _handle_custom(ev)
    if result_val is None or result_val == [] or result_val == {} or (
        isinstance(result_val, list) and all(
            v is None
            for row in result_val
            for v in (row.values() if isinstance(row, dict) else [row])
        )
    ):
        answer = (
            "本次查询未命中数据：数据仓库样例数据的时间范围为 2025-01 ~ 2025-03，"
            "请调整时间条件后重试（例如：\"华北地区2025年1月的销售总额是多少\"）。"
        )
    else:
        answer = _format_data_result(result_val)
    return answer, sql_text


def run_orchestrated_query(session_id: str, user_query: str, is_stream: bool = False):
    """
    多智能体统一问答编排（智库为主、问数为辅）：
    1. FAQ 缓存命中 → 直接返回（缓存加速）
    2. 问数智能体(Flash) 分析理解 → 意图路由
    3. data 意图 → NL2SQL 问数链路；knowledge/chitchat → 智库 RAG（用改写后的查询）
    4. 主智能体(Pro) 答案质量兜底 → 兜底话术
    5. 结果写入 FAQ 缓存
    所有 LLM 步骤失败均降级不阻断。
    """
    import json as _json
    orchestrator = get_orchestrator()
    analyst = get_data_analysis_agent()

    try:
        # 0. 流式模式先推"意图识别中"，让用户立刻看到响应而非空白等待
        if is_stream:
            push_to_session(session_id, SSEEvent.PROGRESS, {"done_list": [], "running_list": ["意图识别中…"]})

        # 1. FAQ 缓存
        cached = orchestrator.cache_get(user_query)
        if cached:
            answer = cached["answer"]
            update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)
            update_task_result(session_id, "answer", answer, is_stream)
            if is_stream:
                push_to_session(session_id, SSEEvent.FINAL, {"answer": answer, "from_cache": True})
            logger.info(f"[{session_id}] FAQ 缓存命中")
            return

        # 2. 问数智能体分析理解（兜底：失败降级为 knowledge + 原查询）
        analysis = analyst.analyze(user_query)
        intent = analysis["intent"]
        rewritten = analysis.get("rewritten_query") or user_query
        logger.info(f"[{session_id}] 问数智能体分析：intent={intent}, model_ok={analysis.get('model_ok')}, reason={analysis.get('reason')}")

        # 3. 意图路由（两条独立线路：data→NL2SQL 查 MySQL / knowledge→智库 RAG 检索）
        data_sql = ""
        if intent == "data":
            # ---- 线路A：问数（NL2SQL）----
            done_steps: list = []

            def _stage_cb(label: str):
                """问数节点实时进度：流式推 SSE，非流式写任务 done_list（供 /status 轮询）"""
                if label in done_steps:
                    return
                done_steps.append(label)
                try:
                    if is_stream:
                        push_to_session(session_id, SSEEvent.PROGRESS, {
                            "done_list": done_steps[:-1], "running_list": [label]})
                    else:
                        add_done_task(session_id, label)
                except Exception:
                    pass

            try:
                if is_stream:
                    push_to_session(session_id, SSEEvent.PROGRESS, {
                        "done_list": ["意图识别：数据查询"], "running_list": ["连接数据仓库…"]})
                answer, data_sql = _run_data_query(rewritten, on_stage=_stage_cb)
                source = "nl2sql"
            except Exception as e:
                logger.warning(f"[{session_id}] 问数链路失败，使用主智能体兜底：{e}")
                answer = orchestrator.fallback_answer(user_query, intent="data", reason=str(e)[:100])
                source = "fallback"
        elif intent == "chitchat":
            answer = FALLBACK_ANSWER("chitchat", user_query)
            source = "chitchat"
        else:
            # ---- 线路B：知识问答（智库 RAG）----
            if is_stream:
                push_to_session(session_id, SSEEvent.PROGRESS, {
                    "done_list": ["意图识别：知识库检索"], "running_list": ["检索知识库…"]})
            update_task_status(session_id, TASK_STATUS_PROCESSING, is_stream)
            run_query_graph(session_id, rewritten, is_stream)
            answer = get_task_result(session_id, "answer", "")
            source = "kb_rag"

        # 4. 主智能体答案质量兜底（非流式才做完整检查；流式答案已在 SSE 推送）
        if not is_stream:
            quality = orchestrator.check_answer_quality(user_query, answer)
            if not quality["ok"]:
                logger.warning(f"[{session_id}] 答案质量不足({quality['reason']})，主智能体生成兜底话术")
                answer = orchestrator.fallback_answer(user_query, intent=intent, reason=quality["reason"])
                source = "fallback"

        # 5. 写 FAQ 缓存（兜底/澄清确认/引导类回答不入缓存，避免临时故障或多轮追问被永久记住）+ 审计
        _no_cache_marks = ("请明确", "哪个产品", "请补充", "您是想问", "没能", "识别出要查询的产品", "支持查询的产品", "请先")
        _is_clarify = any(m in answer for m in _no_cache_marks)
        if answer and source != "fallback" and not _is_clarify:
            orchestrator.cache_put(user_query, answer, source=source)
        update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)
        update_task_result(session_id, "answer", answer, is_stream)
        if is_stream and source != "kb_rag":
            push_to_session(session_id, SSEEvent.FINAL, {
                "answer": answer, "intent": intent, "source": source,
                "sql": data_sql, "analysis": analysis,
            })
    except Exception as e:
        logger.error(f"[{session_id}] 编排异常：{e}", exc_info=True)
        update_task_status(session_id, TASK_STATUS_FAILED, is_stream)
        answer = get_orchestrator().fallback_answer(user_query, intent="knowledge", reason=str(e)[:100])
        update_task_result(session_id, "answer", answer, is_stream)
        if is_stream:
            push_to_session(session_id, SSEEvent.FINAL, {"answer": answer, "fallback": True})


def FALLBACK_ANSWER(intent: str, query: str) -> str:
    """闲聊/异常的快速回复（固定模板，无需 LLM）"""
    from ai_agents.orchestrator import FALLBACK_TEMPLATES
    return FALLBACK_TEMPLATES.get(intent, FALLBACK_TEMPLATES["empty"]).format(q=query[:50])


def update_task_result(session_id: str, key: str, value: str, is_stream: bool):
    """写入任务结果（task_utils.set_task_result）"""
    try:
        from utils.task_utils import set_task_result
        set_task_result(session_id, key, value)
    except Exception:
        pass


@app.post("/query")
async def query(background_tasks: BackgroundTasks, request: QueryRequest):
    """
    多智能体统一问答入口：
    FAQ缓存 → 问数智能体(Flash)分析理解 → 意图路由（数据查询走NL2SQL / 知识问答走智库RAG）→ 主智能体(Pro)质量兜底
    """
    user_query = request.query
    session_id = request.session_id if request.session_id else str(uuid.uuid4())
    is_stream = request.is_stream

    if is_stream:
        create_sse_queue(session_id)
    update_task_status(session_id, TASK_STATUS_PROCESSING, is_stream)

    if is_stream:
        background_tasks.add_task(run_orchestrated_query, session_id, user_query, True)
        return {
            "message": "结果正在处理中...",
            "session_id": session_id
        }
    else:
        run_orchestrated_query(session_id, user_query, False)
        answer = get_task_result(session_id, "answer", "")
        return {
            "message": "处理完成！",
            "session_id": session_id,
            "answer": answer,
            "done_list": []
        }


@app.get("/stream/{session_id}")
async def stream(session_id: str, request: Request):
    print("调用流式/stream...")
    return StreamingResponse(
        sse_generator(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.delete("/history/{session_id}")
async def clear_chat_history(session_id: str):
    count = clear_history(session_id)
    return {"message": "历史会话已清空", "deleted_count": count}


@app.get("/history/{session_id}")
async def history(session_id: str, limit: int = 50):
    try:
        records = get_recent_messages(session_id, limit=limit)
        items = []
        for r in records:
            items.append({
                "_id": str(r.get("_id")) if r.get("_id") is not None else "",
                "session_id": r.get("session_id", ""),
                "role": r.get("role", ""),
                "text": r.get("text", ""),
                "rewritten_query": r.get("rewritten_query", ""),
                "item_names": r.get("item_names", []),
                "ts": r.get("ts")
            })
        return {"session_id": session_id, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history error: {e}")


# ==================== 问数相关 API（BOSS问数 data_agent）====================

class DataQueryRequest(BaseModel):
    query: str = Field(..., description="自然语言问数请求，如：统计华北地区销售总额")


@app.post("/api/data/query", summary="NL2SQL 智能问数（生成SQL → 审查 → 执行）")
async def data_query(request: DataQueryRequest):
    """
    执行 BOSS问数 LangGraph 工作流：
    关键词提取 → 三路召回 → 过滤 → 生成SQL → 审查/校验 → 纠错 → 执行
    SQL 审查未通过时自动转纠错重写；执行结果与审查信息一并返回。
    """
    from app.agent.graph import graph
    from app.agent.state import DataAgentState
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.es_client_manager import es_client_manager
    from app.clients.mysql_client_manager import dw_mysql_client_manager, meta_mysql_client_manager
    from app.clients.qdrant_client_manager import qdrant_client_manager
    from app.repositories.es.value_es_repository import ValueEsRepository
    from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
    from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

    try:
        # 客户端懒初始化（幂等：已初始化则跳过，连接池跨请求复用）
        if embedding_client_manager.client is None:
            embedding_client_manager.init()
        if qdrant_client_manager.client is None:
            qdrant_client_manager.init()
        if es_client_manager.client is None:
            es_client_manager.init()
        if dw_mysql_client_manager.engine is None:
            dw_mysql_client_manager.init()
        if meta_mysql_client_manager.engine is None:
            meta_mysql_client_manager.init()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"基础设施初始化失败（请检查 docker compose 服务是否启动）：{e}")

    async with dw_mysql_client_manager.session_factory() as dw_session, \
               meta_mysql_client_manager.session_factory() as meta_session:
        context = {
            "embedding_client": embedding_client_manager.client,
            "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
            "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
            "value_es_repository": ValueEsRepository(es_client_manager.client),
            "meta_mysql_repository": None,  # 占位：当前节点链路未使用元库读，需要时替换为 MetaMysqlRepository(meta_session)
            "dw_mysql_repository": None,    # 下方用审查版 repository
        }
        from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
        context["dw_mysql_repository"] = DwMysqlRepository(session=dw_session)

        state = DataAgentState(query=request.query)
        events = []
        try:
            async for chunk in graph.astream(input=state, context=context, stream_mode="custom"):
                events.append(chunk)
        except SQLReviewError as e:
            raise HTTPException(status_code=422, detail={"message": "SQL 审查未通过", "reasons": e.reasons})
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"问数工作流执行失败：{e}")

    return {"code": 200, "query": request.query, "events": events}


# ==================== 健康 ====================

@app.get("/health")
async def health():
    return {"ok": True, "service": "河南鼎誉智数审查项目"}


# ==================== 启动 ====================
if __name__ == "__main__":
    logger.info("河南鼎誉智数审查项目服务启动中...（智库 + 问数 + 审查）")
    uvicorn.run(app, host="127.0.0.1", port=17954)
