# vlm_utils.py 
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import config

# 定义重试策略：最多3次，指数退避（2s, 4s, 8s），仅针对网络异常重试
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True
)

def _ask_with_retry(client, model, messages):
    """将实际的 API 调用抽离出来，专门负责重试"""
    return client.chat.completions.create(
        model=model,
        messages=messages,
    )
    
    
def ask_visual_model(question=None, base64_image=None, mime_type=None, messages=None, api_key=None, model=None):
    """
    调用视觉语言模型，支持单轮或多轮对话。
    参数：
        question (str): 用户问题（与 messages 二选一）
        base64_image (str): 图片的Base64编码（与 messages 二选一）
        mime_type (str): 图片MIME类型（与 messages 二选一）
        messages (list): 完整的对话消息列表（如果提供，则忽略 question/image/mime_type）
        api_key (str): API密钥
        model (str): 模型名称
    返回：
        str: 模型回答
    """
    api_key = api_key or config.DASHSCOPE_API_KEY
    model = model or config.VLM_MODEL

    client = OpenAI(
        api_key=api_key,
        base_url=config.VLM_BASE_URL,
        timeout=15.0  # 显式设置超时时间，防止无限挂起
    )
    

    if messages is None:
        # 单轮模式：构造包含图片和问题的一条消息
        prompt = f"你现在是帮助盲人进行实时语音问答的助手，请用最简洁、最简单的语言回答这个问题，直接给出问题的答案即可：{question}"
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
    # 多轮模式：直接使用传入的 messages（需包含图片和对话历史）

    try:
        completion = _ask_with_retry(client, model, messages)
        return completion.choices[0].message.content
    except Exception as e:
        print(f"[VLM] 经过重试后依然失败: {e}")
        return None