import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant
from app.prompt.prompt_loader import loader_prompt


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("召回指标数据")
    writer({"stage": "召回指标数据"})
    try:

        # 获取用户查询
        query = state['query']
        # 获取关键字列表
        keywords = state["keywords"]
        # 获取向量服务客户端
        embedding_client = runtime.context["embedding_client"]
        # 获取指标向量操作对象
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]

        # 1.使用llm扩展关键字
        # 1.1 构建提示词对象
        prompt = PromptTemplate(template=loader_prompt("extend_keywords_for_metric_recall"), input_variables=["query"])
        # 1.2 构建结果转换器
        output_parser = JsonOutputParser()
        # 1.3 构建chain执行链
        chain = prompt | llm | output_parser
        # 1.4 执行chain链对接大模型
        result = await chain.ainvoke({"query": query})

        # 2.使用扩展的关键字进行召回字段信息
        # 2.1 根据提取的关键字和适用llm扩张的关键字合并后去重
        keywords = set(keywords + result)
        # 2.2 实现指标信息召回
        # 定义字典收集召回的指标信息
        retrieved_metric_map: dict[str, MetricInfoQdrant] = {}
        # 变量所有关键
        for keyword in keywords:
            # 使用向量服务客户端对象关键字进行向量化
            embedding = await embedding_client.aembed_query(keyword)

            # 使用指标向量库操作对象查询payload列表
            payloads: list[MetricInfoQdrant] = await metric_qdrant_repository.search(embedding)
            # 遍历指标信息列表
            for payload in payloads:
                # 获取指标id,用于存储时去重
                metric_id = payload["id"]
                # 判断是否已经存在收集的字典中
                if metric_id not in retrieved_metric_map:
                    retrieved_metric_map[metric_id] = payload

        # 将最终去重后，召回的字段信息转换成列表返回
        retrieved_metrics = list(retrieved_metric_map.values())
        logger.info(f"召回指标信息:{list(retrieved_metric_map.keys())}")
        # 存储到state中

        return {"retrieved_metrics": retrieved_metrics}
    except Exception as e:
        logger.error(f"召回指标信息异常：{str(e)}")
        raise


if __name__ == "__main__":
    from unittest.mock import MagicMock
    from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.qdrant_client_manager import qdrant_client_manager


    async def main():
        # 初始化客户端
        embedding_client_manager.init()
        qdrant_client_manager.init()

        # 构造 state
        state = {
            "query": "统计华北地区销售总额",
            "keywords": ["统计", "统计华北地区销售总额", "销售总额", "华北地区"],
        }

        # 构造 runtime
        metric_repo = MetricQdrantRepository(qdrant_client_manager.client)
        runtime = MagicMock()
        runtime.stream_writer = lambda x: print(f"进度: {x}")
        runtime.context = {
            "embedding_client": embedding_client_manager.client,
            "metric_qdrant_repository": metric_repo,
        }

        # 执行
        result = await recall_metric(state, runtime)
        print(f"\n召回指标: {result['retrieved_metrics']}")
        print(f"共 {len(result['retrieved_metrics'])} 个指标")

        await qdrant_client_manager.close()


    asyncio.run(main())
