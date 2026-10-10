"""
导入工作流调度（供 main.py 与人工审核 API 共用，避免循环导入）
"""
from processor.import_processor.main_graph import KBImportworkflow
from tool.logger import logger
from utils.task_utils import update_task_status, add_done_task


def run_graph_task(task_id: str, file_dir: str, import_file_path: str):
    """后台执行导入工作流（内容审查已在前置阶段完成）"""
    try:
        update_task_status(task_id, "processing")
        init_state = {
            "task_id": task_id,
            "file_dir": file_dir,
            "import_file_path": import_file_path,
        }
        workflow = KBImportworkflow()
        for event in workflow.run(init_state, stream=True):
            for node_name, node_result in event.items():
                add_done_task(task_id, node_name)
        update_task_status(task_id, "completed")
        logger.info(f"[{task_id}] 导入工作流执行完成")
    except Exception as e:
        update_task_status(task_id, "failed")
        logger.info(f"[{task_id}] LangGraph全流程执行失败，异常信息：{str(e)}", exc_info=True)
