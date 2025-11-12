# heartflow/features/poke_handler.py
# (v4.0 重构 - 新文件)
import time
import json
import random
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent, filter as event_filter

# (使用相对路径导入 v4.0 模块)
from ..config import HeartflowConfig
from ..datamodels import JudgeResult, ChatState, UserProfile
from ..core.state_manager import StateManager
from ..core.reply_engine import ReplyEngine
from ..persistence import PersistenceManager

class PokeHandler:
    """
    (新) v4.0 戳一戳处理器
    职责：负责处理 on_poke 事件
    来源：迁移自 main.py -> on_poke
    """

    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 state_manager: StateManager,
                 reply_engine: ReplyEngine,
                 persistence: PersistenceManager # (v4.0) 依赖持久层
                 ):
        self.context = context
        self.config = config
        self.state_manager = state_manager
        self.reply_engine = reply_engine
        self.persistence = persistence

    @event_filter.event_message_type(event_filter.EventMessageType.ALL)
    async def on_poke(self, event: AstrMessageEvent):
        """
        (v8 修复) 50% 戳回 (停止)，50% 设置 "bonus_score" (继续)
        """
        if not self.config.enable_poke_response or event.get_platform_name() != "aiocqhttp": #
            return

        raw_message = getattr(event.message_obj, "raw_message", None) #

        # 1. 解析 Poke 事件
        if (not raw_message or
            raw_message.get('post_type') != 'notice' or
            raw_message.get('notice_type') != 'notify' or
            raw_message.get('sub_type') != 'poke'): #
            return

        bot_id = raw_message.get('self_id')
        sender_id = raw_message.get('user_id')
        target_id = raw_message.get('target_id')
        group_id = raw_message.get('group_id')

        # 2. 检查是否戳机器人
        if not bot_id or not sender_id or not target_id or str(target_id) != str(bot_id): #
            return

        chat_id = event.unified_msg_origin
        logger.info(f"🔥 [群聊] 心流检测到戳一戳 | 来自: {sender_id}") #

        # 3. 检查黑名单
        if sender_id in self.config.user_blacklist: #
            logger.debug(f"戳一戳来自黑名单 {sender_id}，忽略。")
            return
        
        # 4. 获取发送者名称
        sender_name = event.get_sender_name() or sender_id
        
        # 5. (v8 修复) 50/50 概率判断
        if random.random() < 0.5:
            # --- 分支 B (50%)：反戳回复 (v7 逻辑不变) ---
            logger.info(f"🔥 [群聊] 心流触发回复 (Poke：反戳)") #
            reply_placeholder = "[反戳了回去]"
            try:
                payloads = {"user_id": int(sender_id)}
                if group_id:
                    payloads["group_id"] = int(group_id)
                
                if hasattr(event, 'bot'):
                     await event.bot.api.call_action('send_poke', **payloads) #
                else:
                    raise Exception("event.bot 不可用")

            except Exception as e: 
                logger.warning(f"反戳失败: {e}") #
                reply_placeholder = "[反戳失败]"

            poke_judge_result = JudgeResult(should_reply=True, reasoning="Poke Event") #
            user_poke_text = f"[{sender_name} 戳了你一下]"
            
            self.state_manager._update_active_state(event, poke_judge_result) #
            
            await self.persistence.save_history_message(
                chat_id, "user", user_poke_text, 
                self.reply_engine.bot_name, sender_name=sender_name
            ) #
            await self.persistence.save_history_message(
                chat_id, "assistant", reply_placeholder, self.reply_engine.bot_name
            ) #
            
            event.stop_event() # ！！！ 必须停止 ！！！
            return
            
        else:
            # --- 分支 A (50%)：文本回复 (v8 修复) ---
            logger.info(f"🔥 [群聊] 心流触发回复 (Poke：转交标准流，添加奖励分)") #
            
            # ！！！ v8 修复：设置奖励分和标记 ！！！
            event.set_extra("heartflow_bonus_score", self.config.force_reply_bonus_score) #
            event.set_extra("heartflow_is_poke_event", True) #
            event.set_extra("heartflow_poke_sender_name", sender_name) #
            
            # ！！！ 必须 *不* 停止事件 ！！！
            return