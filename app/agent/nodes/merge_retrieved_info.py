import asyncio
from os import name
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, TableInfoStata, MetricInfoStata, ColumnInfoState, ValueInfoEs
from app.core.log import logger
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant


async def merge_retrieved_info(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("合并召回信息")
    writer({"stage": "合并召回信息"})
    try:
        # 封装表信息列表
        table_infos: list[TableInfoStata] = []
        # 封装指标信息列表
        metric_infos: list[MetricInfoStata] = []
        # 获取召回的字段列表
        retrieved_columns: list[ColumnInfoQdrant] = state["retrieved_columns"]
        # 获取召回的值列表
        retrieved_values: list[ValueInfoEs] = state["retrieved_values"]
        # 获取召回的指标信息
        retrieved_metrics: list[MetricInfoQdrant] = state["retrieved_metrics"]

        # 获取查询字段信息的mysql客户端对象
        meta_mysql_repository = runtime.context["meta_mysql_repository"]
        # 调整数据结构为字典，目的就是去重
        retrieved_columns_map: dict[str, ColumnInfoQdrant] = {retrieved_column["id"]: retrieved_column for
                                                              retrieved_column in retrieved_columns}

        # 1.处理召回的指标信息，将指标信息的相关字段加入到字段列表
        for retrieved_metric in retrieved_metrics:
            # 获取指标对应的字段id列表
            relevant_columns = retrieved_metric["relevant_columns"]
            # 遍历字段id列表
            for relevant_column in relevant_columns:
                # 判断列信息列表中是否已经在该字段
                if relevant_column not in retrieved_columns_map:
                    # 根据字段id查询字段信息
                    column_info_mysql: ColumnInfoMySQL = await meta_mysql_repository.get_column_info_by_id(
                        relevant_column)
                    # 存储数据到retrieved_columns_map
                    retrieved_columns_map[relevant_column] = convert_column_info_from_mysql_to_qdrant(column_info_mysql)

        # 2.处理召回字段值列表，将值加入到列数据的example列表中
        # 遍历值列表
        for retrieved_value in retrieved_values:
            # 获取值
            column_value = retrieved_value["value"]
            # 获取列id
            column_id = retrieved_value["column_id"]
            # 如果值对应的列不存在，直接查询列存储到列map中
            if column_id not in retrieved_columns_map:
                # 当前对应的值所属的字段不在召回的字段列表中
                column_info_mysql = await meta_mysql_repository.get_column_info_by_id(column_id)
                # 加入到列信息列表中
                retrieved_columns_map[column_id] = convert_column_info_from_mysql_to_qdrant(column_info_mysql)
            # 如果列对应的example 中不存在当前值，直接存储
            if column_value not in retrieved_columns_map[column_id]['examples']:
                # 当前值不存在example列表,添加到列表中
                retrieved_columns_map[column_id]['examples'].append(column_value)

        # 3.将字段信息列表转换为表信息列表
        # 3.1 将字段信息分组，实现以表为单位进行归类 结构dict {table_id,list[ColumnInfoQdrant]}
        # 定义存储分组容器
        table_to_column_map: dict[str, list[ColumnInfoQdrant]] = {}
        # 遍历字段信息存储字典容器
        for column in retrieved_columns_map.values():
            # 从字段信息对象中获取表id
            table_id = column["table_id"]
            # 如果指定的表id对应的数据不存在进行初始化
            if table_id not in table_to_column_map:
                table_to_column_map[table_id] = []
            # 存储遍历的字段信息到分组容器中
            table_to_column_map[table_id].append(column)

        # 显示的添加每个表的主外键字段
        for table_id in table_to_column_map.keys():
            # 根据表id，查询表的主外键字段
            key_columns: list[ColumnInfoMySQL] = await meta_mysql_repository.get_key_columns_by_table_id(table_id)
            # 获取当前table_id 对应字段id的列表
            column_ids = [column["id"] for column in table_to_column_map[table_id]]
            # 遍历查询的主外键列表
            for key_column in key_columns:
                # 判断当前主外键列是否存在当前表对应的字段列表中
                if key_column.id not in column_ids:
                    # 将当前表对应的主外键字段添加到字段列表中
                    table_to_column_map[table_id].append(convert_column_info_from_mysql_to_qdrant(key_column))

        # 3.2 table_id-->columns 转换为list[TableInfoStata]
        # 遍历字段分组容器
        for table_id, columns in table_to_column_map.items():
            # 根据表id查询表信息TableInfoMysql
            table: TableInfoMySQL = await meta_mysql_repository.get_table_info_by_id(table_id)
            # 转换字段数据 ColumnInfoQdrant-ColumnInfoStata
            columns = [convert_column_info_from_qdrant_to_stata(column) for column in columns]
            # 构建tableInfoStata
            table_info = TableInfoStata(
                name=table.name,
                role=table.role,
                description=table.description,
                columns=columns
            )
            # 收集组装的表数据
            table_infos.append(table_info)

        # 处理指标信息
        metric_infos = [convert_metric_info_from_qdrant_to_stata(retrieved_metric) for retrieved_metric in
                        retrieved_metrics]
        # 返回封装数据到后面节点
        return {"table_infos": table_infos, "metric_infos": metric_infos}

    except Exception as e:
        logger.error(f"合并召回信息异常:{str(e)}")
        raise


def convert_metric_info_from_qdrant_to_stata(metric_info_qdrant: MetricInfoQdrant) -> MetricInfoStata:
    """
    转换指标信息MetricInfoQdrant 为MetricInfoStata
    :param metric_info_qdrant:
    :return:
    """
    return MetricInfoStata(
        name=metric_info_qdrant['name'],
        description=metric_info_qdrant['description'],
        relevant_columns=metric_info_qdrant['relevant_columns'],
        alias=metric_info_qdrant['alias']

    )


def convert_column_info_from_qdrant_to_stata(column_info_qdrant: ColumnInfoQdrant) -> ColumnInfoState:
    """
    转化字段信息ColumnInfoQdrant 为ColumnInfoState
    :param column_info_qdrant:
    :return:
    """
    return ColumnInfoState(
        name=column_info_qdrant['name'],
        type=column_info_qdrant['type'],
        role=column_info_qdrant['role'],
        examples=column_info_qdrant['examples'],
        description=column_info_qdrant["description"],
        alias=column_info_qdrant['alias']

    )


# 定义转换类型的函数
def convert_column_info_from_mysql_to_qdrant(column_info_mysql: ColumnInfoMySQL) -> ColumnInfoQdrant:
    """
    转化字段信息ColumnInfoMySQL为ColumnInfoQdrant
    :param column_info_mysql:
    :return:
    """
    return ColumnInfoQdrant(
        id=column_info_mysql.id,
        name=column_info_mysql.name,
        type=column_info_mysql.type,
        role=column_info_mysql.role,
        examples=column_info_mysql.examples,
        description=column_info_mysql.description,
        alias=column_info_mysql.alias,
        table_id=column_info_mysql.table_id
    )


if __name__ == "__main__":
    from unittest.mock import MagicMock

    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.qdrant_client_manager import qdrant_client_manager
    from app.clients.es_client_manager import es_client_manager
    from app.clients.mysql_client_manager import meta_mysql_client_manager

    from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
    from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
    from app.repositories.es.value_es_repository import ValueEsRepository
    from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository

    from app.agent.nodes.recall_column import recall_column
    from app.agent.nodes.recall_metric import recall_metric
    from app.agent.nodes.recall_value import recall_value


    async def main():
        # ========== 1. 初始化所有客户端 ==========
        embedding_client_manager.init()
        qdrant_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()

        query = "统计广东省销售总额"
        keywords = ["统计", "统计广东省销售总额", "销售总额", "广东省"]
        state = {"query": query, "keywords": keywords}

        # ========== 2. 三路召回（真实数据） ==========
        # 字段召回（真实 Qdrant）
        column_repo = ColumnQdrantRepository(qdrant_client_manager.client)
        runtime_col = MagicMock()
        runtime_col.stream_writer = lambda x: print(f"[字段召回] {x}")
        runtime_col.context = {
            "embedding_client": embedding_client_manager.client,
            "column_qdrant_repository": column_repo,
        }
        col_result = await recall_column(state, runtime_col)
        print(f"字段召回: {len(col_result['retrieved_columns'])} 个")
        for c in col_result["retrieved_columns"]:
            print(f"  - {c['id']} ({c['name']}, {c['role']})")

        # 指标召回（真实 Qdrant）
        metric_repo = MetricQdrantRepository(qdrant_client_manager.client)
        runtime_metric = MagicMock()
        runtime_metric.stream_writer = lambda x: print(f"[指标召回] {x}")
        runtime_metric.context = {
            "embedding_client": embedding_client_manager.client,
            "metric_qdrant_repository": metric_repo,
        }
        metric_result = await recall_metric(state, runtime_metric)
        print(f"\n指标召回: {len(metric_result['retrieved_metrics'])} 个")
        for m in metric_result["retrieved_metrics"]:
            print(f"  - {m['id']} ({m['name']}): relevant_columns={m['relevant_columns']}")

        # 取值召回（真实 ES）
        value_repo = ValueEsRepository(es_client_manager.client)
        runtime_val = MagicMock()
        runtime_val.stream_writer = lambda x: print(f"[取值召回] {x}")
        runtime_val.context = {"value_es_repository": value_repo}
        val_result = await recall_value(state, runtime_val)
        print(f"\n取值召回: {len(val_result['retrieved_values'])} 条")
        for v in val_result["retrieved_values"]:
            print(f"  - {v['id']} → value={v['value']}")

        # ========== 3. 合并召回信息（真实 meta MySQL） ==========
        async with meta_mysql_client_manager.session_factory() as session:
            repo = MetaMysqlRepository(session=session)
            merge_state = {
                "query": query,
                "retrieved_columns": col_result["retrieved_columns"],
                "retrieved_metrics": metric_result["retrieved_metrics"],
                "retrieved_values": val_result["retrieved_values"],
            }
            runtime_merge = MagicMock()
            runtime_merge.stream_writer = lambda x: print(f"[合并] {x}")
            runtime_merge.context = {"meta_mysql_repository": repo}

            result = await merge_retrieved_info(merge_state, runtime_merge)

            # ========== 4. 打印结果 ==========
            print(f"\n========== 合并结果 ==========")
            for t in result["table_infos"]:
                print(f"\n表 [{t['role']}] {t['name']} — {t['description']}")
                for c in t["columns"]:
                    print(f"  · {c['name']} ({c['role']}): {c['description']}, examples={c['examples']}")

            print(f"\n指标: {[m['name'] for m in result['metric_infos']]}")

        # ========== 5. 释放资源 ==========
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()


    asyncio.run(main())
