from typing import TypedDict
from app.models.es.value_info_es import ValueInfoEs
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository


class ColumnInfoState(TypedDict):
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list[str]



class TableInfoStata(TypedDict):
    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class MetricInfoStata(TypedDict):
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]

class DataAgentState(TypedDict):
    query: str
    keywords: list[str]
    retrieved_columns: list[ColumnInfoQdrant]
    retrieved_metrics: list[MetricInfoQdrant]
    retrieved_values: list[ValueInfoEs]


class DateInfoStata(TypedDict):
    date: str
    weekday: str
    quarter: str


class DBInfoStata(TypedDict):
    dialect: str
    version: str


class DataAgentState(TypedDict):
    query: str  # 用户的查询
    keywords: list[str]  # 提取关键字列表
    retrieved_columns: list[ColumnInfoQdrant]  # 召回列信息列表
    retrieved_values: list[ValueInfoEs]  # 召回值信息列表
    retrieved_metrics: list[MetricInfoQdrant]  # 召回指标信息列表
    table_infos: list[TableInfoStata]  # 封装表信息列表
    metric_infos: list[MetricInfoStata]  # 封装指标信息列表
    date_info: DateInfoStata  # 时间信息
    db_info: DBInfoStata  # 数据库环境信息
    sql: str
    error: str  # 错误信息，根据state中是否存在错误信息，可以进行流程执行


class DateInfoState(TypedDict):
    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):
    version: str
    dialect: str


class DataAgentState(TypedDict):
    """
     图的状态定义（生效版本）
     修复（2026-10-10）：曾被裁剪到只剩 date_info/db_info，LangGraph 按schema过滤
     输入时丢弃 query 等字段，节点内 state["query"] 抛 KeyError('query')。
     现补全为节点实际读写的全部字段。
    """
    query: str  # 用户的查询
    keywords: list[str]  # 提取关键字列表
    retrieved_columns: list  # 召回列信息列表
    retrieved_values: list  # 召回值信息列表
    retrieved_metrics: list  # 召回指标信息列表
    table_infos: list  # 封装表信息列表
    metric_infos: list  # 封装指标信息列表
    date_info: DateInfoState  # 时间信息
    db_info: DBInfoState  # 数据库环境信息
    sql: str  # 生成的SQL
    error: str  # 错误信息，根据state中是否存在错误信息，可以进行流程执行
