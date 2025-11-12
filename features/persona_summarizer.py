# heartflow/features/persona_summarizer.py
# (v10.12 修复 - 根据用户请求，从 dynamic_style_guide 中移除 energy 和 tier)
import json
import asyncio # <--- 导入 asyncio
from astrbot.api import logger
from astrbot.api.star import Context

# (使用相对路径导入 v4.0 模块)
from ..config import HeartflowConfig
from ..persistence import PersistenceManager
# --- (v10.5 修复) Task 不在 typing 中 ---
from typing import TYPE_CHECKING, Dict, Any 

# --- (BUG 13 重构) ---
from ..utils.api_utils import elastic_json_chat

# (v10.0) 循环依赖
if TYPE_CHECKING:
    from ..utils.prompt_builder import PromptBuilder 

class PersonaSummarizer:
    """
    (新) v4.0 人格摘要管理器
    职责：负责管理和生成人格摘要，并处理缓存
    来源：迁移自 decision_engine.py
    """
    
    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 persistence: PersistenceManager,
                 prompt_builder: "PromptBuilder" # (v6.2) 仅保留类型提示
                 ):
        self.context = context
        self.config = config
        self.persistence = persistence
        self.cache = self.persistence.load_persona_cache() #
        
        # --- (v10.3 修复) ---
        # 字典：用于存储正在进行的摘要任务
        # (v10.5 修复) 类型提示现在使用 asyncio.Task
        self.pending_summaries: Dict[str, asyncio.Task[str]] = {} 
        # 锁：用于保护对 pending_summaries 字典的并发访问
        self._lock = asyncio.Lock()
        # --- (修复结束) ---

    async def _internal_create_summary(self, umo: str, persona_key_for_cache: str, original_prompt: str) -> str:
        """
        (v10.3 新增) 内部函数，实际执行摘要生成和缓存。
        此函数由 get_or_create_summary 中的锁机制确保只被调用一次。
        """
        try:
            # --- 3. 检查是否需要总结 (v10.0 逻辑) ---
            if not original_prompt or len(original_prompt.strip()) < 50:
                logger.debug(f"Persona {persona_key_for_cache} 无需总结 (过短或为空)。")
                self.cache[persona_key_for_cache] = {
                    "original": original_prompt,
                    "summarized": original_prompt, # 摘要=原始
                    "dynamic_style_guide": ""    # v10.0: 存一个空字符串
                }
                self.save_cache() # (v5) 立即保存
                return original_prompt
            
            # --- ！！！ v10.9 修复：优化日志 ！！！ ---
            logger.info(f"Persona {persona_key_for_cache} 缓存未命中或已失效（Persona已更改），正在重新生成摘要...")
            # --- 修复结束 ---
            
            # ！！！ v10.0 修复：现在返回 (summarized, style_guide)
            summarized_prompt, dynamic_style_guide = await self._summarize_system_prompt(original_prompt)
            
            # ！！！ v10.1 修复：如果摘要失败，不要污染缓存 ！！！
            if summarized_prompt == original_prompt or not dynamic_style_guide:
                 logger.error(f"Persona {persona_key_for_cache} 摘要失败，返回原始 Prompt，*不*更新缓存。")
                 return original_prompt # 返回原始 prompt，但不保存
            
            # --- 4. 更新内存缓存 ---
            self.cache[persona_key_for_cache] = {
                "original": original_prompt,
                "summarized": summarized_prompt,
                "dynamic_style_guide": dynamic_style_guide
            }
            
            self.save_cache() # (v5) 立即保存
            
            logger.info(f"创建新的精简系统提示词 (Persona Key: {persona_key_for_cache}) | 原长度:{len(original_prompt)} -> 新长度:{len(summarized_prompt)}")
            
            return summarized_prompt
            
        except Exception as e:
            logger.error(f"获取精简系统提示词失败 (Internal): {e}")
            import traceback
            logger.error(traceback.format_exc())
            # (v5 修复) 即使摘要失败，也返回完整人设
            return original_prompt
        finally:
            # --- (v10.3 修复) ---
            # 无论成功或失败，都从“正在进行”的字典中移除此任务
            async with self._lock:
                self.pending_summaries.pop(persona_key_for_cache, None)
                logger.debug(f"摘要任务 {persona_key_for_cache} 已完成，已从 pending 队列移除。")
            # --- (修复结束) ---

    async def get_or_create_summary(self, 
                                    umo: str, 
                                    persona_id: str,      # (v6.2 修复)
                                    original_prompt: str
                                    ) -> str:
        """
        (v10.10 修复) 获取或创建人格缓存 (摘要 + 动态风格)
        职责：检查缓存，如果(ID+Original)不匹配，则调用摘要模型并保存
        返回：(str) summarized_persona (用于判断模型)
        """
        try:
            persona_key_for_cache = persona_id 
            
            # --- 1. 检查 *已完成* 缓存 (Fast Path) ---
            cached_data = self.cache.get(persona_key_for_cache)

            # --- ！！！ v10.10 修复：移除 'original_prompt' 检查 ！！！ ---
            # 只要缓存存在，且包含摘要和风格，就视为命中
            if (cached_data and 
                cached_data.get("summarized") and                   
                cached_data.get("dynamic_style_guide") is not None): # (is not None 允许空字符串 "" 被视为有效)         
                
                logger.debug(f"使用缓存的精简系统提示词 (Persona Key: {persona_key_for_cache})")
                return cached_data.get("summarized") # 返回摘要
            # --- 修复结束 ---

            # --- 2. (v10.3 修复) 检查 *正在进行* 的任务 (Locking Path) ---
            # (如果 Fast Path 缓存未命中，则进入此逻辑)
            
            # 在检查/添加 self.pending_summaries 字典时必须加锁
            async with self._lock:
                pending_task = self.pending_summaries.get(persona_key_for_cache)
                
                if pending_task:
                    # --- 2a. 任务已在进行 ---
                    logger.debug(f"摘要任务 {persona_key_for_cache} 已在进行中，等待其完成...")
                    # 锁会在 with 语句块结束时自动释放
                else:
                    # --- 2b. 此请求是第一个 ---
                    # (v10.9) 日志已移动到 _internal_create_summary
                    logger.debug(f"摘要任务 {persona_key_for_cache} 未在进行，创建新任务...")
                    # 创建任务，但 *不* await 它
                    pending_task = asyncio.create_task(
                        self._internal_create_summary(umo, persona_key_for_cache, original_prompt)
                    )
                    # 将任务存入字典
                    self.pending_summaries[persona_key_for_cache] = pending_task
                    # 锁会在 with 语句块结束时自动释放
            
            # --- 3. (v10.3) 等待任务完成 ---
            # (无论我们是“找到”了任务还是“创建”了任务，都在 *锁外* 等待它)
            summarized_result = await pending_task
            return summarized_result

        except Exception as e:
            logger.error(f"获取精简系统提示词失败 (Outer): {e}")
            import traceback
            logger.error(traceback.format_exc())
            # (v5 修复) 即使摘要失败，也返回完整人设，而不是空
            return original_prompt
            
    def get_cached_style_guide(self, persona_key: str) -> str:
        """
        (v10.0 新增) 从缓存中获取动态风格指南
        """
        if not persona_key:
            return None
        cached_data = self.cache.get(persona_key)
        if cached_data:
            return cached_data.get("dynamic_style_guide") # 可能返回 None 或空字符串
        return None

    async def _summarize_system_prompt(self, original_prompt: str) -> (str, str):
        """
        (v10.12 修复) 使用小模型对系统提示词进行总结
        (移除了 energy 和 tier 的要求)
        """
        try:
            # --- ！！！(BUG 13 重构) 构建弹性模型列表！！！ ---
            providers_to_try = []
            if self.config.summarize_provider_name: # 1. 专属
                providers_to_try.append(self.config.summarize_provider_name)

            if self.config.general_pool: # 2. 全局池
                providers_to_try.extend(self.config.general_pool)

            if self.config.judge_provider_names: # 3. 判断池
                providers_to_try.extend(self.config.judge_provider_names)
            
            if not providers_to_try:
                logger.warning("未配置摘要模型、全局池或判断模型，无法执行人格摘要") #
                return original_prompt, "" # (v10.0) 返回空 style
            # --- 修复结束 ---

            # --- ！！！ 2. (v10.12) 构建 Prompt (仅保留 mood) ！！！ ---
            summarize_prompt = f"""
你的任务是分析以下[原始角色设定]，并提取两项关键内容：

1.  **"summarized_persona"**: 将角色设定总结为简洁的核心要点（100-200字），用于*判断模型*。
2.  **"dynamic_style_guide"**: (关键) 生成一套**给 AI (你) 的回复风格指南**。
    - 这个指南**必须**描述 AI 在不同心情(mood)下的行为。
    - **必须**只包含对“心情”(mood)的反应。
    - 你的输出**必须**包含 Python f-string 占位符 `{{mood:.2f}}` 来动态显示当前心情。
    - **重要：** 你的输出**不应**包含 "内部状态指令" 或 "回复风格要求" 这样的词。它应该被写成**直接的指示**。

[原始角色设定]
{original_prompt}

[JSON输出要求]
请严格按照以下JSON格式回复，不要添加任何其他内容：
{{
    "summarized_persona": "（100-200字的人格摘要...）",
    "dynamic_style_guide": "## 内部状态与风格指南 (仅供你参考)\\n\\n* **当前心情**: `{{mood:.2f}}` (-1.0=沮丧, 1.0=积极)\\n* **行为指导**:\\n    * (基于人设) 当心情 < -0.5 时，你的语气应[...插入指示...]。\\n    * (基于人设) 当心情在 -0.5 到 0.5 之间时，你的语气应[...插入指示...]。\\n    * (基于人设) 当心情 > 0.5 时，你的语气应[...插入指示...]。"
}}
""" #
            # --- 修复结束 ---

            # 3. (BUG 13 重构) 调用统一的弹性 JSON Helper
            # (v4.1.1 修复) JSON 重试
            max_retries = 2 # (基于 proactive_task.py 的 JSON 逻辑)
            
            result_data = await elastic_json_chat(
                self.context,
                providers_to_try,
                summarize_prompt,
                max_retries=max_retries
            )
            
            if not result_data:
                 logger.error(f"小模型总结系统提示词失败：弹性调用列表 {providers_to_try} 均失败。")
                 return original_prompt, ""
            
            # 4. (v10.1) 解析结果
            summarized = result_data.get("summarized_persona")
            style_guide = result_data.get("dynamic_style_guide")
            
            if (summarized and isinstance(summarized, str) and len(summarized) > 10 and
                style_guide and isinstance(style_guide, str) and len(style_guide) > 10):
                
                return summarized.strip(), style_guide.strip() # (v10.1) 成功
            else:
                logger.warning(f"小模型返回的总结内容为空或过短 (summarized 或 style_guide 缺失/无效)。Data: {result_data}") #
                return original_prompt, "" # (v10.1)

        except Exception as e:
            # (v10.2) 捕获 NameError 或 API 异常
            logger.error(f"总结系统提示词 API 异常: {e}") #
            import traceback
            logger.error(traceback.format_exc())
            return original_prompt, "" # (v10.1)

    def save_cache(self):
        """(新) 供外部调用，在 terminate 时保存"""
        self.persistence.save_persona_cache(self.cache) #

    def get_all_cache_info(self) -> str:
        """
        (v10.2 修复) 获取缓存状态字符串
        """
        cache_info = "🧠 系统提示词缓存状态 (v10.12)\n\n" # <-- 版本号更新
        
        if not self.cache:
            cache_info += "📭 当前无缓存记录"
        else:
            cache_info += f"📝 总缓存数量: {len(self.cache)}\n\n"
            
            for persona_id, cache_data in self.cache.items(): #
                original_len = len(cache_data.get("original", ""))
                summarized_len = len(cache_data.get("summarized", ""))
                style_len = len(cache_data.get("dynamic_style_guide", "")) # (v10.0)
                
                cache_info += f"👤 **人格ID (Key)**: {persona_id}\n"
                cache_info += f"📏 **摘要压缩率**: {original_len} -> {summarized_len}\n"
                cache_info += f"🎨 **风格指南**: {'✅ (已生成)' if style_len > 0 else '❌ (空)'}\n"
                cache_info += f"📄 **精简内容**: {cache_data.get('summarized', '')[:100]}...\n\n"
        
        return cache_info

    def clear_cache(self) -> int:
        """
        (新) 清除内存缓存
        来源: main.py -> heartflow_cache_clear
        """
        # (v10.3 修复) 增加清除 pending 字典的逻辑
        asyncio.create_task(self._async_clear_cache()) #
        count = len(self.cache)
        return count

    async def _async_clear_cache(self):
        """(v10.3 新增) 异步安全地清除缓存和待处理任务"""
        async with self._lock:
            logger.info(f"正在清除 {len(self.cache)} 个缓存和 {len(self.pending_summaries)} 个待处理任务...")
            # 1. 取消所有正在进行的任务
            for task in self.pending_summaries.values():
                task.cancel()
            self.pending_summaries.clear()
            
            # 2. 清除已完成的缓存
            self.cache.clear()
        
        # 3. 保存到磁盘
        self.save_cache() # 清除后立即保存空状态
        logger.info("心流缓存已异步清除。")