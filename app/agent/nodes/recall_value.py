
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


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    召回字段取值：之前是将字段取值存储到了es中，索引根据关键字和条件去es全文检索获取值数据

    :param state:
    :param runtime:
    :return:
    """
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("召回字段取值")
    writer({"stage": "召回字段取值"})
    try:

        # 获取用户查询
        query = state["query"]
        keywords = state["keywords"]
        # 获取字段值es操作对象
        value_es_repository = runtime.context['value_es_repository']
        # 使用大模型扩展关键字
        # 1.使用llm扩展关键字
        # 1.1 构建提示词对象
        prompt = PromptTemplate(template=loader_prompt("extend_keywords_for_value_recall"), input_variables=["query"])
        # 1.2 构建结果转换器
        output_parser = JsonOutputParser()
        # 1.3 构建chain执行链
        chain = prompt | llm | output_parser
        # 1.4 执行chain链对接大模型
        result = await chain.ainvoke({"query": query})

        # 2 使用扩展后的关键字召回字段值
        # 2.1 合并关键字
        keywords = list(set(keywords + result))
        # 定义字典结构数据用户去重
        values_map: dict[str, ValueInfoEs] = {}
        # 2.2 遍历关键字，根据关键字在es中进行召回
        for keyword in keywords:
            # 根据遍历的关键字，查询字段值数据
            values: list[ValueInfoEs] = await value_es_repository.search(keyword,threshold=0)
            # 遍历检索结果
            for value in values:
                # 获取字段值的id
                value_id = value['id']
                # 判断不存在添加
                if value_id not in values_map:
                    # 添加数据到map中
                    values_map[value_id] = value

        # 转换结果为列表结果
        retrieved_values = list(values_map.values())
        logger.info(f"召回字段取值：{list(values_map.keys())}")
        # 返回结果到state中
        return {"retrieved_values": retrieved_values}
    except Exception as e:
        logger.error(f"召回字段取值异常：{str(e)}")
        raise


if __name__ == "__main__":
    from unittest.mock import MagicMock
    from app.repositories.es.value_es_repository import ValueEsRepository
    from app.clients.es_client_manager import es_client_manager


    async def main():
        # 初始化 ES 客户端
        es_client_manager.init()

        # 构造 state
        state = {
            "query": "统计广东省的销售总额",
            "keywords": ["统计", "统计广东省的销售总额", "销售总额", "广东省"],
        }

        # 构造 runtime
        value_repo = ValueEsRepository(es_client_manager.client)
        runtime = MagicMock()
        runtime.stream_writer = lambda x: print(f"进度: {x}")
        runtime.context = {
            "value_es_repository": value_repo,
        }

        # 执行
        result = await recall_value(state, runtime)
        print(f"\n召回到的值: {result['retrieved_values']}")
        print(f"共 {len(result['retrieved_values'])} 条")

        await es_client_manager.close()


    asyncio.run(main())
