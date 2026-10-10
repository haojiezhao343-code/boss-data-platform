"""
SQL 执行审查器

挂载点：app/repositories/mysql/dw_mysql_repository.py
- execute_sql()：执行前统一拦截（覆盖工作流与所有直接调用路径）
- validate_sql()：补齐原项目缺失的校验方法（先审查、再 EXPLAIN 验证语法）

审查规则（按顺序执行，任一命中即拒绝）：
1. 基础校验：空语句 / 超长语句
2. 多语句检测：禁止分号分隔的多条语句
3. 语句类型白名单：仅允许 SELECT / WITH(CTE) / SHOW / DESCRIBE / DESC / EXPLAIN
4. 危险关键字黑名单：INSERT / UPDATE / DELETE / DROP / TRUNCATE / ALTER / CREATE ...
   （含注释剥离，防止用注释绕过；词边界匹配，避免误伤列名 contain 等场景）
5. 系统库封禁：information_schema / mysql / performance_schema / sys（可配置）
6. 强制 LIMIT：无 LIMIT 自动追加；LIMIT 超上限自动改写（改写属低风险告警，不拒绝）

配置（.env）：
- SQL_REVIEW_MAX_LIMIT：返回行数上限，默认 1000
- SQL_REVIEW_FORBID_SYSTEM_DB：是否封禁系统库，默认 true
"""
import os
import re

from review.audit_log import audit_logger

# ==================== 规则常量 ====================

# 允许的语句类型（首关键字，不区分大小写）
_ALLOWED_FIRST_KEYWORDS = {"select", "with", "show", "describe", "desc", "explain"}

# 危险关键字黑名单（词边界匹配，大小写不敏感）
_FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "truncate", "alter",
    "create", "grant", "revoke", "replace", "merge", "rename",
    "call", "set", "use", "lock", "unlock", "kill", "shutdown",
    "handler", "prepare", "execute", "load_file", "load data",
    "into outfile", "into dumpfile",
]

# MySQL 系统库（只读保护）
_SYSTEM_DBS = ["information_schema", "performance_schema", "mysql", "sys"]

# 注释剥离：/* ... */ 块注释、-- 行注释、# 行注释
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_LINE_COMMENT_DASH = re.compile(r"--[^\n]*")
_RE_LINE_COMMENT_HASH = re.compile(r"#[^\n]*")

# LIMIT 子句提取（limit N / limit N, M / limit M offset N）
_RE_LIMIT = re.compile(r"\blimit\s+(\d+)\s*(?:,\s*(\d+)\s*|\s+offset\s+(\d+)\s*)?$", re.IGNORECASE)
_RE_LIMIT_ANYWHERE = re.compile(r"\blimit\s+(\d+)\s*(?:,\s*(\d+)\s*)?", re.IGNORECASE)


class SQLReviewError(Exception):
    """SQL 审查未通过时抛出，message 为可读的拒绝原因列表"""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("SQL 审查未通过：" + "；".join(reasons))


