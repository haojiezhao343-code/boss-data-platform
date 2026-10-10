"""
导入功能的主流程图
"""
import logging

from langgraph.constants import END
from langgraph.graph import StateGraph

from processor.import_processor.base import setup_logging
from processor.import_processor.nodes.node_bge_embedding import NodeBGEEmbedding
from processor.import_processor.nodes.node_document_split import NodeDocumentSplit
from processor.import_processor.nodes.node_entry import NodeEntry
from processor.import_processor.nodes.node_import_milvus import NodeImportMilvus
from processor.import_processor.nodes.node_item_name_recognition import NodeItemNameRecognition
from processor.import_processor.nodes.node_md_img import NodeMDImg
from processor.import_processor.nodes.node_pdf_to_md import NodePdfToMd
from processor.import_processor.state import ImportGraphState


class KBImportworkflow:
    def __init__(self):
        """初始化工作流对象"""
        self._compiled_graph = None

    @property
    def graph(self):
        """
        延迟加载（懒加载）：只在第一次使用工作流对象的时候编译图
        """
        if self._compiled_graph is None:
            # 如果还没生成过编译图，就现场生成一张存起来
            self._compiled_graph = self._build_graph()
        return self._compiled_graph

    def _build_graph(self):
        # 1.初始化图（工作流）
        graph = StateGraph(ImportGraphState)
        # 2.在图中注册节点
        graph.add_node("node_entry", NodeEntry())          # 入口：判断文档类型
        graph.add_node("node_pdf_to_md", NodePdfToMd())    # pdf转换为markdown
        graph.add_node("node_md_img", NodeMDImg())         # 解析文档中的图片，并嵌入文档
        graph.add_node("node_document_split", NodeDocumentSplit())  # 文档切分
        graph.add_node("node_item_name_recognition", NodeItemNameRecognition()) # 识别商品名称
        graph.add_node("node_bge_embedding", NodeBGEEmbedding()) # 文本转向量
        graph.add_node("node_import_milvus", NodeImportMilvus()) # 向量存入milvus

        # 3. 设置入口节点
        graph.set_entry_point("node_entry")

        # 4. 注册条件边
        graph.add_conditional_edges(
            "node_entry",
            self.route_after_entry,
            {
                "node_pdf_to_md": "node_pdf_to_md",
                "node_md_img": "node_md_img",
                END: END
            }
        )

        # 5.注册顺序边
        graph.add_edge("node_pdf_to_md", "node_md_img")
        graph.add_edge("node_md_img", "node_document_split")
        graph.add_edge("node_document_split", "node_item_name_recognition")
        graph.add_edge("node_item_name_recognition", "node_bge_embedding")
        graph.add_edge("node_bge_embedding", "node_import_milvus")
        graph.add_edge("node_import_milvus", END)

        # 6. 编译工作流并返回
        return graph.compile()

    @staticmethod
    def route_after_entry(state: ImportGraphState) -> str:
        """
        入口路由判断：
        如果开启读取PDF，走PDF转MD；
        如果开启MD读取，走MD图片解析；
        都没开启直接结束流程
        """
        if state.get("is_pdf_read_enabled"):
            return "node_pdf_to_md"
        elif state.get("is_md_read_enabled"):
            return "node_md_img"
        else:
            return END

    def run(self, state: ImportGraphState, stream: bool = False):
        """
        执行工作流
        stream=True：分步流式返回中间结果
        stream=False：一次性跑完返回最终结果
        """
        if stream:
            # return self.graph.stream(state, stream_mode="values")
            return self.graph.stream(state)

        else:

            return self.graph.invoke(state)


if __name__ == "__main__":
    # 激活全局日志
    setup_logging()

    init_state = {"import_file_path": "一个文件.pdf",
                  "file_dir":r"F:\output"
                  }
    workflow = KBImportworkflow()
    # 打印编译后的流程图对象
    logging.getLogger().info(f"编译完成的工作流图：{workflow.graph}")
    # 执行工作流，拿到最终状态
    final_state = workflow.run(init_state)
    logging.getLogger().info(f"流程执行结果：{final_state}")