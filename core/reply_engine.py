# heartflow/core/reply_engine.py
# (v10.13 修复 - 确保主 LLM 严格遵守 context_messages_count)
import json
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse
import astrbot.api.message_components as Comp

# (使用相对路径导入 v4.0 模块)
from ..datamodels import JudgeResult, ChatState, UserProfile
from ..config import HeartflowConfig
from ..utils.prompt_builder import PromptBuilder
from ..persistence import PersistenceManager
from ..core.state_manager import StateManager
# (v4.0) 导入 meme 模块
from ..meme_engine.meme_config import MEMES_DIR
from ..meme_engine.meme_emotion_engine import get_emotion_from_text
from ..meme_engine.meme_sender import send_meme

class ReplyEngine:
    """
    (新) v4.0 回复引擎
    职责：负责生成回复、发送消息、保存历史和发送表情
    来源：迁移自 decision_engine.py 和 main.py
    """

    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 prompt_builder: PromptBuilder, 
                 state_manager: StateManager, 
                 persistence: PersistenceManager
                 ):
        self.context = context
        self.config = config
        self.prompt_builder = prompt_builder
        self.state_manager = state_manager
        self.persistence = persistence
        self.bot_name: str = None # 将由 main.py 注入

    async def fetch_bot_name(self):
        """(新) 供 main.py 调用的异步初始化"""
        # (v4.0) 确保 PromptBuilder 中的 bot_name 也被设置
        await self.prompt_builder._fetch_bot_name_from_context()
        self.bot_name = self.prompt_builder.bot_name

    # --- 1. 核心回复处理器 ---

    # ！！！ (v8) 已删除 handle_force_reply ！！！

    async def handle_reply(self, event: AstrMessageEvent, judge_result: JudgeResult):
        """
        (v9.2 修复) v3.5 标准回复 (统一回复入口)
        (v9.2 修复: 增加 LLMResponse is None 检查)
        """
        
        chat_state = self.state_manager._get_chat_state(event.unified_msg_origin)
        user_profile = self.state_manager._get_user_profile(event.get_sender_id())

        # (v8 逻辑)
        is_poke_event = event.get_extra("heartflow_is_poke_event")
        bonus_score = event.get_extra("heartflow_bonus_score", 0.0)
        
        prompt_override = None
        
        if is_poke_event:
            sender_name = event.get_extra("heartflow_poke_sender_name") or "用户"
            prompt_override = f"用户 {sender_name} 刚刚戳了你一下，请你用符合人设的、元气的、简短的（1-2句话）方式回应他/她。" #
        
        # ！！！ (v9.2) 调用 LLM 并检查 None ！！！
        llm_response, _ = await self._get_main_llm_reply(
            event, chat_state, user_profile, 
            prompt_override=prompt_override 
        ) 
        
        if llm_response is None:
            # (v9.2) 核心修复：LLM 调用失败（例如 PROHIBITED_CONTENT）
            logger.warning(f"[{event.unified_msg_origin}] 主LLM调用失败（可能因 PROHIBITED_CONTENT），执行“静默降级”。")
            # 满足用户“退而求其次”的请求：不回复，但更新为被动状态
            self.state_manager._update_passive_state(event, judge_result, batch_size=1)
            event.stop_event()
            return
        # --- 修复结束 ---

        reply_text = llm_response.completion_text.strip()

        if reply_text:
            await event.send(event.plain_result(reply_text)) #
        else:
            logger.warning("[群聊] 主LLM返回了空文本，跳过发送。") #
        
        # 2a. 更新状态
        self.state_manager._update_active_state(event, judge_result) #

        # 2b. (v3.5) 只保存 Assistant 消息
        await self.persistence.save_history_message(
            event.unified_msg_origin, "assistant", reply_text, self.bot_name
        )

        # 2c. 发送表情
        prob = self.config.emotions_probability
        if is_poke_event or bonus_score > 0.0:
            prob = 100
        await self._send_meme(event, reply_text, prob) #
        
        event.stop_event() #

    async def handle_summary_reply(self, event: AstrMessageEvent, judge_result: JudgeResult, message_count: int):
        """
        (v9.2 修复) v4.1 (Bug 10) 修复
        处理“总结判断”的回复
        """
        try:
            # 1. 获取总结回复的 Prompt (v4.1 新)
            recent_messages = await self.prompt_builder._get_recent_messages(event.unified_msg_origin, message_count) #
            
            # (v9.1 架构) prompt_override 不包含人格
            summary_reply_prompt = f"""
以下是最近的群聊摘要：
{recent_messages}

请你针对上述**所有**内容，发表一句总结性的、符合人设的回复。
**重要：你的回复必须自然，就像一个真实群友的“冒泡”，不要暴露你是机器人！**
""" #

            chat_state = self.state_manager._get_chat_state(event.unified_msg_origin) #
            user_profile = self.state_manager._get_user_profile(event.get_sender_id()) #

            # 2. 调用主LLM (使用 prompt_override)
            # ！！！ (v9.2) 调用 LLM 并检查 None ！！！
            llm_response, _ = await self._get_main_llm_reply(event, chat_state, user_profile, prompt_override=summary_reply_prompt) #
            
            if llm_response is None:
                # (v9.2) 核心修复：LLM 调用失败
                logger.warning(f"[{event.unified_msg_origin}] 总结回复：主LLM调用失败，转为被动状态。")
                self.state_manager._update_passive_state(event, judge_result, batch_size=1)
                event.stop_event()
                return
            # --- 修复结束 ---
            
            reply_text = llm_response.completion_text.strip()
            
            if reply_text:
                await event.send(event.plain_result(reply_text)) #
            else:
                logger.warning("[群聊] 总结回复：主LLM返回了空文本。") #
            
            # 3. 更新状态 (消耗精力)
            self.state_manager._update_active_state(event, judge_result) #

            # 4. (Bug 2) 保存历史 (只保存助手回复)
            await self.persistence.save_history_message(
                event.unified_msg_origin, "assistant", reply_text, self.bot_name
            ) #

            # 5. 发送表情 (受概率影响)
            await self._send_meme(event, reply_text, self.config.emotions_probability) #
            
            event.stop_event() #

        except Exception as e:
            logger.error(f"handle_summary_reply 异常: {e}") #
            import traceback
            logger.error(traceback.format_exc()) #
            
    # --- 2. 核心 LLM 调用 ---

    async def _get_main_llm_reply(self, event: AstrMessageEvent, 
                                  chat_state: ChatState, 
                                  user_profile: UserProfile, 
                                  contexts_to_add: list = None, 
                                  prompt_override: str = None) -> (LLMResponse, list):
        """
        (v10.13 修复) v3.5 手动调用主LLM
        (v10.13 修复: 严格遵守 context_messages_count)
        """
        try:
            # 1. 获取主LLM提供商
            provider = self.context.get_using_provider(umo=event.unified_msg_origin) #
            if not provider:
                logger.warning(f"MainLLM: 未找到 {event.unified_msg_origin} 的主回复模型") #
                # ！！！ v9.2 修复：添加 role="assistant" ！！！
                return LLMResponse(role="assistant", completion_text="抱歉，我好像出错了..."), []

            # 2. 获取人格 (v3.4)
            base_system_prompt = await self.prompt_builder._get_persona_system_prompt_by_umo(event.unified_msg_origin) #
            
            # 3. (Bug 2) 加载历史
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin) #
            history = []
            if curr_cid:
                conversation = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, curr_cid) #
                if conversation and conversation.history:
                    history = json.loads(conversation.history) #
            
            # --- ！！！ (v10.13 修复) ！！！ ---
            # 根据用户请求，确保主 LLM 和判断模型看到的历史记录长度一致
            count = self.config.context_messages_count
            if len(history) > count:
                logger.debug(f"MainLLM: 历史记录 {len(history)} > {count}，截断为最近 {count} 条。")
                history = history[-count:]
            # --- 修复结束 ---
            
            if prompt_override is None and history:
                last_message = history.pop() #
                if last_message.get("role") != "user":
                    history.append(last_message)
                else:
                    logger.debug("MainLLM: 修正：已从 history 弹出最后一条 'user' 消息，防止双重注入。")
            
            # 4. (如果适用) 添加额外上下文
            if contexts_to_add:
                history.extend(contexts_to_add) #

            # 5. ！！！ (v9.1 架构) 组装「场景信息」和「Prompt」 ！！！
            enhancements, final_user_prompt = await self.prompt_builder.build_reply_prompt(event, chat_state, user_profile, prompt_override) #
            
            # (v9.1) 将 场景/风格 注入 System Prompt
            final_system_prompt = f"{base_system_prompt}\n\n{enhancements}"
            
            # 6. (v3.3 修复) 组装「视觉信息」
            image_urls_to_send = []
            if event.message_obj and event.message_obj.message: #
                for component in event.message_obj.message:
                    if isinstance(component, Comp.Image) and component.url: #
                        image_urls_to_send.append(component.url)
            
            if image_urls_to_send:
                logger.debug(f"MainLLM: 正在向主回复模型传递 {len(image_urls_to_send)} 张图片。") #

            # 7. 调用LLM
            llm_resp = await provider.text_chat(
                prompt=final_user_prompt,           # (v9.1) User = Message
                context=history, 
                system_prompt=final_system_prompt,  # (v9.1) System = Persona + Enhancements
                image_urls=image_urls_to_send 
            ) #
            return llm_resp, history 
            
        except Exception as e:
            # ！！！ v9.2 修复：捕获 API 异常 (如 PROHIBITED_CONTENT) ！！！
            logger.error(f"MainLLM: _get_main_llm_reply 异常: {e}") #
            import traceback
            logger.error(traceback.format_exc()) #
            # ！！！ v9.2 修复：返回 None 以触发“静默降级” ！！！
            return None, []

    # --- 3. 辅助功能 (表情) ---

    async def _send_meme(self, event: AstrMessageEvent, reply_text: str, probability: int):
        """
        (BUG 9 修复) v4.1.1 修复 Bug 6
        (BUG 9 修复: 构建弹性列表，而不是选择单个 Provider)
        """
        if not self.config.enable_emotion_sending or not reply_text: #
            return
        
        try:
            # --- ！！！(BUG 9 修复：构建弹性列表)！！！ ---
            providers_to_try = []
            if self.config.emotion_model_provider_name: # 1. 专属
                providers_to_try.append(self.config.emotion_model_provider_name)
            
            if self.config.general_pool: # 2. 全局池
                providers_to_try.extend(self.config.general_pool)
            
            if self.config.judge_provider_names: # 3. 判断池
                providers_to_try.extend(self.config.judge_provider_names)
            # --- 修复结束 ---

            if not providers_to_try:
                 logger.warning("表情功能：未配置“心情模型”、“全局池”或“判断池”，跳过。") #
                 return
            
            # 2. 判断心情
            emotion_tag = await get_emotion_from_text(
                self.context,
                providers_to_try, # (BUG 9 修复) 传入列表
                self.config.emotion_mapping,
                self.config.emotion_mapping_string,
                reply_text
            ) #
            
            # 3. 发送表情
            await send_meme(
                self.context, 
                event, 
                emotion_tag,
                probability, # (v8) 
                MEMES_DIR
            ) #
        
        except Exception as e:
            logger.error(f"ReplyEngine: _send_meme 失败: {e}") #