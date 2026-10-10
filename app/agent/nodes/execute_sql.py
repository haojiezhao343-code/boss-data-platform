import asyncio

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("执行SQL")
    writer({"stage": "执行SQL"})
    try:

        # 获取sql
        sql = state["sql"]
        # 获取数仓操作对象
        dw_mysql_repository = runtime.context["dw_mysql_repository"]
        # 执行sql
        result = await dw_mysql_repository.execute_sql(sql)

        # 输出执行结果
        logger.info(f"执行sql的结果{result}")
        # 解析处理结果
        writer({"result": result})
    except Exception as e:
        logger.error(f"执行sql的结果异常：{str(e)}")
        raise


if __name__ == '__main__':
    from app.clients.mysql_client_manager import dw_mysql_client_manager
    from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository


    async def test():
        # 初始化数仓客户端（创建引擎和会话工厂，需真实可用的数仓连接）
        dw_mysql_client_manager.init()

        async with dw_mysql_client_manager.session_factory() as session:
            # 创建数仓仓储（真实 MySQL 连接）
            dw_mysql_repository = DwMysqlRepository(session=session)

            # 收集流式进度输出
            messages = []

            def writer(msg):
                messages.append(msg)
                print("writer 输出:", msg)

            # Runtime 是冻结(frozen)dataclass，字段必须在构造时传入
            runtime = Runtime(
                context={"dw_mysql_repository": dw_mysql_repository},
                stream_writer=writer,
            )

            # 构造 state（该节点只读取 sql 字段）
            state = DataAgentState(sql="select * from fact_order limit 10")

            # 执行节点
            result = await execute_sql(state, runtime)
            print("节点返回值:", result)  # 预期为 None，实际结果在 writer 输出中
            print("流式输出:", messages)

        # 释放连接池资源
        await dw_mysql_client_manager.close()


    asyncio.run(test())
