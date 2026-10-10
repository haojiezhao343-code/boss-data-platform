# utils/llm_utils.py

from langchain_openai import ChatOpenAI

from config.lm_config import lm_config

_llm_client_cache = {}


def get_llm_client(model: str | None = None, json_mode: bool = False) -> ChatOpenAI:
    """
    获取 LangChain ChatOpenAI 客户端实例
    - model: 允许不同节点使用不同模型
    - json_mode: True 时要求输出 JSON
    """
    m = model or lm_config.llm_model
    key = (m, json_mode)
    if key in _llm_client_cache:
        return _llm_client_cache[key]

    extra_body = {"enable_thinking": False}

    model_kwargs: dict = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}

    client = ChatOpenAI(
        model=m,
        temperature=lm_config.llm_temperature,
        api_key=lm_config.api_key,
        base_url=lm_config.base_url,
        extra_body=extra_body,
        model_kwargs=model_kwargs,
        # 超时保护：单次请求最多等90秒，失败自动重试2次
        # （硅基流动偶发请求挂死，无超时会导致用户无限等待）
        timeout=90,
        max_retries=2,
    )
    _llm_client_cache[key] = client
    return client