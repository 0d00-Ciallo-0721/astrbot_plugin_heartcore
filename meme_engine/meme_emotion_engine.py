# heartflow/meme_engine/meme_emotion_engine.py
# (v4.0 重构 - 迁移 v3.5 版本)
# (BUG 9/13 统一重构 - 导入 api_utils)
import json
from astrbot.api import logger
from astrbot.api.star import Context # 导入 Context 类型注解

# --- (BUG 9/13 重构) ---
from ..utils.api_utils import elastic_simple_text_chat

async def get_emotion_from_text(
    context: Context,                   # 传入 AstrBot 上下文
    provider_names: list[str],          # (BUG 9 修复) 心情判断模型 *列表*
    emotion_mapping: dict,              # 解析后的表情 tag -> desc 映射
    emotion_mapping_string: str,        # 格式化后的表情描述字符串
    text_output: str                    # LLM 回复的文本内容
) -> str:
    """
    (BUG 9/13 重构) 使用配置的“心情模型”分析文本并返回一个表情标签。
    """
    # 检查前置条件
    if not provider_names: # (BUG 9 修复) 检查列表
        return "none" #

    if not emotion_mapping:
        return "none" #

    if not text_output or len(text_output.strip()) < 5: # 文本太短可能无法判断
        logger.debug("表情引擎：回复文本过短，跳过心情分析。") #
        return "none" #

    try:
        # 1. 构建专属的判断 Prompt (不变)
        emotion_prompt = f"""
你的任务是分析以下[待分析文本]，并从[可用心情列表]中选择一个最能代表该文本情绪的标签。

[可用心情列表]
{emotion_mapping_string}

[规则]
- 你的回复**必须**仅仅是列表中的一个心情标签（例如："happy" 或 "sad"）。
- 如果文本情绪非常平淡、中性，或者没有强烈的对应关系，请**必须**回复 "none"。
- 不要添加任何解释或多余的文字。

[待分析文本]
{text_output}

[你的心情标签]""" #

        # 2. (BUG 9/13 重构) 调用统一的弹性 Helper
        emotion_tag_raw = await elastic_simple_text_chat(
            context,
            provider_names,
            emotion_prompt
        )

        # 3. (BUG 9 修复) 处理弹性 Helper 的结果
        if not emotion_tag_raw:
            logger.error(f"表情引擎：弹性调用列表 {provider_names} 均失败")
            return "none"

        emotion_tag = emotion_tag_raw.strip().lower() #

        # 4. 验证输出 (不变)
        if emotion_tag in emotion_mapping: # 检查返回的标签是否在配置的 key 中
            logger.info(f"表情引擎：心情判断模型输出: {emotion_tag}") #
            return emotion_tag #
        else:
            if emotion_tag != "none":
                logger.warning(f"表情引擎：心情判断模型输出了无效或非预期的标签: '{emotion_tag}'，已重置为 'none'") #
            else:
                 logger.debug("表情引擎：心情判断模型输出: none (情绪平淡或无匹配)") #
            return "none" #

    except Exception as e:
        logger.error(f"表情引擎：(外层) 调用失败: {e}") #
        import traceback
        logger.error(traceback.format_exc()) #
        return "none" #

# --- (BUG 9/13 重构) 移除 _attempt_elastic_emotion_chat ---