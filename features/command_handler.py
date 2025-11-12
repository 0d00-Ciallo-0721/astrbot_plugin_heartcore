# heartflow/features/command_handler.py
# (v4.0 重构 - 新文件)
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent, filter as event_filter

# (使用相对路径导入 v4.0 模块)
from ..config import HeartflowConfig
from ..core.state_manager import StateManager
from ..features.persona_summarizer import PersonaSummarizer

class CommandHandler:
    """
    (新) v4.0 命令处理器
    职责：负责处理 /heartflow, /重载心流 等命令
    来源：迁移自 main.py
    """

    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 state_manager: StateManager,
                 persona_summarizer: PersonaSummarizer,
                 decision_engine: "DecisionEngine" # (v4.0) 依赖决策引擎获取模型信息
                 ):
        self.context = context
        self.config = config
        self.state_manager = state_manager
        self.persona_summarizer = persona_summarizer
        self.decision_engine = decision_engine

    @event_filter.command("heartcore", "心芯状态", "查看心芯")
    async def heartflow_status(self, event: AstrMessageEvent):
        """
        (迁移) 查看心芯状态
        来源: main.py -> heartflow_status
        """
        chat_id = event.unified_msg_origin
        chat_state = self.state_manager._get_chat_state(chat_id)

        # --- (v4.0) 更新显示逻辑 ---
        
        # 1. 判断模型显示
        judge_providers_str = "未配置"
        if self.config.judge_provider_names: #
            judge_providers_str = f"专属: {self.config.judge_provider_names}"
        elif self.config.general_pool: #
            judge_providers_str = f"全局池: {self.config.general_pool}"
        if len(judge_providers_str) > 50:
             judge_providers_str = f"{len(self.config.judge_provider_names or self.config.general_pool)} 个模型 (轮询中)"

        # 2. 摘要模型显示
        summarize_provider_str = "未配置"
        if self.config.summarize_provider_name: #
            summarize_provider_str = f"专属: {self.config.summarize_provider_name}"
        elif self.config.general_pool: #
            summarize_provider_str = f"全局池: {self.config.general_pool[0]}"
        elif self.config.judge_provider_names: #
            summarize_provider_str = f"回退: {self.config.judge_provider_names[0]}"
            
        # 3. 心情模型显示
        emotion_model_str = "未配置"
        if self.config.emotion_model_provider_name: # 1. 专属
            emotion_model_str = f"专属: {self.config.emotion_model_provider_name}"
        elif self.config.general_pool: # 2. 全局池
            emotion_model_str = f"全局池: {self.config.general_pool[0]}"
        elif self.config.judge_provider_names: # 3. 修复：添加判断池回退
            emotion_model_str = f"回退: {self.config.judge_provider_names[0]}"
        
        emotion_status = '✅ 已启用' if self.config.enable_emotion_sending else '❌ 已禁用'
        if self.config.enable_emotion_sending and emotion_model_str == "未配置":
            emotion_status = "⚠️ 启用但未配置模型"
            
        image_model_str = "未配置"
        if self.config.image_recognition_provider_name: #
            image_model_str = f"专属: {self.config.image_recognition_provider_name}"
        
        image_status = '✅ 已启用' if self.config.enable_image_recognition else '❌ 已禁用'
        if self.config.enable_image_recognition and image_model_str == "未配置":
            image_status = "⚠️ 启用但未配置模型"
            
        # --- ！！！ v4.3 新增：获取个人社交状态 ！！！ ---
        user_profile_info = "❌ (用户画像未启用)"
        if self.config.enable_user_profiles: #
            try:
                # 获取 *发送命令者* 的画像
                user_profile = self.state_manager._get_user_profile(event.get_sender_id()) #
                user_profile_info = (
                    f"- 关系层级: {user_profile.relationship_tier}\n" #
                    f"- 社交综合评分: {user_profile.social_score:.1f}" #
                ) 
            except Exception as e:
                user_profile_info = f"⚠️ (获取您的画像失败: {e})"

        # --- ！！！ v4.3.4 修复：状态信息 ！！！ ---
        status_info = f"""
🔮 心芯状态报告 (v4.3.4 / 社交评分)

🧠 **判断状态 (v3.0)**
- 判断模式: {chat_state.judgment_mode.upper()}
- 模式计数器: {chat_state.message_counter} / {self.config.summary_judgment_count if chat_state.judgment_mode == 'summary' else self.config.single_judgment_window}
- 社交冷却: {chat_state.consecutive_reply_count} / {self.config.max_consecutive_replies} (v4.2)

📊 **群聊状态 (v2.0)**
- 群聊ID: {event.unified_msg_origin}
- 精力水平: {chat_state.energy:.2f}/1.0 {'🟢' if chat_state.energy > 0.7 else '🟡' if chat_state.energy > 0.3 else '🔴'}
- 当前心情: {chat_state.mood:.2f} (-1.0 到 1.0)
- 上次回复: {self.state_manager._get_minutes_since_last_reply(chat_id)} 分钟前

👥 **您的社交状态 (v4.3)**
{user_profile_info}

⚙️ **v4.3 社交配置 (Bug 1 修复)**
- [好友/熟人/回避] 阈: [{self.config.tier_friend_score}/{self.config.tier_acquaintance_score}/{self.config.tier_avoiding_score}]
- [积极/消极] 计分: [{self.config.score_positive_interaction}/{self.config.score_negative_interaction}]

❤️ **多模态配置 (v3.0)**
- 图像识别: {image_status}
- 戳一戳: {'✅ 开启' if self.config.enable_poke_response else '❌ 关闭'}
- 表情功能: {emotion_status}
- (标准)表情概率: {self.config.emotions_probability}%

🎯 **插件状态**: {'✅ 已启用' if self.config.enable_heartflow else '❌ 已禁用'}
"""
        await event.send(event.plain_result(status_info)) #

    @event_filter.command("重载心芯")
    async def heartflow_reset(self, event: AstrMessageEvent):
        """
        (迁移) 重置心流状态
        来源: main.py -> heartflow_reset
        """
        chat_id = event.unified_msg_origin
        # (v4.0) 调用 StateManager
        success = self.state_manager.reset_chat_state(chat_id) #
        
        if success:
            # (v4.0) 调用 Persistence
            self.persistence.save_states(self.state_manager.get_all_states()) #
            await event.send(event.plain_result("✅ 心流状态已重置")) #
        else:
            await event.send(event.plain_result("ℹ️ 当前群聊无心流状态，无需重置")) #

    @event_filter.command("查看缓存")
    async def heartflow_cache_status(self, event: AstrMessageEvent):
        """
        (迁移) 查看系统提示词缓存状态
        来源: main.py -> heartflow_cache_status
        """
        # (v4.0) 调用 PersonaSummarizer
        cache_info = self.persona_summarizer.get_all_cache_info() #
        await event.send(event.plain_result(cache_info)) #


    @event_filter.command("清除缓存")
    async def heartflow_cache_clear(self, event: AstrMessageEvent):
        """
        (迁移) 清除系统提示词缓存
        来源: main.py -> heartflow_cache_clear
        """
        # (v4.0) 调用 PersonaSummarizer
        cache_count = self.persona_summarizer.clear_cache() #
        await event.send(event.plain_result(f"✅ 已清除 {cache_count} 个系统提示词缓存")) #
        logger.info(f"系统提示词缓存已清除，共清除 {cache_count} 个缓存") #