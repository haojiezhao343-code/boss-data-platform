import asyncio
import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from sqlalchemy import true

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TableInfoStata
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("过滤表")
    writer({"stage": "过滤表"})
    try:

        # 获取用户查询
        query = state["query"]
        # 获取合并后的表数据
        table_infos: list[TableInfoStata] = state["table_infos"]

        # 1.使用LLM对组合的表和字段数据根据需求过滤
        # 定义提示词
        prompt = PromptTemplate(template=loader_prompt("filter_table_info"), input_variables=["query", "table_infos"])
        # 定义json格式化输出对象
        output_parser = JsonOutputParser()
        # 组合chain链
        chain = prompt | llm | output_parser
        # 执行chain
        result = await chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False)
            }
        )
        # https://pyyaml.org/wiki/PyYAMLDocumentation
        # print(f"json转换的结果{yaml.dump(table_infos)}")

        """
         {
         "表名1":["字段1", "字段2", "..."],
         "表名2":["字段1", "字段2", "..."]
        }


        """
        # 2.根据大模型返回根据业务相关的表和列调整table_infos
        # 2.1判断过滤掉大模型返回与业务无关的表和字段 浅拷贝
        for table_info in table_infos[:]:
            # 如果不存在过滤的结果，移除表数据
            if table_info["name"] not in result:
                # 移除表数据
                table_infos.remove(table_info)
            else:
                # 如果存在过滤的结果中， 处理表字段
                # 获取返回指定表的字段列表
                selected_columns = result[table_info["name"]]
                # 遍历组合的字段列表  浅拷贝
                for column in table_info["columns"][:]:
                    # 判断是否存在返回的结果中
                    if column["name"] not in selected_columns:
                        table_info["columns"].remove(column)

        logger.info(f"过滤后的表信息{[table_info["name"] for table_info in table_infos]}")
        # 结果不符合要求（5。如果选择了多张表，需要确保所选字段包含join所需的主外键）
        return {"table_infos": table_infos}
    except Exception as e:
        logger.error(f"过滤后的表信息异常：{str(e)}")
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
        test_query = "统计华北地区上个月的销售额"

        # 构造合并后的表数据（模拟 merge_retrieved_info 节点的输出结构 TableInfoStata）
        table_infos = [
            {
                "name": "sales_order",
                "role": "事实表",
                "description": "销售订单明细表，记录每笔销售订单",
                "columns": [
                    {"name": "order_id", "type": "varchar(32)", "role": "主键",
                     "examples": ["SO20260701001"], "description": "订单编号", "alias": ["订单号"]},
                    {"name": "region", "type": "varchar(32)", "role": "维度",
                     "examples": ["华北", "华东", "华南"], "description": "销售大区", "alias": ["大区", "地区"]},
                    {"name": "sales_amount", "type": "decimal(18,2)", "role": "度量",
                     "examples": ["1200.50"], "description": "销售金额", "alias": ["销售额", "销售总额"]},
                    {"name": "order_date", "type": "date", "role": "维度",
                     "examples": ["2026-07-01"], "description": "订单日期", "alias": ["下单时间"]},
                    {"name": "remark", "type": "varchar(255)", "role": "维度",
                     "examples": ["加急"], "description": "备注信息", "alias": []},
                ],
            },
            {
                "name": "employee_info",
                "role": "维度表",
                "description": "员工信息表，与本次销售额统计无关，用于验证整表被过滤",
                "columns": [
                    {"name": "emp_id", "type": "varchar(16)", "role": "主键",
                     "examples": ["E001"], "description": "员工编号", "alias": ["工号"]},
                    {"name": "emp_name", "type": "varchar(64)", "role": "维度",
                     "examples": ["张三"], "description": "员工姓名", "alias": ["姓名"]},
                ],
            },
        ]

        state = {"query": test_query, "table_infos": table_infos}
        runtime = FakeRuntime()

        print(f"输入问题: {test_query}")
        print("-" * 50)

        # 执行节点（会真实调用 LLM）
        result = await filter_table(state, runtime)

        print("-" * 50)
        print(f"过滤后保留的表数量: {len(result['table_infos'])}")
        for table in result["table_infos"]:
            print(f"表: {table['name']} -> 保留字段: {[c['name'] for c in table['columns']]}")


    # 运行测试
    asyncio.run(test())