# 河南鼎誉智数审查项目（boss-data-platform）

> **河南鼎誉智数审查项目** —— 面向企业的智能审查与数智问答平台：以多智能体协同承载「智库问答 / 文档审查 / 智能问数」三大能力。

> 由 **data_agent（BOSS问数 NL2SQL 智能问数）** 与 **knowledge_base（BOSS智库 知识库）** 深度合并而成，并新增 **审查模块（review）**。

## 项目结构

```
boss-data-platform/
├── main.py                 # 统一 FastAPI 入口（智库API + 问数API + 审查API），端口 7954
├── pyproject.toml          # 合并后的依赖（uv 管理）
├── .env.example            # 环境配置模板（复制为 .env 后填入真实值）
│
├── app/                    # 【BOSS问数】LangGraph NL2SQL 工作流
│   ├── agent/              #   工作流图与 12 个节点（关键词→三路召回→过滤→生成SQL→校验→纠错→执行）
│   ├── clients/            #   MySQL/Qdrant/ES/TEI 客户端管理器
│   ├── conf/               #   配置加载（敏感项支持 .env 环境变量覆盖）
│   ├── core/               #   loguru 日志
│   ├── models/             #   SQLAlchemy ORM + Qdrant/ES payload
│   ├── repositories/       #   数据访问层（dw_mysql_repository 内置 SQL 审查拦截）
│   ├── scripts/            #   元知识构建脚本（python -m app.scripts.build_meta_knowledge）
│   └── services/           #   元知识构建服务
├── conf/                   # BOSS问数 YAML 配置（app_config.yaml / meta_config.yaml）
├── prompts/                # BOSS问数提示词
├── docker/                 # BOSS问数编排：MySQL(dw+meta) 13306 / ES(IK) 19200 / Qdrant 6333 / TEI 19081 / Kibana
│
├── config/                 # 【BOSS智库】配置层（MinIO/Milvus/BGE/LLM/Reranker/MCP）
├── processor/              # 【BOSS智库】导入工作流(PDF/MD→切分→BGE→Milvus) + 问答工作流(三路检索→RRF→重排→生成)
├── tool/                   # 【BOSS智库】colorlog 日志
├── utils/                  # 【BOSS智库】Milvus/MinIO/Mongo/Embedding/SSE 工具
├── web/                    # 【BOSS智库】chat.html / import.html 前端页面
│
└── review/                 # ★ 审查模块（新增）
    ├── sql_reviewer.py     #   SQL 执行审查：只读白名单/危险语句拦截/多语句检测/强制LIMIT/系统库封禁
    ├── content_reviewer.py #   知识库内容审查：敏感词/合规校验/重复文档检测
    ├── audit_log.py        #   审计日志（JSONL 持久化 + 内存查询）
    ├── api.py              #   审查 REST API（/api/review/*）
    └── sensitive_words.txt #   敏感词库（可自行扩充）
```

## 审查模块说明

### SQL 执行审查（挂载于 BOSS问数链路）
- **拦截点**：`app/repositories/mysql/dw_mysql_repository.py` 的 `execute_sql()` 入口统一拦截（覆盖工作流与测试脚本所有执行路径），并补齐了原项目缺失的 `validate_sql()` 方法。
- **规则**：只允许 `SELECT/WITH/SHOW/DESCRIBE/EXPLAIN`；拦截 `INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/...` 等写操作与 DDL；拦截多语句与 `INTO OUTFILE`；系统库访问封禁；无 `LIMIT` 自动追加、超上限自动改写。
- **审计**：每次审查（通过/拒绝）均写入 `logs/audit/*.jsonl`。

### 知识库内容审查（挂载于 BOSS智库导入链路）
- **拦截点**：`main.py` 的 `/upload` 后台任务执行前，文档先过内容审查，不通过则任务直接失败并留审计记录。
- **规则**：敏感词检测（`review/sensitive_words.txt` 可扩充）、空文档/超长校验、基于内容 MD5 的重复文档检测。

### 审查 API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/review/audit-logs` | 审计日志分页查询（支持 type/approved 过滤） |
| POST | `/api/review/sql/check` | 手动测试 SQL 审查（只审查不执行） |
| GET | `/api/review/stats` | 审查统计（总数/通过率/拦截分布） |

## 启动方式

```bash
# 1. 复制并填写环境配置
cp .env.example .env

# 2. 启动基础设施（问数侧 MySQL/ES/Qdrant/TEI/Kibana + 智库侧 Milvus/Mongo/MinIO/etcd）
docker compose up -d

# 3. 启动统一服务（端口 7954）
python main.py

# 4. 元知识构建（问数侧，首次使用需要）
python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

## 主要 API

| 分组 | 路径 | 说明 |
|------|------|------|
| 智库 | `/upload` `/status/{task_id}` | 文档导入（导入前内容审查） |
| 智库 | `/query` `/stream/{session_id}` `/history/{session_id}` | 知识库问答 |
| 问数 | `POST /api/data/query` | NL2SQL 问数（SQL 生成→审查→执行） |
| 审查 | `/api/review/*` | 审计日志 / SQL 审查测试 / 统计 |
| 页面 | `/chat.html` `/import.html` | 智库前端页面 |
| 健康 | `/health` | 健康检查 |

## 合并说明

- 两个原项目顶层包名（`app/` vs `config/ processor/ ...`）天然无冲突，代码 import 路径保持原样零改动。
- 合并前的完整备份位于 `F:/backup_20261010/`（knowledge_base 与 data_agent 各一份，含 git 历史）。
- 原 `data_agent/conf/app_config.yaml` 中的明文 API Key 与 MySQL 密码已外移至 `.env`（`DA_LLM_*` / `MYSQL_*`），环境变量优先于 yaml。
- BGE embedding 模型（`docker/embedding/bge-large-zh-v1.5/`）体积大且被 gitignore，从原项目随 docker/ 目录一并复制。
