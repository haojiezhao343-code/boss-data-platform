# app/repositories/mysql/meta_mysql_repository.py
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL


class MetaMysqlRepository:
    def __init__(self,session:AsyncSession):
        self.session = session

    async def save_table_infos(self, table_infos:list[TableInfoMySQL]):
        """
        保存表信息到meta数据库
        :param table_infos:
        :return:
        """
        self.session.add_all(table_infos)

    async def save_column_infos(self, column_infos:list[ColumnInfoMySQL]):
        """
        表存字段信息到meta数据库
        :param column_infos:
        :return:
        """
        self.session.add_all(column_infos)

    async def save_metric_infos(self, metric_infos:list[MetricInfoMySQL]):
        """
                保存指标信息到meta数据库
                :param metric_infos:
                :return:
                """
        self.session.add_all(metric_infos)

    async def save_column_metric_infos(self, column_metric_infos:list[ColumnMetricMySQL]):
        """
               保存字段指标中间表信息
               :param column_metric_infos:
               :return:
               """
        self.session.add_all(column_metric_infos)
    async def get_column_info_by_id(self, column_id: str) -> ColumnInfoMySQL | None:
        """
        根据字段id查询列对象信息
        :param column_id:
        :return:
        参考官网：https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.get
        """
        return await self.session.get(ColumnInfoMySQL, column_id)

    async def get_key_columns_by_table_id(self, table_id: str) -> list[ColumnInfoMySQL]:
        """
        根据表id，查询表的主外键字段
        :param table_id:
        :return:
        参考文档：https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html#selecting-orm-entities
        """
        # 定义sql语句
        sql = """
              select *
              from column_info
              where table_id = :table_id
                 and role in ('primary_key', "foreign_key") 
              """
        # 指定查询返回结果类型
        query = select(ColumnInfoMySQL).from_statement(text(sql))
        # 执行查询
        result = await self.session.execute(query, {'table_id': table_id})
        # 解析返回结果数据
        # scalars()核心含义是自动解包查询结果的元组嵌套，直接提取 ColumnInfoMySQL 模型实例；
        return result.scalars().fetchall()

    async def get_table_info_by_id(self, table_id: str) -> TableInfoMySQL:
        """
        根据表id查询表信息TableInfoMysql
        :param table_id:
        :return:
        """
        return await self.session.get(TableInfoMySQL, table_id)
