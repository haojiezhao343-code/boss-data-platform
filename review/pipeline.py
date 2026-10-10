"""
文档审查管线（完整版）

融合三道防线（参考企业级文档审核分层防御架构）：
1. 规则审查（review/content_reviewer）：敏感词/空文件/超大/重复 —— 快速硬拦截
2. LLM 语义审查（agents/reviewer_agent，Qwen3.8-27B）：敏感信息泄露/违规内容/质量 —— 语义软审查
3. 人工审核（review/manual_review）：中风险人工把关 —— HITL 闭环

决策矩阵（decision）：
- 规则拒绝                                      → rejected（直接拒绝，不入队）
- 规则通过 + LLM pass（低风险）                  → auto_approved（自动通过，直接导入）
- 规则通过 + LLM suspect（可疑但未定性）          → auto_approved（放行入库，风险留痕；2026-10-10 策略调整：仅高风险拦截）
- 规则通过 + LLM reject（高风险）                → rejected
- 规则通过 + LLM 不可用（超时/异常，兜底策略）    → pending（安全优先，转人工审核）

所有决策写入审计日志。
"""
from ai_agents.reviewer_agent import get_review_agent
from review.audit_log import audit_logger
from review.content_reviewer import ContentReviewError, get_content_reviewer
from review.manual_review import get_manual_queue


def review_document_pipeline(file_path: str, filename: str = "", task_id: str = "") -> dict:
    """
    完整文档审查管线。

    :return: {
        "decision": "auto_approved" | "pending" | "rejected",
        "risk_level": "low"|"medium"|"high",
        "rule_result": {...} | None,     # 规则审查结果（拒绝时为 None）
        "llm_result": {...} | None,      # LLM 审查结果（失败为 None）
        "review_id": "...",              # pending 时的审核单号
        "reasons": [...],                # 汇总原因
    }
    """
    filename = filename or file_path.split("/")[-1].split("\\")[-1]
    reasons: list[str] = []

    # ---------- 第一道防线：规则审查 ----------
    try:
        rule_result = get_content_reviewer().review_document(file_path, operator="rule_engine")
    except ContentReviewError as e:
        # 规则硬拦截 → 直接拒绝（敏感词/重复/空文件等）
        audit_logger.record(
            action_type="content",
            action="pipeline_decision",
            operator="pipeline",
            target=filename,
            approved=False,
            risk_level="high",
            reasons=e.reasons,
            details={"stage": "rule", "decision": "rejected"},
        )
        return {
            "decision": "rejected",
            "risk_level": "high",
            "rule_result": None,
            "llm_result": None,
            "review_id": None,
            "reasons": e.reasons,
        }

    reasons = []  # 规则通过，进入 LLM 审查阶段

    file_md5 = rule_result.get("file_md5", "")

    # ---------- 第二道防线：LLM 语义审查（Qwen3.8-27B）----------
    # 抽取文本：md/txt 直接读，pdf 用 pypdf
    text_content = _extract_text(file_path)
    llm_result = None
    if text_content:
        llm_result = get_review_agent().review_text(title=filename, content=text_content)

    if llm_result is None:
        # ---------- 兜底策略：LLM 不可用 → 安全优先，转人工审核 ----------
        item = get_manual_queue().submit(
            filename=filename,
            file_path=str(file_path),
            file_md5=file_md5,
            risk_level="medium",
            rule_findings=[],
            llm_findings=["LLM 审查不可用（超时/异常），已按兜底策略转人工审核"],
            llm_summary="",
            task_id=task_id,
        )
        return {
            "decision": "pending",
            "risk_level": "medium",
            "rule_result": rule_result,
            "llm_result": None,
            "review_id": item["review_id"],
            "reasons": ["LLM 审查不可用，转人工审核（兜底策略）"],
        }

    # ---------- 决策矩阵 ----------
    verdict = llm_result["verdict"]
    if verdict == "pass":
        audit_logger.record(
            action_type="content",
            action="pipeline_decision",
            operator="pipeline",
            target=filename,
            approved=True,
            risk_level="low",
            reasons=[llm_result.get("summary", "规则+LLM 审查均通过")],
            details={"stage": "llm", "decision": "auto_approved", "file_md5": file_md5},
        )
        return {
            "decision": "auto_approved",
            "risk_level": "low",
            "rule_result": rule_result,
            "llm_result": llm_result,
            "review_id": None,
            "reasons": [],
        }

    if verdict == "suspect":
        # 策略调整（2026-10-10）：LLM 发现可疑点但未定性 → 不再阻断，直接放行入库（风险留痕）。
        # 只有「规则硬拦截」和「LLM 明确判定违规」两类高风险文件才拦截。
        findings = llm_result.get("findings", []) or ["LLM 判定存在可疑内容"]
        audit_logger.record(
            action_type="content",
            action="pipeline_decision",
            operator="pipeline",
            target=filename,
            approved=True,
            risk_level="medium",
            reasons=findings + ["（可疑但未定性，按「仅高风险拦截」策略放行）"],
            details={"stage": "llm", "decision": "auto_approved_with_warning", "file_md5": file_md5},
        )
        return {
            "decision": "auto_approved",
            "risk_level": "medium",
            "rule_result": rule_result,
            "llm_result": llm_result,
            "review_id": None,
            "reasons": findings + ["（可疑但未定性，按「仅高风险拦截」策略放行）"],
        }

    # verdict == "reject"
    findings = llm_result.get("findings", []) or ["LLM 判定内容违规"]
    audit_logger.record(
        action_type="content",
        action="pipeline_decision",
        operator="pipeline",
        target=filename,
        approved=False,
        risk_level="high",
        reasons=findings,
        details={"stage": "llm", "decision": "rejected", "file_md5": file_md5},
    )
    return {
        "decision": "rejected",
        "risk_level": "high",
        "rule_result": rule_result,
        "llm_result": llm_result,
        "review_id": None,
        "reasons": findings,
    }


def _extract_text(file_path: str) -> str | None:
    """为 LLM 审查抽取文本（md/txt 直读，pdf 用 pypdf，其他返回 None）"""
    from pathlib import Path
    p = Path(file_path)
    suffix = p.suffix.lower()
    try:
        if suffix in (".md", ".markdown", ".txt"):
            return p.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:80])
        if suffix in (".docx",):
            from docx import Document
            doc = Document(str(p))
            return "\n".join(par.text for par in doc.paragraphs[:500] if par.text)
    except Exception:
        return None
    return None
