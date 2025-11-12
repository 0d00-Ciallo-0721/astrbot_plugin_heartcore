# heartflow/utils/api_utils.py
# (新) v10.3 统一弹性 API 辅助函数
# 职责：为所有模块提供统一的、具有故障切换和重试功能的 LLM 调用

import json
from json.decoder import JSONDecodeError
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.provider import LLMResponse

async def elastic_simple_text_chat(context: Context, provider_names: list[str], prompt: str, system_prompt: str = "") -> str | None:
    """
    (新) 弹性辅助函数 (用于 Bug 8, 9, 12)
    - 实现了模型轮询和故障切换。
    - 适用于返回 *纯文本* 的 LLM 调用。
    """
    if not provider_names:
        return None
    
    last_error = "No models in list"
    unique_provider_names = list(dict.fromkeys(provider_names)) #

    for provider_name in unique_provider_names:
        try:
            provider = context.get_provider_by_id(provider_name) #
            if not provider:
                logger.warning(f"ElasticTextChat: 未找到提供商 {provider_name}，尝试下一个") #
                last_error = f"未找到提供商: {provider_name}"
                continue
            
            llm_response = await provider.text_chat(
                prompt=prompt,
                contexts=[], 
                system_prompt=system_prompt
            ) #
            
            if llm_response and llm_response.completion_text and llm_response.completion_text.strip():
                return llm_response.completion_text.strip() #
            else:
                logger.warning(f"ElasticTextChat: 模型 {provider_name} 返回了空响应，尝试下一个") #
                last_error = f"模型 {provider_name} 返回空响应"
                continue
        
        except Exception as e:
            logger.error(f"ElasticTextChat: 模型 {provider_name} API调用异常: {e}，尝试下一个 Provider") #
            last_error = str(e)
            continue
    
    logger.error(f"ElasticTextChat: 模型列表 {unique_provider_names} 均尝试失败。最后错误: {last_error}") #
    return None

async def elastic_json_chat(context: Context, provider_names: list[str], prompt: str, max_retries: int, system_prompt: str = "") -> dict | None:
    """
    (新) 弹性辅助函数 (用于 Bug 13)
    - 实现了模型轮询、故障切换 和 *JSON解析重试*。
    - 适用于返回 *JSON* 的 LLM 调用。
    """
    if not provider_names:
        return None
        
    last_error = "No models in list"
    unique_provider_names = list(dict.fromkeys(provider_names))

    for provider_name in unique_provider_names:
        try:
            provider = context.get_provider_by_id(provider_name)
            if not provider:
                logger.warning(f"ElasticJsonChat: 未找到提供商 {provider_name}，尝试下一个")
                last_error = f"未找到提供商: {provider_name}"
                continue
        except Exception as e:
             logger.error(f"ElasticJsonChat: 获取提供商 {provider_name} 失败: {e}，尝试下一个")
             last_error = str(e)
             continue 
        
        logger.debug(f"ElasticJsonChat: 正在尝试模型: {provider_name}")

        for attempt in range(max_retries + 1): #
            try:
                llm_response = await provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    system_prompt=system_prompt
                ) #
                
                content = llm_response.completion_text
                if not content or not content.strip():
                    logger.warning(f"ElasticJsonChat: 模型 {provider_name} 返回了空响应 (尝试 {attempt + 1}/{max_retries + 1})") #
                    last_error = f"模型 {provider_name} 返回空响应"
                    if attempt == max_retries:
                        break # 放弃此模型，尝试下一个 Provider
                    continue # 重试 JSON

                content = content.strip()
                
                #
                if content.startswith("```json"): content = content[7:-3].strip()
                elif content.startswith("```"): content = content[3:-3].strip()
                
                data = json.loads(content) #
                return data # 成功！

            except (json.JSONDecodeError, JSONDecodeError) as e: #
                logger.warning(f"ElasticJsonChat: 模型 {provider_name} JSON解析失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}") #
                last_error = f"JSON解析失败: {e}"
                if attempt == max_retries:
                    logger.error(f"ElasticJsonChat: 模型 {provider_name} 重试多次JSON解析失败，放弃此模型") #
                    break # 放弃此模型，尝试下一个 Provider
            
            except Exception as e:
                logger.error(f"ElasticJsonChat: 模型 {provider_name} API调用异常: {e}，尝试下一个 Provider") #
                last_error = str(e)
                break # 放弃此模型，尝试下一个 Provider
    
    logger.error(f"ElasticJsonChat: 模型列表 {unique_provider_names} 均尝试失败。最后错误: {last_error}") #
    return None