# heartflow/core/message_handler.py
# (v8.2 修复 - 修复 v8 引入的 "summary" 模式 Bug)
import time
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

# (使用相对路径导入 v4.0 模块)
from ..datamodels import JudgeResult, ChatState, UserProfile
from ..config import HeartflowConfig
from ..persistence import PersistenceManager
from .state_manager import StateManager
from .decision_engine import DecisionEngine
from .reply_engine import ReplyEngine
from ..utils.prompt_builder import PromptBuilder

class MessageHandler:
    """
    (新) v4.0 核心状态机 (原 state_machine.py)
    职责：负责 v3.0 的“总结/单次”判断模式状态机
    来源：迁移自 main.py -> on_group_message
    """

    def __init__(self, 
                 config: HeartflowConfig, 
                 state_manager: StateManager, 
                 decision_engine: DecisionEngine, 
                 reply_engine: ReplyEngine,
                 prompt_builder: PromptBuilder # (v4.0) VL 调度需要 PromptBuilder
                 ):
        self.config = config
        self.state_manager = state_manager
        self.decision_engine = decision_engine
        self.reply_engine = reply_engine
        self.prompt_builder = prompt_builder # (v4.0)

# 位于 message_handler.py

    async def handle_group_message(self, event: AstrMessageEvent):
        """
        (v8.2 修复) v3.5 'on_group_message' 的核心逻辑
        (v8.2 修复: 修正 summary/single 流程，确保 bonus_score 生效)
        (BUG 15 修复: 修正 image_urls 的 NameError/AttributeError)
        """
        try:
            chat_id = event.unified_msg_origin
            chat_state = self.state_manager._get_chat_state(chat_id) #
            judge_result = None
            
            # (v8 修复) 检查是否为 Poke 或 昵称
            is_poke_event = event.get_extra("heartflow_is_poke_event")
            bonus_score = event.get_extra("heartflow_bonus_score", 0.0)

            # -----------------------------------------------
            # --- (v3.5) 核心逻辑：VL 调度与保存 ---
            # -----------------------------------------------
            
            if not is_poke_event: # (v8 修复：Poke 事件跳过 VL)
                event.set_extra("image_description", None) #
                
                # --- (BUG 15 修复：上移 image_urls 定义) ---
                image_urls = []
                if event.message_obj and event.message_obj.message: #
                     for component in event.message_obj.message:
                        if isinstance(component, Comp.Image) and component.url: #
                            image_urls.append(component.url) #
                # --- (修复结束) ---

                if chat_state.judgment_mode == "single" and self.config.enable_image_recognition and image_urls: #
                    
                    vl_provider_name = self.config.image_recognition_provider_name #
                    if vl_provider_name:
                        try:
                            vl_provider = self.reply_engine.context.get_provider_by_id(vl_provider_name) #
                            if vl_provider:
                                logger.debug(f"[{chat_id[:10]}] (v3.5) 调用VL模型分析 {len(image_urls)} 张图片...") #
                                vl_response = await vl_provider.text_chat(
                                    prompt=self.config.image_recognition_prompt, #
                                    image_urls=image_urls #
                                )
                                image_description_text = vl_response.completion_text.strip()
                                logger.info(f"💖 图片识别(VL)成功 (模型: {vl_provider_name})：{image_description_text}") #
                                event.set_extra("image_description", image_description_text) #
                            
                        except Exception as e:
                            logger.error(f"图片识别(VL)在 MessageHandler 失败: {e}") #
                    else:
                        logger.warning(f"图片识别(VL)功能已启用，但 'image_recognition_provider_name' 未配置。") #

            # --- (v3.5) 立即保存用户消息 (Bug 2 修复) ---
            rich_content = await self.prompt_builder._build_rich_content_string(event) #
            
            if rich_content: 
                sender_name = event.get_extra("heartflow_poke_sender_name") or event.get_sender_name()
                await self.reply_engine.persistence.save_history_message(
                    chat_id, "user", rich_content, 
                    self.reply_engine.bot_name, sender_name
                ) #
                logger.debug(f"[{chat_id[:10]}] (v8) 已将 (含VL/Poke) 的用户消息保存到上下文") #
            
            # --- (v3.5) API 节省分支 ---
            # (BUG 15 修复) 此处的 'image_urls' 现在总是已定义的
            if (not is_poke_event and #
                self.config.enable_image_recognition and #
                image_urls and #
                (not event.message_str or not event.message_str.strip())):
                
                logger.info(f"[{chat_id[:10]}] (v3.5) 纯图片消息，已保存VL转述，跳过“判断” API。") #
                self.state_manager._update_passive_state(event, JudgeResult(reasoning="VL Save Only"), batch_size=1) #
                return 
            
            # -----------------------------------------------
            # --- 状态机（v8.2 修复：确保 bonus_score/poke 绕过 summary） ---
            # -----------------------------------------------

            # ！！！ v8.2 修复：summary 模式仅在 *没有* 奖励时运行 ！！！
            if chat_state.judgment_mode == "summary" and not is_poke_event and bonus_score == 0.0:
                chat_state.message_counter += 1 #
                if chat_state.message_counter >= self.config.summary_judgment_count: #
                    logger.debug(f"[{chat_id[:10]}] 达到总结计数，执行总结判断...") #
                    
                    judge_result = await self.decision_engine.judge_summary(event, chat_state.message_counter) #
                    
                    if judge_result.should_reply:
                        logger.info(f"[{chat_id[:10]}] 总结判断触发回复，切换到 'single' 模式。") #
                        await self.reply_engine.handle_summary_reply(event, judge_result, chat_state.message_counter) #
                    else:
                        self.state_manager._update_passive_state(event, judge_result, batch_size=chat_state.message_counter) #
                        chat_state.message_counter = 0 #
                        return
                else:
                    return # (v8.2) 消息被“吃掉”并等待总结

            # ！！！ v8.2 修复：single 模式在 *或* 有奖励时运行 ！！！
            elif chat_state.judgment_mode == "single" or is_poke_event or bonus_score > 0.0:
                
                if is_poke_event or bonus_score > 0.0:
                    logger.debug(f"[{chat_id[:10]}] (v8.2) 奖励消息/Poke，强制进入 'single' 模式判断...")
                else:
                    logger.debug(f"[{chat_id[:10]}] 'single' 模式，执行逐条判断...") #
                
                judge_result = await self.decision_engine.judge_message(event, chat_state) #

                # (v3.4) 动态阈值 (v8 修复：bonus_score 已在 decision_engine 中应用)
                mood_factor = 1.0 - (chat_state.mood * 0.5) #
                dynamic_threshold = max(0.2, min(0.9, self.config.reply_threshold * mood_factor)) #
                
                score_triggers = judge_result.overall_score >= dynamic_threshold #
                energy_triggers = chat_state.energy >= self.config.energy_threshold #
                
                if score_triggers or energy_triggers:
                    judge_result.should_reply = True #
                else:
                    judge_result.should_reply = False #
                
                # (v4.2) 社交冷却
                if judge_result.should_reply:
                    if chat_state.consecutive_reply_count >= self.config.max_consecutive_replies: #
                        # (v8.2 修复：只有在 *没有* 奖励时才应用冷却)
                        if not is_poke_event and bonus_score == 0.0:
                            logger.info(f"[{chat_id[:10]}] 触发回复，但因“社交冷却”而强制跳过。")
                            judge_result.should_reply = False # 强制否决
                    
                if not judge_result.should_reply:
                    chat_state.message_counter += 1 #
                    # (v8.2 修复：只有在 'single' 模式下才切换回 'summary')
                    if (chat_state.judgment_mode == "single" and
                        chat_state.message_counter >= self.config.single_judgment_window): #
                        logger.info(f"[{chat_id[:10]}] 'single' 窗口结束，切回 'summary' 模式。") #
                        chat_state.judgment_mode = "summary" #
                        chat_state.message_counter = 0 #
            
            # --- 6. 统一回复/不回复执行点 (v8 逻辑不变) ---
            if judge_result and judge_result.should_reply:
                # (v8 修复：Poke/Nickname 必定会耗费精力，但 reasoning 不同)
                if is_poke_event:
                    judge_result.reasoning = "Poke Event"
                elif event.get_extra("heartflow_bonus_score", 0.0) > 0:
                    judge_result.reasoning = "Nickname Force Reply"

                await self.reply_engine.handle_reply(event, judge_result) #
            elif judge_result:
                self.state_manager._update_passive_state(event, judge_result, batch_size=1) #
        except Exception as e:
            logger.error(f"[群聊] MessageHandler 处理消息异常: {e}") #
            import traceback
            logger.error(traceback.format_exc()) #

    def get_overload_status(self, chat_id: str) -> (bool, float):
        """
        (新) 供 main.py 检查过载状态
        来源: v3.5 main.py -> on_group_message
        """
        cooldown_end = self.decision_engine.overload_cooldown_until.get(chat_id, 0)
        is_in_cooldown = time.time() < cooldown_end
        return is_in_cooldown, cooldown_end

    async def handle_overload_recovery(self, event: AstrMessageEvent) -> bool:
        """
        (新) 供 main.py 处理过载恢复
        来源: v3.5 main.py -> on_group_message
        返回：是否回复了 (True/False)
        """
        chat_id = event.unified_msg_origin
        if chat_id not in self.decision_engine.needs_overload_summary:
            return False # (理论上不应发生)

        logger.info(f"[{chat_id[:10]}] (v4.0) 冷却结束，执行过载总结判断...") #
        self.decision_engine.needs_overload_summary.remove(chat_id) #
        
        judge_result = await self.decision_engine.judge_overload(event) #
        
        if not judge_result.should_reply:
            self.state_manager._update_passive_state(event, judge_result, batch_size=1) #
            return False
        else:
            await self.reply_engine.handle_reply(event, judge_result) #
            return True
