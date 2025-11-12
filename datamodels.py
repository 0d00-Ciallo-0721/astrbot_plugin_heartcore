# heartflow/datamodels.py
# (v4.2 更新 - 扩展 F1, F3, F4)
from dataclasses import dataclass, field

@dataclass
class JudgeResult:
    """判断结果数据类"""
    relevance: float = 0.0           # 内容相关度评分
    willingness: float = 0.0         # 回复意愿评分
    social: float = 0.0              # 社交适宜性评分
    timing: float = 0.0              # 时机恰当性评分
    continuity: float = 0.0          # 对话连贯性评分
    reasoning: str = ""              # 判断理由（可选）
    should_reply: bool = False       # 最终决策：是否回复
    confidence: float = 0.0          # 决策置信度 (通常是 overall_score)
    overall_score: float = 0.0       # 综合加权评分
    related_messages: list = None    # (已弃用，保留兼容性)
    inferred_mood: str = "neutral"   # 推断的群聊氛围

    def __post_init__(self):
        # 确保 related_messages 默认为空列表
        if self.related_messages is None:
            self.related_messages = []


@dataclass
class ChatState:
    """群聊状态数据类"""
    energy: float = 1.0              # 当前精力值 (0.1 - 1.0)
    last_reply_time: float = 0.0     # 上次回复的时间戳
    last_reset_date: str = ""        # 上次执行每日重置的日期
    total_messages: int = 0          # 插件加载后该群聊的总消息数
    total_replies: int = 0           # 插件加载后该群聊的总回复数
    mood: float = 0.0                # v2.0 心情状态 (-1.0 消极 -> 1.0 积极)
    
    # --- v3.0 (F1.1) ---
    judgment_mode: str = "summary"   # 当前判断模式: 'summary' (总结) 或 'single' (单次)
    message_counter: int = 0       # 消息计数器 (用于两种模式)

    # --- v4.2 新增 (F3, F4) ---
    consecutive_reply_count: int = 0      # (F4) 社交冷却：连续回复计数
    last_passive_decay_time: float = 0.0  # (F3) 情绪衰减：上次平复的时间戳


@dataclass
class UserProfile:
    """(v4.3 修改) v3.0 用户画像数据类 (Feature 3)"""
    user_id: str                 #
    name: str                 # 最近一次的昵称
    
    # --- ！！！v4.3 废弃！！！ ---
    # interaction_count: int = 0
    # negative_interactions: int = 0
    
    # --- ！！！v4.3 新增！！！ ---
    social_score: float = 0.0   # 综合社交评分
    
    relationship_tier: str = "stranger" # 关系层级 (由 social_score 派生)
    last_seen: float = 0.0      # (v3.0) 最后发言时间戳

    # --- (BUG 10 修复) ---
    # 新增一个字段来跟踪上次执行衰减检查的时间戳
    last_decay_check_time: float = 0.0