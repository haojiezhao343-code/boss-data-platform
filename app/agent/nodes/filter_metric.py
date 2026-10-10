import asyncio

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, MetricInfoStata
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def filter_metric(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("过滤指标")
    writer({"stage": "过滤指标"})
    try:
        # 获取用户查询
        query = state["query"]
        # 获取合并后的指标数据
        metric_infos: list[MetricInfoStata] = state["metric_infos"]

        # 1.使用LLM对指标数据根据需求过滤
        # 定义提示词
        prompt = PromptTemplate(template=loader_prompt("filter_metric_info"), input_variables=["query", "metric_infos"])
        # 定义json格式化输出对象
        output_parser = JsonOutputParser()
        # 组合chain链
        chain = prompt | llm | output_parser
        # 执行chain
        result = await chain.ainvoke({"query": query, "metric_infos": yaml.dump(metric_infos,allow_unicode=True,sort_keys=False)})
        print(f"json转换的结果{yaml.dump(metric_infos,allow_unicode=True,sort_keys=False)}")

        """
        [
             "转正人数",
             "转正率"
        ]
        """
        # 2.根据大模型返回根据业务相关的指标，调整metric_infos
        # 2.1判断过滤掉大模型返回与业务无关的指标 浅拷贝
        for metric_info in metric_infos[:]:
            # 如果不存在过滤的结果，移除表数据
            if metric_info["name"] not in result:
                # 移除表数据
                metric_infos.remove(metric_info)


        logger.info(f"过滤后的表信息{[metric_info["name"] for metric_info in metric_infos]}")
        # 结果不符合要求（5。如果选择了多张表，需要确保所选字段包含join所需的主外键）
        return {"metric_infos": metric_infos}
    except Exception as e:
        logger.error(f"过滤后的表信息异常{str(e)}")
        raise

# ========== 简单测试 ==========
if __name__ == '__main__':
    # 模拟一个简单的 runtime，只需要 stream_writer
    class FakeRuntime:
        @staticmethod
        def stream_writer(data):
            print(f"[进度] {data}")


    async def test():
        # 测试问题
        test_query = "统计广东省上个月的销售总额"

        # 构造合并后的指标数据（模拟 merge_retrieved_info 节点的 metric_infos 输出结构 MetricInfoStata）
        metric_infos = [
            {
                "name": "GMV",
                "description": "全称Gross Merchandise Value，表示所有订单的成交金额总和。",
                "relevant_columns": ["fact_order.order_amount"],
                "alias": ["成交总额", "订单总额", "总销售额"],
            },
            {
                "name": "AOV",
                "description": "全称Average Order Value，表示所有订单的成交金额平均值。",
                "relevant_columns": ["fact_order.order_quantity", "fact_order.order_amount"],
                "alias": ["平均单价", "平均订单金额"],
            },
            {
                "name": "销量",
                "description": "所有订单中商品的购买数量总和。",
                "relevant_columns": ["fact_order.order_quantity"],
                "alias": ["销售数量", "总销量"],
            },
        ]

        state = {"query": test_query, "metric_infos": metric_infos}
        runtime = FakeRuntime()

        print(f"输入问题: {test_query}")
        print("-" * 50)

        # 执行节点（会真实调用 LLM）
        result = await filter_metric(state, runtime)

        print("-" * 50)
        print(f"过滤后保留的指标数量: {len(result['metric_infos'])}")
        for metric in result["metric_infos"]:
            print(f"指标: {metric['name']} -> {metric['description']}")


    # 运行测试
    asyncio.run(test())