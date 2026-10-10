"""
知识库内容审查器

挂载点：main.py 的 /upload 后台任务（run_graph_task 之前）
审查不通过的文档不进入导入工作流，任务标记失败并写入审计日志。

审查规则：
1. 文件存在性 / 大小校验（空文件、超大文件）
2. 敏感词检测（review/sensitive_words.txt 可扩充，命中即拒绝）
3. 文本抽取：.md/.txt 直接读取；.pdf 用 pypdf 抽取纯文本（失败跳过敏感词检测，仅做 MD5 指纹）
4. 重复文档检测：内容 MD5 与本进程内已通过审查的文档指纹比对

配置（.env）：
- REVIEW_SENSITIVE_WORDS_FILE：敏感词文件路径，默认 review/sensitive_words.txt
- REVIEW_MAX_FILE_MB：文件大小上限，默认 50 MB
"""
import hashlib
import os
from pathlib import Path

from review.audit_log import audit_logger

# 已导入文档指纹的内存上限（超出后淘汰最早的一半）
_FINGERPRINT_KEEP = 2000


class ContentReviewError(Exception):
    """内容审查未通过时抛出，message 为可读的拒绝原因"""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("内容审查未通过：" + "；".join(reasons))


class ContentReviewer:
    """知识库文档导入前审查器"""

    def __init__(
        self,
        sensitive_words_file: str | None = None,
        max_file_mb: int | None = None,
    ):
        env_file = os.getenv("REVIEW_SENSITIVE_WORDS_FILE")
        base = sensitive_words_file or env_file or "review/sensitive_words.txt"
        path = Path(base) if os.path.isabs(base) else Path(__file__).parents[1] / base
        self.sensitive_words: list[str] = self._load_sensitive_words(path)
        self.max_file_mb = int(max_file_mb or os.getenv("REVIEW_MAX_FILE_MB", "50"))
        # 已导入文档指纹：MD5 -> 文件名（进程内存级）
        self._imported_md5: dict[str, str] = {}

    # ==================== 敏感词库 ====================

    @staticmethod
    def _load_sensitive_words(path: Path) -> list[str]:
        """从词库文件加载敏感词（每行一个，# 开头为注释）"""
        words: list[str] = []
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        words.append(line)
        except OSError:
            pass
        return words

    def reload_sensitive_words(self) -> int:
        """重新加载敏感词库（词库文件更新后热生效），返回当前词数"""
        env_file = os.getenv("REVIEW_SENSITIVE_WORDS_FILE")
        base = env_file or "review/sensitive_words.txt"
        path = Path(base) if os.path.isabs(base) else Path(__file__).parents[1] / base
        self.sensitive_words = self._load_sensitive_words(path)
        return len(self.sensitive_words)

    # ==================== 文本抽取 ====================

    @staticmethod
    def _extract_text(file_path: Path) -> str | None:
        """按后缀抽取文本；PDF 抽取失败返回 None（跳过敏感词检测）"""
        suffix = file_path.suffix.lower()
        try:
            if suffix in (".md", ".markdown", ".txt"):
                return file_path.read_text(encoding="utf-8", errors="ignore")
            if suffix == ".pdf":
                from pypdf import PdfReader
                reader = PdfReader(str(file_path))
                return "\n".join((page.extract_text() or "") for page in reader.pages[:200])
        except Exception:
            return None
        return None

    # ==================== 审查主流程 ====================

    def review_document(
        self,
        file_path: str | Path,
        operator: str = "kb_import",
        write_audit: bool = True,
    ) -> dict:
        """
        审查一个待导入文档。

        :return: {"approved", "reasons", "risk_level", "file_md5", "stats"}
        :raises ContentReviewError: 审查未通过
        """
        reasons: list[str] = []
        p = Path(file_path)
        filename = p.name

        # 1. 文件基础校验
        if not p.exists() or not p.is_file():
            reasons.append(f"文件不存在或不可读：{filename}")
            content_md5 = ""
            text = None
            file_size = 0
        else:
            file_size = p.stat().st_size
            size_mb = file_size / 1024 / 1024
            if size_mb == 0:
                reasons.append("文件内容为空（0 字节）")
            elif size_mb > self.max_file_mb:
                reasons.append(f"文件过大：{size_mb:.1f} MB（上限 {self.max_file_mb} MB）")
            content_md5 = self._file_md5(p)
            text = self._extract_text(p)

            # 2. 重复文档检测（与进程内已通过审查的指纹比对）
            if content_md5 and content_md5 in self._imported_md5:
                reasons.append(
                    f"重复文档：内容与已导入的「{self._imported_md5[content_md5]}」完全相同（MD5 {content_md5[:12]}…）"
                )

            # 3. 敏感词检测
            if text is not None:
                if len(text.strip()) < 10 and p.suffix.lower() in (".md", ".markdown", ".txt"):
                    reasons.append("文档有效文本过短（少于 10 字符）")
                hits = self._find_sensitive_hits(text)
                if hits:
                    reasons.append(f"命中敏感词 {len(hits)} 个：{'、'.join(hits[:10])}")

        risk_level = "high" if reasons else "low"

        if write_audit:
            audit_logger.record(
                action_type="content",
                action="import_document",
                operator=operator,
                target=filename,
                approved=not reasons,
                risk_level=risk_level,
                reasons=reasons,
                details={
                    "file_md5": content_md5,
                    "file_size_bytes": file_size,
                    "sensitive_words_count": len(self.sensitive_words),
                },
            )

        if reasons:
            raise ContentReviewError(reasons)

        # 审查通过 → 登记指纹（供后续重复检测）
        if content_md5:
            self._register_md5(content_md5, filename)

        return {
            "approved": True,
            "reasons": [],
            "risk_level": "low",
            "file_md5": content_md5,
            "stats": {
                "filename": filename,
                "text_length": len(text) if text else 0,
                "sensitive_words_checked": len(self.sensitive_words),
            },
        }

    # ==================== 内部工具 ====================

    def _find_sensitive_hits(self, text: str) -> list[str]:
        """返回文本中命中的敏感词（去重、按词库顺序）"""
        hits: list[str] = []
        lowered = text.lower()
        for word in self.sensitive_words:
            w = word.lower()
            if w and w in lowered and word not in hits:
                hits.append(word)
        return hits

    def _register_md5(self, md5: str, filename: str):
        """登记已通过审查的文档指纹，超限时淘汰最早的一半"""
        self._imported_md5[md5] = filename
        if len(self._imported_md5) > _FINGERPRINT_KEEP:
            for k in list(self._imported_md5.keys())[: _FINGERPRINT_KEEP // 2]:
                self._imported_md5.pop(k, None)

    @staticmethod
    def _file_md5(path: Path) -> str:
        """流式计算文件 MD5（不整读内存）"""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


# 模块级单例
_content_reviewer: ContentReviewer | None = None


def get_content_reviewer() -> ContentReviewer:
    global _content_reviewer
    if _content_reviewer is None:
        _content_reviewer = ContentReviewer()
    return _content_reviewer
