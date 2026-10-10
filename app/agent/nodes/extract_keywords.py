import asyncio

import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    await asyncio.sleep(1)
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "提取关键字"})
    try:
        # 获取问题
        query = state["query"]
        # 定义词性
        # 定义返回指定词性的元组
        allow_pos = (
            "n",  # 名词: 数据、服务器、表格
            "nr",  # 人名: 张三、李四
            "ns",  # 地名: 北京、上海
            "nt",  # 机构团体名: 政府、学校、某公司
            "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
            "v",  # 动词: 运行、开发
            "vn",  # 名动词: 工作、研究
            "a",  # 形容词: 美丽、快速
            "an",  # 名形词: 难度、合法性、复杂度
            "eng",  # 英文
            "i",  # 成语
            "l",  # 常用固定短语
        )

        # jieba分词
        keywords: list[str] = jieba.analyse.extract_tags(query, allowPOS=allow_pos)

        # 避免缺失语义，确实关键字
        keywords = list(set(keywords + [query]))

        logger.info(f"提取关键字成功：{keywords}")

        return {"keywords": keywords}
    except Exception as e:
        logger.error(f"提取关键字异常:{str(e)}")
        raise


# ========== 简单测试 ==========
if __name__ == '__main__':
    # 模拟一个简单的 runtime，只需要 stream_writer
    class FakeRuntime:
        def __init__(self):
            pass

        @staticmethod
        def stream_writer(data):
            print(f"[进度] {data}")


    async def test():
        # 测试问题
        test_query = "华北地区上个月销售额最高的商品类别是什么"

        # 构造参数
        state = {"query": test_query}
        runtime = FakeRuntime()

        print(f"输入问题: {test_query}")
        print("-" * 50)

        # 执行函数
        result = await extract_keywords(state, runtime)

        # 打印结果
        print("-" * 50)
        print(f"提取的关键词: {result['keywords']}")
        print(f"关键词数量: {len(result['keywords'])}")


    # 运行测试
    asyncio.run(test())
