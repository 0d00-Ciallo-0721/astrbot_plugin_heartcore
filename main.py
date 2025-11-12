# heartflow/main.py
# (v4.0 重构 - 瘦身版)
import asyncio
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter as event_filter

# (v4.0) 导入所有重构后的模块
from .config import HeartflowConfig
from .datamodels import JudgeResult, ChatState, UserProfile
from .persistence import PersistenceManager
from .utils.prompt_builder import PromptBuilder
from .utils.pre_filters import PreFilters
from .core.state_manager import StateManager
from .core.decision_engine import DecisionEngine
from .core.reply_engine import ReplyEngine
from .core.message_handler import MessageHandler
from .features.proactive_task import ProactiveTask
from .features.poke_handler import PokeHandler
from .features.command_handler import CommandHandler
from .features.persona_summarizer import PersonaSummarizer
# (v4.0) 导入 meme_init (其他 meme 模块在需要时被调用)
from .meme_engine.meme_init import init_meme_storage

class HeartflowPlugin(Star):
    """
    (新) v4.0 插件主入口
    职责：仅负责依赖注入和注册事件监听器
    """
    
    # ！！！v4.0 修复：将 'config_wrapper' 重命名为 'config'！！！
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        
        # --- 1. 加载配置 ---
        self.config = HeartflowConfig(config) #

        # --- 2. 实例化所有模块 (v4.1 修复注入顺序) ---
        
        # (持久化层)
        self.persistence = PersistenceManager(context, self.config) #
        
        # (状态层)
        self.state_manager = StateManager(
            self.config,
            self.persistence.load_states(), #
            self.persistence.load_user_profiles() #
        ) #
        
        # (工具层)
        self.prompt_builder = PromptBuilder(context, self.config, self.state_manager) # ！！！v4.1 (Bug 1) 修复：必须先实例化
        self.pre_filters = PreFilters(self.config) #

        # (功能层)
        self.persona_summarizer = PersonaSummarizer(
            context, self.config, self.persistence,
            self.prompt_builder # <-- (v4.1 Bug 4) 修复：注入 PromptBuilder
        ) # 
        
        # (核心逻辑层)
        self.decision_engine = DecisionEngine(
            context, self.config, self.prompt_builder,
            self.state_manager # <-- (v4.1 Bug 1) 修复：注入 StateManager
        ) # 
        
        self.reply_engine = ReplyEngine(
            context, self.config, self.prompt_builder, 
            self.state_manager, self.persistence
        ) #
        
        self.message_handler = MessageHandler(
            self.config, self.state_manager, self.decision_engine, 
            self.reply_engine, self.prompt_builder
        ) #
        
        # (特性处理器)
        self.proactive_task_handler = ProactiveTask(
            context, self.config, self.state_manager, 
            self.prompt_builder, self.persona_summarizer
            # (v4.1 Bug 7 修复：已在 proactive_task.py 内部解决)
        ) #
        
        self.poke_handler = PokeHandler(
            context, self.config, self.state_manager, 
            self.reply_engine, self.persistence
        ) #
        
        self.command_handler = CommandHandler(
            context, self.config, self.state_manager, 
            self.persona_summarizer, self.decision_engine
        ) #
        
        self.prompt_builder.set_persona_summarizer(self.persona_summarizer)
        # --- 3. 异步启动 & 初始化 ---
        
        # (v4.0) 异步获取 Bot 昵称并注入
        asyncio.create_task(self._initialize_engines())
        
        # (v4.0) 启动后台任务
        self.proactive_task = asyncio.create_task(self.proactive_task_handler.run_task())

        # (v2.1) 初始化表情包目录
        init_meme_storage()

    async def _initialize_engines(self):
        """(v4.1 修复 Bug 5) 异步初始化需要 API 调用的模块"""
        await self.reply_engine.fetch_bot_name() #

    # --- 4. 注册事件监听 (委托) ---
    
    @event_filter.event_message_type(event_filter.EventMessageType.GROUP_MESSAGE, priority=1000)
    async def on_group_message(self, event: AstrMessageEvent):
        """(v8.1 修复) 委托给预过滤器和状态机"""
        
        # 1. 预过滤 (v8.1 修复：忽略 @, 标记 Nickname)
        if not self.pre_filters.should_process_message(event):
            return
            
        # -----------------------------------------------
        # --- (BUG 1 修复) 检查过载逻辑必须在 bonus_score 之后 ---
        # -----------------------------------------------
        
        # 2. (修复) 立即检查 bonus，这是豁免过载的关键
        bonus_score = event.get_extra("heartflow_bonus_score", 0.0)
        
        # 3. (修复) 检查过载状态
        is_in_cooldown, _ = self.message_handler.get_overload_status(event.unified_msg_origin)
        
        # 4. (修复) 只有在 "没有奖励分数" 的情况下，才应用过载冷却
        if is_in_cooldown and bonus_score == 0.0:
            return # 冷却中，静默 (普通消息被丢弃)
        
        # 5. (修复) 只有在 "没有奖励分数" 的情况下，才检查过载恢复
        # (有奖励分数的消息应该立即处理，而不是触发恢复)
        if bonus_score == 0.0 and event.unified_msg_origin in self.message_handler.decision_engine.needs_overload_summary:
            await self.message_handler.handle_overload_recovery(event)
            return
        
        # -----------------------------------------------
        # --- (修复结束) ---
        # -----------------------------------------------
            
        # 6. (v3.0) 用户画像更新
        if self.config.enable_user_profiles:
            self.state_manager.update_user_profile(event)
            
        # 7. 交给核心状态机处理
        await self.message_handler.handle_group_message(event)

    @event_filter.event_message_type(event_filter.EventMessageType.ALL)
    async def on_poke(self, event: AstrMessageEvent):
        """(v8.3 修复) 委托给 Poke 处理器，如果未停止，则继续执行标准回复流"""
        
        # --- 1. 委托给 Poke 处理器 ---
        await self.poke_handler.on_poke(event)
        
        # --- 2. 检查是否被 Poke 处理器（分支B）停止 ---
        if event.is_stopped(): # (v8 修复：分支B "戳回去" 会停止事件)
            return

        # --- 3. (v8) 如果事件未停止 (分支A "文本回复")，执行标准回复流 ---
        
        # ！！！ v8.3 修复：检查正确的 v8 标志 ！！！
        if not event.get_extra("heartflow_is_poke_event"):
            return # (v8.3) 如果不是分支A，则忽略
        
        # -----------------------------------------------
        # --- (BUG 2 修复) ---
        # -----------------------------------------------
        
        # 3.2 (修复) 移除所有过载检查 (is_in_cooldown, needs_overload_summary)
        # Pokes (分支A) 应该总是高优先级，直接进入 message_handler,
        # message_handler 内部有豁免逻辑。
        
        # -----------------------------------------------
        # --- (修复结束) ---
        # -----------------------------------------------
            
        # 3.4 用户画像更新 (Poke 事件也更新 last_seen)
        if self.config.enable_user_profiles:
            self.state_manager.update_user_profile(event)
            
        # 3.5 交给核心状态机处理 (v8 核心)
        await self.message_handler.handle_group_message(event)

    # --- 5. 注册命令 (委托) ---

    @event_filter.command("heartflow")
    async def heartflow_status(self, event: AstrMessageEvent):
        await self.command_handler.heartflow_status(event)

    @event_filter.command("重载心流")
    async def heartflow_reset(self, event: AstrMessageEvent):
        await self.command_handler.heartflow_reset(event)

    @event_filter.command("查看缓存")
    async def heartflow_cache_status(self, event: AstrMessageEvent):
        await self.command_handler.heartflow_cache_status(event)

    @event_filter.command("清除缓存")
    async def heartflow_cache_clear(self, event: AstrMessageEvent):
        await self.command_handler.heartflow_cache_clear(event)

    # --- 6. 终止 (委托) ---
    
    async def terminate(self):
        """(v4.0) 委托所有模块进行保存和关闭"""
        self.persistence.save_states(self.state_manager.get_all_states()) #
        if self.config.enable_user_profiles:
            self.persistence.save_user_profiles(self.state_manager.get_all_user_profiles()) #
        
        self.persona_summarizer.save_cache() #
        
        if self.proactive_task:
            self.proactive_task.cancel() #