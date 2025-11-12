# heartflow/config.py
# (v4.3.4 修复 Bug 1)
import json
from dataclasses import dataclass, field
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig

@dataclass
class HeartflowConfig:
    """
    (新) v4.0 配置加载器
    职责：从 _conf_schema.json 加载并验证所有配置项
    来源：迁移自 main.py 的 __init__
    """

    # --- 全局 ---
    general_pool: list = field(default_factory=list)
    enable_heartflow: bool = False

    # --- 判断 ---
    judge_provider_names: list = field(default_factory=list)
    summarize_provider_name: str = ""
    reply_threshold: float = 0.6
    humanization_word_count: int = 30 
    judge_include_reasoning: bool = True
    judge_max_retries: int = 3
    overload_cooldown_seconds: int = 60

    # --- API 优化 (v3.0) ---
    summary_judgment_count: int = 10
    single_judgment_window: int = 10

    # --- 精力 (v2.0) ---
    energy_initial: float = 0.5
    energy_threshold: float = 0.8
    energy_decay_rate: float = 0.1
    energy_recovery_rate: float = 0.02

    # --- 情绪 (v2.0) ---
    mood_increment: float = 0.1
    mood_decay: float = 0.05
    
    # --- v4.2 情绪 (F3) ---
    emotion_decay_interval_hours: float = 1.0

    # --- 上下文 ---
    context_messages_count: int = 5

    # --- 过滤器 (v2.1 / v3.0) ---
    whitelist_enabled: bool = False
    chat_whitelist: list = field(default_factory=list)
    user_blacklist: set = field(default_factory=set)
    blacklist_pass_probability: float = 0.1
    bot_nicknames: list = field(default_factory=list)

    # --- v4.3 社交 (F1, F4, M1, M2) ---
    max_consecutive_replies: int = 3
    # ！！！ v4.3.4 修复：使用新配置名 ！！！
    tier_friend_score: float = 50.0
    tier_acquaintance_score: float = 10.0
    tier_avoiding_score: float = -20.0
    score_positive_interaction: float = 1.0
    score_negative_interaction: float = -1.5
    score_decay_rate_per_day: float = -0.5
    social_memory_decay_days: int = 3

    # --- 感知 & 多模态 (v3.0) ---
    enable_user_profiles: bool = False
    enable_image_recognition: bool = False
    image_recognition_provider_name: str = ""
    image_recognition_prompt: str = ""
    enable_poke_response: bool = False

    # --- 主动话题 (v2.0) ---
    proactive_enabled: bool = False
    proactive_check_interval_seconds: int = 600
    proactive_energy_threshold: float = 0.9
    proactive_silence_threshold_minutes: int = 120
    proactive_global_cooldown_seconds: int = 60

    # --- 表情 (v2.1) ---
    enable_emotion_sending: bool = False
    emotion_model_provider_name: str = ""
    emotions_probability: int = 100
    emotion_mapping: dict = field(default_factory=dict)
    emotion_mapping_string: str = ""

    # --- 权重 (v2.1) ---
    weights: dict = field(default_factory=dict)

    def __init__(self, config: AstrBotConfig):
        """从 AstrBotConfig 加载所有配置"""
        
        # --- 全局 ---
        self.general_pool = config.get("general_small_model_pool", [])
        self.enable_heartflow = config.get("enable_heartflow", False)

        # --- 判断 ---
        self.judge_provider_names = config.get("judge_provider_names", [])
        self.summarize_provider_name = config.get("summarize_provider_name", "")
        self.reply_threshold = config.get("reply_threshold", 0.6)
        self.humanization_word_count = config.get("humanization_word_count", 30)
        self.judge_include_reasoning = config.get("judge_include_reasoning", True)
        self.judge_max_retries = max(0, config.get("judge_max_retries", 3))
        self.force_reply_bonus_score = config.get("force_reply_bonus_score", 0.5)
        self.overload_cooldown_seconds = config.get("overload_cooldown_seconds", 60)

        # --- API 优化 (v3.0) ---
        self.summary_judgment_count = config.get("summary_judgment_count", 10)
        self.single_judgment_window = config.get("single_judgment_window", 10)

        # --- 精力 (v2.0) ---
        self.energy_initial = config.get("energy_initial", 0.5)
        self.energy_threshold = config.get("energy_threshold", 0.8)
        self.energy_decay_rate = config.get("energy_decay_rate", 0.1)
        self.energy_recovery_rate = config.get("energy_recovery_rate", 0.02)

        # --- 情绪 (v2.0) ---
        self.mood_increment = config.get("mood_increment", 0.1)
        self.mood_decay = config.get("mood_decay", 0.05)
        
        # --- v4.2 情绪 (F3) ---
        self.emotion_decay_interval_hours = config.get("emotion_decay_interval_hours", 1.0) #
        
        # --- 上下文 ---
        self.context_messages_count = config.get("context_messages_count", 5)
        
        # --- 过滤器 (v2.1 / v3.0) ---
        self.whitelist_enabled = config.get("whitelist_enabled", False)
        self.chat_whitelist = config.get("chat_whitelist", [])
        self.user_blacklist = set(config.get("user_blacklist", []))
        self.blacklist_pass_probability = config.get("blacklist_pass_probability", 0.1)
        self.bot_nicknames = config.get("bot_nicknames", [])
        
        # --- v4.3 社交 (F1, F4, M1, M2) ---
        self.max_consecutive_replies = config.get("max_consecutive_replies", 3) #
        
        # ！！！ v4.3.4 修复：使用新配置名 ！！！
        self.tier_friend_score = config.get("tier_friend_score", 50.0)
        self.tier_acquaintance_score = config.get("tier_acquaintance_score", 10.0)
        self.tier_avoiding_score = config.get("tier_avoiding_score", -20.0)
        self.score_positive_interaction = config.get("score_positive_interaction", 1.0)
        self.score_negative_interaction = config.get("score_negative_interaction", -1.5)
        self.score_decay_rate_per_day = config.get("score_decay_rate_per_day", -0.5)
        
        self.social_memory_decay_days = config.get("social_memory_decay_days", 3) #

        # --- 感知 & 多模态 (v3.0) ---
        self.enable_user_profiles = config.get("enable_user_profiles", False)
        self.enable_image_recognition = config.get("enable_image_recognition", False)
        self.image_recognition_provider_name = config.get("image_recognition_provider_name", "")
        self.image_recognition_prompt = config.get("image_recognition_prompt", "这是一张图。请用5-10个词简要描述这张图的核心内容。如果是表情包，请描述情绪。")
        self.enable_poke_response = config.get("enable_poke_response", False)

        # --- 主动话题 (v2.0) ---
        self.proactive_enabled = config.get("proactive_enabled", False)
        self.proactive_check_interval_seconds = config.get("proactive_check_interval_seconds", 600)
        self.proactive_energy_threshold = config.get("proactive_energy_threshold", 0.9)
        self.proactive_silence_threshold_minutes = config.get("proactive_silence_threshold_minutes", 120)
        self.proactive_global_cooldown_seconds = config.get("proactive_global_cooldown_seconds", 60)

        # --- 表情 (v2.1) ---
        self.enable_emotion_sending = config.get("enable_emotion_sending", False)
        self.emotion_model_provider_name = config.get("emotion_model_provider_name", "")
        self.emotions_probability = config.get("emotions_probability", 100)
        
        # (v3.0) 加载并解析表情 JSON
        try:
            emotion_json_str = config.get("emotion_descriptions", "{}")
            self.emotion_mapping = json.loads(emotion_json_str)
            self.emotion_mapping_string = "\n".join(
                [f"- {key}: {desc}" for key, desc in self.emotion_mapping.items()]
            )
            if not self.emotion_mapping:
                 logger.warning("表情类别描述 'emotion_descriptions' 为空...")
        except json.JSONDecodeError as e:
            logger.error(f"Config: 解析 'emotion_descriptions' JSON 失败: {e}")
            self.enable_emotion_sending = False
        
        # (v2.1) 权重归一化
        self.weights = {
            "relevance": config.get("judge_relevance", 0.25),
            "willingness": config.get("judge_willingness", 0.2),
            "social": config.get("judge_social", 0.2),
            "timing": config.get("judge_timing", 0.15),
            "continuity": config.get("judge_continuity", 0.2)
        }
        weight_sum = sum(self.weights.values())
        if abs(weight_sum - 1.0) > 1e-6: #
            logger.warning(f"判断权重和不为1，当前和为{weight_sum}")
            self.weights = {k: v / weight_sum for k, v in self.weights.items()} #
            logger.info(f"判断权重和已归一化，当前配置为: {self.weights}")