class SQLReviewer:
    """SQL 执行审查器"""

    def __init__(
        self,
        max_limit: int | None = None,
        forbid_system_db: bool | None = None,
    ):
        self.max_limit = int(max_limit or os.getenv("SQL_REVIEW_MAX_LIMIT", "1000"))
        if forbid_system_db is not None:
            self.forbid_system_db = forbid_system_db
        else:
            self.forbid_system_db = os.getenv("SQL_REVIEW_FORBID_SYSTEM_DB", "true").lower() in ("1", "true", "yes")

    # ==================== 内部规则 ====================

    @staticmethod
    def _strip_comments(sql: str) -> str:
        """剥离三种 MySQL 注释，防止利用注释绕过关键字检测"""
        sql = _RE_BLOCK_COMMENT.sub(" ", sql)
        sql = _RE_LINE_COMMENT_DASH.sub(" ", sql)
        sql = _RE_LINE_COMMENT_HASH.sub(" ", sql)
        return sql.strip()

    def _check_basic(self, sql: str, reasons: list[str]):
        if not sql or not sql.strip():
            reasons.append("SQL 为空")
        if len(sql) > 10000:
            reasons.append(f"SQL 过长（{len(sql)} 字符，上限 10000）")

    def _check_multi_statement(self, sql: str, reasons: list[str]):
        # 去掉字符串字面量后检查分号（简化：按引号分段跳过字符串内部）
        body = self._strip_string_literals(sql)
        statements = [s for s in body.split(";") if s.strip()]
        if len(statements) > 1:
            reasons.append(f"禁止多语句执行（检测到 {len(statements)} 条语句）")

    @staticmethod
    def _strip_string_literals(sql: str) -> str:
        """去掉 '...' "..." `...` 内部内容，避免字符串里的分号/关键字误报"""
        result, i, n = [], 0, len(sql)
        while i < n:
            ch = sql[i]
            if ch in ("'", '"', "`"):
                quote = ch
                i += 1
                while i < n:
                    if sql[i] == "\\" and quote != "`":
                        i += 2
                        continue
                    if sql[i] == quote:
                        # 处理双写转义（'' / ""）
                        if i + 1 < n and sql[i + 1] == quote:
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                result.append(" ")
            else:
                result.append(ch)
                i += 1
        return "".join(result)

    def _check_first_keyword(self, clean_sql: str, reasons: list[str]):
        first = re.match(r"^\s*\(*\s*([a-zA-Z]+)", clean_sql)
        if not first or first.group(1).lower() not in _ALLOWED_FIRST_KEYWORDS:
            kw = first.group(1) if first else "<空>"
            reasons.append(f"仅允许查询类语句（SELECT/WITH/SHOW/DESCRIBE/EXPLAIN），实际首关键字：{kw}")

    def _check_forbidden(self, clean_sql: str, reasons: list[str]):
        # 在字符串字面量剥离后的文本上做词边界匹配
        body = self._strip_string_literals(clean_sql)
        lowered = body.lower()
        for kw in _FORBIDDEN_KEYWORDS:
            pattern = r"\b" + re.escape(kw).replace(r"\ ", r"\s+") + r"\b"
            if re.search(pattern, lowered):
                reasons.append(f"包含禁止的关键字/子句：{kw.upper()}")

    def _check_system_db(self, clean_sql: str, reasons: list[str]):
        if not self.forbid_system_db:
            return
        lowered = self._strip_string_literals(clean_sql).lower()
        for db in _SYSTEM_DBS:
            if re.search(r"\b" + re.escape(db) + r"\s*\.", lowered):
                reasons.append(f"禁止访问系统库：{db}")

    def _enforce_limit(self, clean_sql: str) -> tuple[str, list[str]]:
        """强制 LIMIT：返回（改写后的 SQL, 告警列表）。仅对 SELECT/WITH 生效。"""
        warnings: list[str] = []
        first = re.match(r"^\s*\(*\s*([a-zA-Z]+)", clean_sql)
        if not first or first.group(1).lower() not in ("select", "with"):
            return clean_sql, warnings

        # 从字符串字面量剥离后的文本中找末尾 LIMIT（避免匹配字符串里的 'limit'）
        body = self._strip_string_literals(clean_sql).rstrip().rstrip(";").rstrip()
        m = _RE_LIMIT.search(body)
        if m:
            # 解析既有 LIMIT：支持 limit N / limit offset, N / limit N offset M
            if m.group(2) is not None:              # limit offset, N
                actual = int(m.group(2))
            elif m.group(3) is not None:            # limit N offset M
                actual = int(m.group(1))
            else:                                    # limit N
                actual = int(m.group(1))
            if actual > self.max_limit:
                rewritten = self._rewrite_limit_value(clean_sql, self.max_limit)
                warnings.append(f"LIMIT {actual} 超过上限 {self.max_limit}，已自动改写")
                return rewritten, warnings
            return clean_sql, warnings

        # 无 LIMIT：追加
        rewritten = clean_sql.rstrip().rstrip(";").rstrip() + f"\nLIMIT {self.max_limit}"
        warnings.append(f"原 SQL 未包含 LIMIT，已自动追加 LIMIT {self.max_limit}")
        return rewritten, warnings

    @staticmethod
    def _rewrite_limit_value(sql: str, new_limit: int) -> str:
        """把末尾 LIMIT 子句的行数值改写为 new_limit（保留 offset 部分）"""
        body = sql.rstrip().rstrip(";").rstrip()
        m = _RE_LIMIT.search(body)
        if not m:
            return sql
        if m.group(2) is not None:                   # limit offset, N -> limit offset, new
            new_tail = f"LIMIT {m.group(1)}, {new_limit}"
        elif m.group(3) is not None:                 # limit N offset M -> limit new offset M
            new_tail = f"LIMIT {new_limit} OFFSET {m.group(3)}"
        else:                                        # limit N
            new_tail = f"LIMIT {new_limit}"
        return body[: m.start()] + new_tail

    # ==================== 对外入口 ====================

    def review(
        self,
        sql: str,
        operator: str = "manual",
        action: str = "manual_check",
        write_audit: bool = True,
    ) -> dict:
        """
        审查一条 SQL。

        :return: {
            "approved": bool,
            "reasons": [...],        # 拒绝原因
            "warnings": [...],       # 通过但自动改写等告警
            "risk_level": "low"|"medium"|"high",
            "cleaned_sql": "...",    # 剥离注释、强制 LIMIT 后的最终可执行 SQL
        }
        :raises SQLReviewError: 审查未通过时抛出
        """
        reasons: list[str] = []
        warnings: list[str] = []

        raw = sql or ""
        clean_sql = self._strip_comments(raw)

        self._check_basic(clean_sql, reasons)
        if not reasons:
            self._check_multi_statement(clean_sql, reasons)
            self._check_first_keyword(clean_sql, reasons)
            self._check_forbidden(clean_sql, reasons)
            self._check_system_db(clean_sql, reasons)

        risk_level = "high" if reasons else "low"
        if reasons:
            if write_audit:
                audit_logger.record(
                    action_type="sql",
                    action=action,
                    operator=operator,
                    target=raw,
                    approved=False,
                    risk_level=risk_level,
                    reasons=reasons,
                    details={"sql_length": len(raw)},
                )
            raise SQLReviewError(reasons)

        # 通过基础审查 → 强制 LIMIT
        final_sql, limit_warnings = self._enforce_limit(clean_sql)
        warnings.extend(limit_warnings)
        if limit_warnings:
            risk_level = "medium"

        if write_audit:
            audit_logger.record(
                action_type="sql",
                action=action,
                operator=operator,
                target=raw,
                approved=True,
                risk_level=risk_level,
                reasons=[],
                details={"warnings": warnings, "sql_length": len(raw)},
            )
        return {
            "approved": True,
            "reasons": [],
            "warnings": warnings,
            "risk_level": risk_level,
            "cleaned_sql": final_sql,
        }


# 模块级单例
_sql_reviewer: SQLReviewer | None = None


def get_sql_reviewer() -> SQLReviewer:
    global _sql_reviewer
    if _sql_reviewer is None:
        _sql_reviewer = SQLReviewer()
    return _sql_reviewer
