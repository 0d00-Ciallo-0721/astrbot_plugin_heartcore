# heartflow/core/state_manager.py
# (v4.0 重构 - 迁移)
# (BUG 16 修复 - 实时关系层级)
import time
import datetime
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from typing import Dict

# (使用相对路径导入 v4.0 模块)
from ..datamodels import ChatState, JudgeResult, UserProfile
from ..config import HeartflowConfig

class StateManager:
    """
    (新) v4.0 状态管理器
    职责：负责内存中的“精力”、“心情”和“用户画像”的计算
    来源：迁移自 v3.5 state_manager.py
    """

    def __init__(self, config: HeartflowConfig, initial_states: Dict[str, ChatState], initial_profiles: Dict[str, UserProfile]):
        # (v4.0) 依赖注入
        self.config = config
        self.chat_states: Dict[str, ChatState] = initial_states
        self.user_profiles: Dict[str, UserProfile] = initial_profiles

    # --- 1. ChatState (群聊状态) ---

    def _get_chat_state(self, chat_id: str) -> ChatState:
        """
        (迁移) 获取群聊状态，如果不存在则创建
        来源: v3.5 state_manager.py
        """
        if chat_id not in self.chat_states:
            self.chat_states[chat_id] = ChatState(energy=self.config.energy_initial) #
            logger.info(f"创建新 ChatState (精力: {self.config.energy_initial:.2f}) for {chat_id[:20]}...")

        today = datetime.date.today().isoformat()
        state = self.chat_states[chat_id]

        if state.last_reset_date != today:
            state.last_reset_date = today
            state.energy = min(1.0, state.energy + 0.2) # (保留 v2.1 逻辑)
            state.mood = 0.0 # (v3.0) 每日重置心情
            logger.debug(f"执行每日状态重置: {chat_id[:20]}... | 精力: {state.energy:.2f} | 心情: {state.mood:.2f}")

        return state

    def get_all_states(self) -> Dict[str, ChatState]:
        """(新) 供 persistence.py 调用"""
        return self.chat_states

    def reset_chat_state(self, chat_id: str):
        """(新) 供 command_handler.py 调用"""
        if chat_id in self.chat_states:
            del self.chat_states[chat_id]
            logger.info(f"心流状态已重置: {chat_id}")
            return True
        return False

    def _get_minutes_since_last_reply(self, chat_id: str) -> int:
        """
        (v4.3.3 修复) 获取距离上次回复的分钟数
        来源: v3.5 state_manager.py
        """
        chat_state = self._get_chat_state(chat_id)

        if chat_state.last_reply_time == 0:
            return 999 

        # --- ！！！ v4.3.3 修复：添加缺失的计算逻辑 ！！！ ---
        minutes_elapsed = (time.time() - chat_state.last_reply_time) / 60
        return int(minutes_elapsed)
        # --- 修复结束 ---

    def get_chat_state_readonly(self, chat_id: str) -> "ChatState | None":
        """(新) 供后台任务调用，只读，不存在时返回 None"""
        return self.chat_states.get(chat_id)

    def _apply_passive_decay(self, chat_id: str):
        """(v4.3.4 修复 Bug 2) v4.2 (F3) 情绪衰减 & (R1) 被动精力恢复"""
        
        # (BUG 3 修复) 
        # chat_state = self._get_chat_state(chat_id) # <- 这是 Bug 3 的根源
        chat_state = self.get_chat_state_readonly(chat_id) #
        
        # 如果状态不存在（例如，刚被 /重载心流 删除），则安全退出
        if not chat_state: #
            return #
        # (修复结束)

        now = time.time()
        
        # 1. (F3) 情绪衰减
        decay_interval_sec = self.config.emotion_decay_interval_hours * 3600 #
        if now - chat_state.last_passive_decay_time > decay_interval_sec: #
            chat_state.last_passive_decay_time = now #
            
            if chat_state.mood > 0: #
                chat_state.mood = max(0.0, chat_state.mood - self.config.mood_decay) #
                logger.debug(f"[{chat_id[:10]}] (F3) 情绪平复，心情 -> {chat_state.mood:.2f}")
            elif chat_state.mood < 0: #
                chat_state.mood = min(0.0, chat_state.mood + self.config.mood_decay) #
                logger.debug(f"[{chat_id[:10]}] (F3) 情绪平复，心情 -> {chat_state.mood:.2f}")
        
        # 2. (R1) 被动精力恢复 (如果太久没说话)
        
        # (BUG 3 修复) 
        # 不再调用 _get_minutes_since_last_reply (因为它会创建状态)
        # 而是直接从已安全获取的 chat_state 计算
        minutes_silent = 999
        if chat_state.last_reply_time != 0:
            minutes_silent = (time.time() - chat_state.last_reply_time) / 60
        # (修复结束)
        
        if isinstance(minutes_silent, (int, float)):
            # ！！！ v4.3.4 修复：必须排除 "999" (从未回复) 的情况 ！！！
            if 60 < minutes_silent < 999: # (在 1 小时到“从未回复”之间)
                 if chat_state.energy < 0.8: # (只恢复到 80%)
                    chat_state.energy = min(0.8, chat_state.energy + 0.1) # (缓慢恢复)
        else:
            # (v4.3.2 修复)
            logger.warning(f"[{chat_id[:10]}] _get_minutes_since_last_reply 返回了 NoneType，跳过被动精力恢复。")

    def _update_mood_with_inertia(self, chat_state: ChatState, inferred_mood: str):
        """
        (v4.1 修复 Bug 1) v3.0 情绪惯性更新
        来源: v3.5 state_manager.py
        """
        if inferred_mood == "positive":
            chat_state.mood = min(1.0, chat_state.mood + self.config.mood_increment) #
        elif inferred_mood == "negative":
            # ！！！v4.1 Bug 1 修复：应该是 减去 增量！！！
            chat_state.mood = max(-1.0, chat_state.mood - self.config.mood_increment) #
        elif inferred_mood == "neutral":
            if chat_state.mood > 0:
                chat_state.mood = max(0.0, chat_state.mood - self.config.mood_decay) #
            elif chat_state.mood < 0:
                chat_state.mood = min(0.0, chat_state.mood + self.config.mood_decay) #

    def _update_active_state(self, event: AstrMessageEvent, judge_result: JudgeResult):
        """
        (v4.3 修改 F1/M2) 更新主动回复状态
        (BUG 16 修复: 增加 tier 实时计算)
        """
        chat_id = event.unified_msg_origin
        chat_state = self._get_chat_state(chat_id)

        chat_state.last_reply_time = time.time()
        chat_state.total_replies += 1
        chat_state.total_messages += 1
        
        # (v4.2 F4) 社交冷却
        chat_state.consecutive_reply_count += 1
        
        # --- v4.3 (F1) 社交评分：积极互动 ---
        if self.config.enable_user_profiles: #
            profile = self._get_user_profile(event.get_sender_id())
            
            # ！！！v4.3.4 修复：使用新配置名！！！
            profile.social_score += self.config.score_positive_interaction #
            logger.debug(f"用户 {profile.user_id} 社交评分 +{self.config.score_positive_interaction:.1f}，总分 {profile.social_score:.1f}")
            
            # --- (BUG 16 修复) ---
            self._recalculate_tier(profile) # 立即重新计算层级
            # --- (修复结束) ---

        # (v4.1.1 修复)
        if judge_result.reasoning == "Nickname Force Reply" or judge_result.reasoning == "Poke Event": #
            logger.debug(f"强行回复 (昵称/Poke)，不消耗精力。") #
        else:
            # 正常消耗精力
            chat_state.energy = max(0.1, chat_state.energy - self.config.energy_decay_rate) #
        
        if judge_result.inferred_mood: #
             self._update_mood_with_inertia(chat_state, judge_result.inferred_mood) #

        # (v3.1 修复) 强制切换到 Single 模式
        if chat_state.judgment_mode == "summary": #
            logger.info(f"[{chat_id[:10]}] 回复成功，已从 'summary' 切换到 'single' 模式。") #
        chat_state.judgment_mode = "single" #
        chat_state.message_counter = 0 #

        logger.debug(f"更新主动状态: {chat_id[:20]}... | 精力: {chat_state.energy:.2f} | 心情: {chat_state.mood:.2f} | 连回: {chat_state.consecutive_reply_count}") # (v4.2 日志)

    def _update_passive_state(self, event: AstrMessageEvent, judge_result: JudgeResult, batch_size: int = 1):
        """
        (v4.3 修改 F1/M1) 更新被动状态
        (BUG 16 修复: 增加 tier 实时计算)
        """
        chat_id = event.unified_msg_origin
        chat_state = self._get_chat_state(chat_id)

        chat_state.total_messages += batch_size #

        chat_state.energy = min(1.0, chat_state.energy + (self.config.energy_recovery_rate * batch_size)) #

        # (v4.2 F4) 社交冷却
        chat_state.consecutive_reply_count = 0 # 只要不回复，就重置

        # --- v4.3 (F1/M1) 社交评分：消极互动 ---
        if batch_size == 1 and judge_result.inferred_mood == "negative": #
            if self.config.enable_user_profiles: #
                profile = self._get_user_profile(event.get_sender_id())
                
                # ！！！v4.3.4 修复：使用新配置名！！！
                profile.social_score += self.config.score_negative_interaction #
                logger.debug(f"用户 {profile.user_id} 社交评分 {self.config.score_negative_interaction:.1f}，总分 {profile.social_score:.1f}")
                
                # --- (BUG 16 修复) ---
                self._recalculate_tier(profile) # 立即重新计算层级
                # --- (修复结束) ---

        if batch_size == 1 and judge_result.inferred_mood: #
            self._update_mood_with_inertia(chat_state, judge_result.inferred_mood)

        logger.debug(f"更新被动状态 (批量: {batch_size}): {chat_id[:20]}... | 精力: {chat_state.energy:.2f}") #


    def _consume_energy_for_proactive_reply(self, chat_id: str):
        """
        (迁移) v3.1 为主动发起话题更新状态 (含模式切换)
        来源: v3.5 state_manager.py
        """
        chat_state = self._get_chat_state(chat_id)

        chat_state.last_reply_time = time.time()
        chat_state.total_replies += 1
        chat_state.total_messages += 1
        
        chat_state.energy = max(0.1, chat_state.energy - self.config.energy_decay_rate) #
        chat_state.mood = 0.0 #
        
        # (v3.1 修复) 强制切换到 Single 模式
        if chat_state.judgment_mode == "summary": #
            logger.info(f"[{chat_id[:10]}] 主动话题回复成功，已从 'summary' 切换到 'single' 模式。") #
        chat_state.judgment_mode = "single" #
        chat_state.message_counter = 0 #

        logger.debug(f"更新Proactive状态: {chat_id[:20]}... | 精力: {chat_state.energy:.2f}") #

    # --- 2. UserProfile (用户画像) ---

    def _get_user_profile(self, user_id: str) -> UserProfile:
        """
        (v4.3.1 修复 Bug 2) v3.0 获取用户画像，如果不存在则创建
        (BUG 7 修复：根据用户需求，移除黑名单惩罚逻辑)
        """
        if user_id not in self.user_profiles:
            # (v4.3) 新增：新用户检查黑名单惩罚
            new_profile = UserProfile(
                user_id=user_id,
                name="未知用户",
                social_score=0.0, # (v4.3)
                last_seen=0.0
            ) #
              
            self.user_profiles[user_id] = new_profile
            
        return self.user_profiles[user_id]

    def update_user_profile(self, event: AstrMessageEvent):
        """
        (v4.3.1 修改) v3.0 更新发言用户的画像信息
        """
        try:
            sender_id = event.get_sender_id()
            if not sender_id:
                return
            
            # (v4.3) _get_user_profile 已包含新用户黑名单检查
            user_profile = self._get_user_profile(sender_id)
            user_profile.name = event.get_sender_name()
            user_profile.last_seen = time.time() #
        except Exception as e:
            logger.warning(f"更新用户画像失败: {e}") #
    
    def get_all_user_profiles(self) -> Dict[str, UserProfile]:
        """(新) 供 persistence.py 调用"""
        return self.user_profiles

    # --- (BUG 16 修复) 新增：层级计算辅助函数 ---
    def _recalculate_tier(self, profile: UserProfile):
        """
        (BUG 16) 
        根据 social_score 实时更新 relationship_tier 字符串
        """
        # ！！！v4.3.4 修复：使用新配置名！！！
        avoid_threshold = self.config.tier_avoiding_score #
        friend_threshold = self.config.tier_friend_score #
        acq_threshold = self.config.tier_acquaintance_score #

        original_tier = profile.relationship_tier
        
        if profile.social_score <= avoid_threshold: #
            profile.relationship_tier = "avoiding"
        elif profile.social_score >= friend_threshold: #
            profile.relationship_tier = "friend"
        elif profile.social_score >= acq_threshold: #
            profile.relationship_tier = "acquaintance"
        else: # (介于 avoiding 和 acquaintance 之间)
            profile.relationship_tier = "stranger"
        
        if original_tier != profile.relationship_tier:
            logger.debug(f"(F1) 用户 {profile.user_id} 关系变更 (实时): {original_tier} -> {profile.relationship_tier}")

    # --- (BUG 16 修复) 修改：后台任务 ---
    def _update_relationship_tiers(self):
        """
        (BUG 10 修复) v4.2 (F1+M2) 社交记忆和关系衰减
        (BUG 10 修复: 衰减逻辑现在每天只对每个用户运行一次)
        (BUG 16 修复: 仅负责“衰减”，并调用 _recalculate_tier)
        """
        if not self.config.enable_user_profiles:
            return
            
        now = time.time()
        decay_days_sec = self.config.social_memory_decay_days * 86400 #
        
        # ！！！v4.3.4 修复：使用新配置名！！！
        decay_score_abs = abs(self.config.score_decay_rate_per_day) #
        
        changed_count = 0
        for profile in self.user_profiles.values():
            
            # --- (BUG 10 修复：每日衰减检查) ---
            # 86400 seconds = 24 hours
            if now - profile.last_decay_check_time > 86400: #
                # 1. 标记“今天”已检查
                profile.last_decay_check_time = now #
                
                # 2. (v4.3.1 M2 修复) 检查是否满足“宽限期”
                if now - profile.last_seen > decay_days_sec: #
                    
                    original_score = profile.social_score
                    # 3. (BUG 10 修复) 仅在此时应用衰减
                    if profile.social_score > decay_score_abs: #
                        # 积极分，向 0 衰减
                        profile.social_score -= decay_score_abs #
                    elif profile.social_score < -decay_score_abs: #
                        # 消极分，向 0 衰减 (治愈)
                        profile.social_score += decay_score_abs #
                    else:
                        # 分数已经接近 0，直接归零
                        profile.social_score = 0.0 #
                    
                    if original_score != profile.social_score:
                         logger.debug(f"用户 {profile.user_id} 关系衰减: {original_score:.1f} -> {profile.social_score:.1f}")
                         changed_count += 1
            # --- (BUG 10 修复结束) ---

            
            # --- (BUG 16 修复) ---
            # (此逻辑 *必须* 位于 24 小时检查 *之外*，
            # 以便在用户积极/消极互动后，立即更新其层级)
            #
            # (在 BUG 16 修复后，此处的调用仅用于捕获“衰减”导致的分数变化)
            self._recalculate_tier(profile)
            # --- (修复结束) ---

        if changed_count > 0:
            logger.info(f"(F1) {changed_count} 个用户的关系因“衰减”而更新。")