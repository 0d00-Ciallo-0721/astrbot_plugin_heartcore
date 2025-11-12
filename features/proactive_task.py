# heartflow/features/proactive_task.py
# (v4.3.7 修复 - 添加缺失的 LLM 调用)
# (BUG 12/13 统一重构 - 导入 api_utils)
import asyncio
import json
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import MessageChain
from json.decoder import JSONDecodeError

# (使用相对路径导入 v4.0 模块)
from ..config import HeartflowConfig
from ..core.state_manager import StateManager
from ..utils.prompt_builder import PromptBuilder
from ..features.persona_summarizer import PersonaSummarizer
# --- (BUG 12/13 重构) ---
from ..utils.api_utils import elastic_simple_text_chat

class ProactiveTask:
    """
    (新) v4.0 主动话题任务管理器
    """

    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 state_manager: StateManager,
                 prompt_builder: PromptBuilder,
                 persona_summarizer: PersonaSummarizer
                 ):
        self.context = context
        self.config = config
        self.state_manager = state_manager
        self.prompt_builder = prompt_builder
        self.persona_summarizer = persona_summarizer

    async def run_task(self):
        """
        (v4.3.7 修复 Bug) v4.1 后台任务
        (BUG 3 & 4 修复)
        (BUG 12/13 重构: API 弹性)
        """
        logger.info("💖 心流：主动话题任务已启动。")
        while True:
            try:
                check_interval = self.config.proactive_check_interval_seconds #
                await asyncio.sleep(max(30, check_interval))
                
                if not self.config.enable_heartflow or not self.config.proactive_enabled: #
                    continue

                energy_threshold = self.config.proactive_energy_threshold #
                silence_threshold = self.config.proactive_silence_threshold_minutes #
                global_cooldown = self.config.proactive_global_cooldown_seconds #
                
                # (v4.0) 从 StateManager 获取状态
                chat_ids = list(self.state_manager.get_all_states().keys()) #
                
                # (v4.2) 仅在有群聊时才记录
                if chat_ids:
                    logger.debug(f"心流：执行后台任务检查，目标群聊 {len(chat_ids)} 个。") #

                for chat_id in chat_ids:
                    # --- ！！！ v4.2 (F3) 新增：情绪衰减 ！！！ ---
                    # (BUG 3 修复) _apply_passive_decay 已被修改为只读，此调用是安全的
                    self.state_manager._apply_passive_decay(chat_id) #
                    
                    # (v4.1 逻辑) 检查是否需要 *主动发起话题*
                    if not self.config.proactive_enabled: #
                        continue
                        
                    if self.config.whitelist_enabled and chat_id not in self.config.chat_whitelist: #
                        continue
                    
                    # (BUG 3 修复) 
                    # 必须使用只读 getter，防止 /重载心流 竞争
                    chat_state = self.state_manager.get_chat_state_readonly(chat_id) #
                    
                    # 如果状态不存在（刚被 /重载心流 删除），则跳过
                    if not chat_state: #
                        continue #
                    
                    # (BUG 3 & 4 修复) 
                    # 不再调用 _get_minutes_since_last_reply (因为它会创建状态)
                    # 而是从已安全获取的 chat_state 手动计算
                    minutes_silent = 999
                    if chat_state.last_reply_time != 0:
                        minutes_silent = (time.time() - chat_state.last_reply_time) / 60
                    # (修复结束)
                    
                    # (BUG 4 修复) 
                    # 增加 `and minutes_silent != 999`
                    # 防止在新群聊 (返回 999) 且初始精力高时立即触发
                    if (chat_state.energy > energy_threshold and 
                        minutes_silent > silence_threshold and 
                        minutes_silent != 999): #
                        
                        logger.info(f"[群聊] 心流：{chat_id[:20]}... 满足主动冒泡条件。") #
                        
                        original_prompt = await self.prompt_builder._get_persona_system_prompt_by_umo(chat_id) #
                        summarized_prompt = await self.persona_summarizer.get_or_create_summary(chat_id, original_prompt) #
                        
                        topic_idea_text = None #

                        # --- (v3.0) 尝试恢复旧话题 (Feature 5) ---
                        try:
                            resume_prompt = await self.prompt_builder.build_resume_topic_prompt(chat_id) #
                            
                            if resume_prompt:
                                # (v4.1.1 修复) 获取 Provider
                                provider_name = self.config.summarize_provider_name or \
                                                (self.config.general_pool[0] if self.config.general_pool else \
                                                (self.config.judge_provider_names[0] if self.config.judge_provider_names else None)) #
                                
                                if not provider_name:
                                    raise Exception("未配置任何可用于恢复话题的模型 (Specific/General/Judge)") #
                                
                                provider = self.context.get_provider_by_id(provider_name) #
                                if not provider:
                                    raise Exception(f"未找到模型: {provider_name}") #
                                
                                # (v4.1.1 修复) JSON 重试
                                max_retries = 2
                                for attempt in range(max_retries + 1):
                                    try:
                                        # ！！！ v4.3.8 修复：恢复话题不需要 system_prompt ！！！
                                        llm_resp = await provider.text_chat(prompt=resume_prompt, contexts=[], system_prompt="") #
                                        content = llm_resp.completion_text.strip()
                                        if content.startswith("```json"): content = content[7:-3].strip()
                                        elif content.startswith("```"): content = content[3:-3].strip()
                                        
                                        data = json.loads(content) #
                                        
                                        if data.get("is_interesting") and data.get("was_interrupted") and data.get("topic_summary"):
                                            topic_idea_text = f"继续我们之前聊到的 “{data.get('topic_summary')}”" #
                                        
                                        break # 成功，跳出重试
                                    
                                    except (json.JSONDecodeError, JSONDecodeError) as e: #
                                        logger.warning(f"恢复话题 JSON 解析失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}") #
                                        if attempt == max_retries:
                                            raise # 重试失败，抛出异常
                        except Exception as e:
                            logger.warning(f"心流：尝试恢复旧话题失败: {e}，将生成新话题。") #
                        # --- 恢复旧话题结束 ---
                        
                        opening_line_text = None #

                        # --- (BUG 12/13 重构：弹性生成新话题) ---
                        
                        # 1. (BUG 12) 构建弹性模型列表 (Summarize -> General -> Judge)
                        providers_to_try = []
                        if self.config.summarize_provider_name: #
                            providers_to_try.append(self.config.summarize_provider_name)
                        if self.config.general_pool: #
                            providers_to_try.extend(self.config.general_pool)
                        if self.config.judge_provider_names: #
                            providers_to_try.extend(self.config.judge_provider_names)
                        
                        if not providers_to_try:
                             logger.error("主动话题：未配置任何可用于生成话题的模型。")
                             continue
                        # --- (修复结束) ---

                        if not topic_idea_text: #
                            logger.info("心流：生成新话题...") #
                            
                            # 2. (BUG 12) 构建“思路” Prompt
                            topic_idea_prompt = self.prompt_builder.build_proactive_idea_prompt(summarized_prompt, int(minutes_silent)) #
 
                            # 3. (BUG 12/13 重构) 【弹性调用 LLM 1】获取“思路”
                            topic_idea_text = await elastic_simple_text_chat(
                                self.context,
                                providers_to_try,
                                topic_idea_prompt,
                                system_prompt=summarized_prompt # 将人格放入 system_prompt
                            )
                            
                            if not topic_idea_text:
                                logger.warning(f"主动话题：LLM 1 (思路) 弹性调用列表 {providers_to_try} 均失败。")
                                continue

                        if topic_idea_text:
                            # 5. (BUG 12) 构建“开场白” Prompt
                            opening_line_prompt = self.prompt_builder.build_proactive_opening_prompt(summarized_prompt, topic_idea_text) #
                            
                            # 6. (BUG 12/13 重构) 【弹性调用 LLM 2】获取“开场白”
                            opening_line_text = await elastic_simple_text_chat(
                                self.context,
                                providers_to_try,
                                opening_line_prompt,
                                system_prompt=summarized_prompt # 将人格放入 system_prompt
                            )

                            if not opening_line_text:
                                logger.warning(f"主动话题：LLM 2 (开场白) 弹性调用列表 {providers_to_try} 均失败。")
                                continue

                            if opening_line_text:
                                # 7. 发送主动消息
                                message_chain = MessageChain().message(opening_line_text) #
                                await self.context.send_message(chat_id, message_chain) #
                                self.state_manager._consume_energy_for_proactive_reply(chat_id) #
                                logger.info(f"💖 [群聊] 心流：已向 {chat_id[:20]}... 发送主动话题。") #
                                await asyncio.sleep(global_cooldown) #
                        # --- 修复结束 ---
                
                # --- ！！！ v4.2 (F1+M2) 新增：更新社交记忆 ！！！ ---
                self.state_manager._update_relationship_tiers() #
                                
            except asyncio.CancelledError:
                logger.info("💖 心流：主动话题任务被取消。") #
                break
            except Exception as e:
                logger.error(f"心流：主动话题任务异常: {e}") #
                import traceback
                logger.error(traceback.format_exc()) #

    # --- (BUG 12/13 重构) 移除 _attempt_simple_text_chat ---