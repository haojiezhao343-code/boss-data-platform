# app/repositories/mysql/dw_mysql_repository.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from review.sql_reviewer import get_sql_reviewer


class DwMysqlRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_values(self, table_name: str, column_name: str, limit: int = 10):
        """
        查询指定字段的取值

        :param table_name:
        :param column_name:
        :return: ["C001","C002","C003","C004","C005"]
        """
        # 定义sql
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        # 执行sql
        result = await self.session.execute(text(sql))
        # [row(C001),row(C002),row(C003)]
        return result.scalars().fetchall()

    async def get_column_types(self, table_name: str):
        """
        查询表中字段的类型
        :param table_name:
        :return: 字段 {"字段名":"字段类型"}
        """
        # 定义sql
        sql = f"show columns from {table_name}"
        # 执行sql
        result = await self.session.execute(text(sql))
        # [row(),row(),row()]

        # 获取结果，组装数据
        return {row.Field: row.Type for row in result.fetchall()}

    async def get_db_info(self):
        """
        查询数据库环境信息，版本、方言
        :return:
        """
        # 查询数据版本
        result = await self.session.execute(text("select version()"))
        # 解析结果
        version = result.scalar()

        # 查询数据方言
        dialect = self.session.get_bind().dialect.name
        return {"version": version, "dialect": dialect}


    async def execute_sql(self, sql):
        """
        执行最终的sql语句，获取结果

        ★ 审查模块挂载点：执行前先过 SQL 审查（只读白名单/危险语句拦截/强制LIMIT）
          审查不通过抛 SQLReviewError，由上层（工作流条件边）转交 correct_sql 纠错。
        :param sql:
        :return:
        """
        # ---- SQL 审查（未通过直接拒绝，不触达数据库）----
        review_result = get_sql_reviewer().review(
            sql, operator="data_agent_graph", action="execute_sql"
        )
        # 使用审查后的 SQL（剥离注释 + 强制 LIMIT 改写后的版本）
        final_sql = review_result["cleaned_sql"]

        result = await self.session.execute(text(final_sql))
        return [dict(row)  for row in result.mappings().fetchall()]

    async def validate_sql(self, sql):
        """
        校验 SQL（补齐原项目缺失的方法：validate_sql 节点此前调用本方法必抛 AttributeError）

        校验 = 两步：
        1. SQL 审查（规则审查，未通过抛 SQLReviewError）
        2. EXPLAIN 语法/表结构验证（不真实执行查询，只让 MySQL 做执行计划分析）
        :param sql:
        :return: None（校验通过）；校验失败抛异常，由 validate_sql 节点捕获
        """
        # 第一步：规则审查
        get_sql_reviewer().review(sql, operator="data_agent_graph", action="validate_sql")

        # 第二步：EXPLAIN 验证语法与表字段真实存在（仅生成执行计划，不返回数据）
        await self.session.execute(text(f"EXPLAIN {sql}"))