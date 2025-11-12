# heartflow/utils/pre_filters.py
# (v8.1 修复 - 手动检查 @Bot)
import random
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

# (使用相对路径导入 v4.0 模块)
from ..config import HeartflowConfig

class PreFilters:
    """
    (新) v4.0 消息预过滤器
    职责：负责 _should_process_message 逻辑
    来源：迁移自 v3.5 utils.py
    """

    def __init__(self, config: HeartflowConfig):
        # (v4.0) 依赖注入
        self.config = config

    def _is_at_bot(self, event: AstrMessageEvent) -> bool:
        """
        (v8.1 修复) 手动检查是否为 @Bot 事件
        """
        if not event.message_obj or not event.message_obj.message:
            return False
            
        try:
            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    if str(component.qq) == str(event.get_self_id()):
                        return True # 是 @Bot
        except Exception:
            return False # 捕获异常
        return False # 不是 @Bot

    def should_process_message(self, event: AstrMessageEvent) -> bool:
        """
        (v8.1 修复) 检查是否应该处理这条消息
        """

        # 1. 检查插件是否启用
        if not self.config.enable_heartflow: #
            return False

        # 2. ！！！ v8.1 修复：手动检查 @Bot ！！！
        if self._is_at_bot(event):
            logger.debug(f"心流：跳过 @Bot 消息，交由 AstrBot 默认流程处理。")
            return False # 忽略 @Bot

        # 3. 检查白名单
        if self.config.whitelist_enabled: #
            if not self.config.chat_whitelist:
                return False
            if event.unified_msg_origin not in self.config.chat_whitelist:
                return False

        # 4. 跳过机器人自己的消息
        if event.get_sender_id() == event.get_self_id(): #
            return False

        # 5. (v3.0) 黑名单概率检查
        if self.config.user_blacklist: #
            sender_id = event.get_sender_id()
            if sender_id in self.config.user_blacklist:
                if random.random() > self.config.blacklist_pass_probability: #
                    logger.debug(f"心流：命中黑名单 {sender_id} 且未通过 {self.config.blacklist_pass_probability} 概率检查，跳过。")
                    return False
                else:
                    logger.debug(f"心流：命中黑名单 {sender_id} 但通过概率检查，继续判断。")
        
        # 6. (v3.5 修复) 空消息检查
# 6. (v3.5 修复 & v4.0 用户修复) 增强版空消息检查
        message_str = event.message_str #
        
        # (用户修复) 检查所有非文本组件
        has_image = False 
        has_at = False
        has_reply = False
        
        if event.message_obj and event.message_obj.message: #
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.Image):
                    has_image = True
                elif isinstance(seg, Comp.At):
                    has_at = True
                elif isinstance(seg, Comp.Reply):
                    has_reply = True
                
                # (优化) 如果三个都有了，可以提前停止遍历
                if has_image and has_at and has_reply:
                    break
        
        # (用户修复) 最终判断：
        # 仅当 [文本为空(含纯空格)] 且 [没有图片] 且 [没有@] 且 [没有引用] 时，才丢弃
        if not message_str or not message_str.strip():
            if not has_image and not has_at and not has_reply:
                logger.debug("心流：消息被视为空消息（无文本、图片、@或引用），已丢弃。")
                return False # 丢弃真正的空消息
            else:
                # 消息虽然文本为空，但包含@、引用或图片，允许通过
                logger.debug("心流：消息文本为空，但包含@、引用或图片，允许处理。")
                pass # (继续执行后续的昵称检查)
        
        # --- ！！！ v8 修复：昵称检测 (切换为 bonus_score) ！！！
        bot_nicknames = self.config.bot_nicknames #
        if bot_nicknames:
            stripped_message = message_str.strip() 
            
            for nickname in bot_nicknames:
                if not nickname:
                    continue
                
                if stripped_message.startswith(nickname) or stripped_message.endswith(nickname):
                    logger.debug(f"心流：检测到昵称点名: {nickname}。添加 {self.config.force_reply_bonus_score} 奖励分。")
                    # ！！！ v8 修复：设置奖励分 ！！！
                    event.set_extra("heartflow_bonus_score", self.config.force_reply_bonus_score) #
                    return True #
        
        # 8. 默认允许通过
        return True