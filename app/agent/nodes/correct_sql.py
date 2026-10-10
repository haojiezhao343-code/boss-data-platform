import asyncio

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流式输出器
    writer = runtime.stream_writer
    # 输出进度状态信息
    # writer("校正SQL")
    writer({"stage": "校正SQL"})
    try:
        # 获取用户查询
        query = state["query"]
        # 获取表数据列表
        table_infos = state["table_infos"]
        # 获取指标数据列表
        metric_infos = state["metric_infos"]
        # 获取时间信息
        date_info = state["date_info"]
        # 获取数据库信息
        db_info = state["db_info"]
        # 获取sql
        sql = state["sql"]
        # 获取异常信息
        error = state["error"]

        # 创建提示词对象
        prompt = PromptTemplate(template=loader_prompt("correct_sql"),
                                input_variables=["query", "table_infos", "metric_infos", "date_info", "db_info", "sql",
                                                 "error"])
        # 构建json转换对象--> 此处解析必须使用文本解析方式
        output_parser = StrOutputParser()
        # 构建查询链
        chain = prompt | llm | output_parser
        # 执行查询
        result = await chain.ainvoke({
            "query": query,
            "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
            "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
            "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
            "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
            "sql": sql,
            "error": error
        })
        logger.info(f"校验生成的正确sql语句\n：{result}")

        return {"sql": result}
    except Exception as e:
        logger.error(f"校验生成的正确sql语句异常:{str(e)}")
        raise


if __name__ == '__main__':
    async def test():
        # 构造测试状态（包含 correct_sql 需要使用的全部字段）
        state = DataAgentState(
            query="统计华北地区销售总额",
            table_infos=[{
                "name": "sales",
                "role": "事实表",
                "description": "销售事实表",
                "columns": [
                    {"name": "region", "type": "string", "role": "维度",
                     "examples": ["华北", "华东"], "description": "地区", "alias": []},
                    {"name": "amount", "type": "double", "role": "度量",
                     "examples": [], "description": "销售额", "alias": []},
                ],
            }],
            metric_infos=[{
                "name": "销售总额",
                "description": "销售额合计",
                "relevant_columns": ["amount"],
                "alias": ["销售额"],
            }],
            date_info={"date": "2026-08-12", "weekday": "星期三", "quarter": "Q3"},
            db_info={"dialect": "mysql", "version": "8.0"},
            # 一条故意写错的 SQL（引用了不存在的字段）
            sql="select total_amount from sales where region = '华北'",
            # 模拟执行 SQL 时的报错信息
            error="Unknown column 'total_amount' in 'field list'",
        )

        # 收集流式进度输出
        messages = []

        def writer(msg):
            messages.append(msg)
            print("writer 输出:", msg)

        # Runtime 是冻结(frozen)dataclass，字段必须在构造时传入
        runtime = Runtime(stream_writer=writer)

        # 执行节点
        result = await correct_sql(state, runtime)
        print("返回结果:", result)
        print("流式输出:", messages)


    asyncio.run(test())
