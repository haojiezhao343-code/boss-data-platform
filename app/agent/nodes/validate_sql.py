import asyncio
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.es.value_info_es import ValueInfoEs
from app.prompt.prompt_loader import loader_prompt


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("校验SQL")
    writer({"stage": "校验SQL"})
    # 获取生成sql
    sql = state["sql"]
    # 获取数仓操作对象
    dw_mysql_repository = runtime.context["dw_mysql_repository"]
    # 捕获验证sql的异常信息
    try:
        # 执行验证sql
        await dw_mysql_repository.validate_sql(sql)
        # 无异常 None
        logger.info(f"sql验证正确：{sql}")
        return {"error": None}
    except Exception as e:
        logger.info(f"sql验证失败：{sql}")
        return {"error": str(e)}


if __name__ == '__main__':
    from app.clients.mysql_client_manager import dw_mysql_client_manager
    from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository

    async def test():
        # 初始化数仓客户端（创建引擎和会话工厂，需真实可用的数仓连接）
        dw_mysql_client_manager.init()

        async with dw_mysql_client_manager.session_factory() as session:
            # 创建数仓仓储（真实 MySQL 连接）
            dw_mysql_repository = DwMysqlRepository(session=session)

            # 注意：Runtime 是冻结(frozen)dataclass，字段只能在构造时传入，不能事后赋值
            def writer(msg):
                print("writer 输出:", msg)

            runtime = Runtime(
                context={"dw_mysql_repository": dw_mysql_repository},
                stream_writer=writer,
            )

            # ===== 用例1：合法 SQL，应返回 error=None =====
            # state_valid = DataAgentState(sql="select * from fact_order limit 10")
            # result_valid = await validate_sql(state_valid, runtime)
            # print("合法SQL结果:", result_valid)

            # ===== 用例2：非法 SQL（不存在的表），应返回 error=异常信息 =====
            state_invalid = DataAgentState(sql="select * from not_exist_table_xyz")
            result_invalid = await validate_sql(state_invalid, runtime)
            print("非法SQL结果:", result_invalid)

        # 释放连接池资源
        await dw_mysql_client_manager.close()

    asyncio.run(test())

