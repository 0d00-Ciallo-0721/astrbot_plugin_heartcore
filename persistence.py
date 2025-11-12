# heartflow/persistence.py
# (v4.0 重构 - 新文件)
# (v5.1 修复：修正 v5 引入的 NameError)
# (BUG 5 修复：使用 config 动态截断)
import os
import json
from dataclasses import asdict
from typing import Dict, Any
from astrbot.api import logger
from astrbot.api.star import Context

# (使用相对路径导入 v4.0 模块)
from .datamodels import ChatState, UserProfile
from .config import HeartflowConfig # (BUG 5 修复) 导入 Config

class PersistenceManager:
    """
    (新) v4.0 持久化管理器
    职责：负责所有文件 I/O (ChatState, UserProfile, PersonaCache, History)
    来源：迁移自 main.py
    """
    
    # (BUG 5 修复) 修改 __init__
    def __init__(self, context: Context, config: HeartflowConfig):
        self.context = context
        self.config = config # (BUG 5 修复) 存储 config
        # (定义所有 ..._file_path)
        self.states_file_path = os.path.join("data", "heartflow_states.json")
        self.user_profiles_file_path = os.path.join("data", "heartflow_user_profiles.json")
        self.persona_cache_file = os.path.join("data", "persona_cache.json")

    # --- 1. History (Bug 2 & 3 修复) ---
    async def save_history_message(self, chat_id: str, role: str, content: str, bot_name: str, sender_name: str = None):
        """
        (迁移) v3.5 核心：手动保存单条消息到数据库
        (BUG 5 修复: 增加动态历史截断)
        """
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(chat_id) #
            history = []
            if curr_cid:
                conv = await self.context.conversation_manager.get_conversation(chat_id, curr_cid) #
                if conv and conv.history: 
                    history = json.loads(conv.history) #
            
            # (v3.5 核心修复)
            formatted_content = ""
            if role == "user":
                formatted_content = f"{sender_name or '用户'}: {content}"
            else:
                formatted_content = f"{bot_name or '我'}: {content}"

            history.append({"role": role, "content": formatted_content}) #
            
            # --- (BUG 5 修复：动态截断) ---
            
            # 1. 获取用户在 WebUI 配置的上下文数量
            user_configured_count = self.config.context_messages_count
            
            # 2. 定义一个系统硬编码的最小/默认最大值（防止无限增长）
            SYSTEM_DEFAULT_MAX = 100
            
            # 3. 使用两者中的 *较大* 值作为截断阈值
            actual_max_history = max(user_configured_count, SYSTEM_DEFAULT_MAX)
            
            if len(history) > actual_max_history:
                # 裁剪列表，只保留最新的 N 条消息
                # (这会自动删除第一条，并保留第 101 条)
                history = history[-actual_max_history:]
                logger.debug(f"[{chat_id[:10]}] 历史记录已截断至 {actual_max_history} 条 (Config: {user_configured_count}, System: {SYSTEM_DEFAULT_MAX})。")
            # --- (修复结束) ---
            
            await self.context.conversation_manager.update_conversation(
                unified_msg_origin=chat_id,
                conversation_id=None, 
                history=history # 保存被截断后的历史
            ) #
        except Exception as e:
            logger.error(f"[{chat_id[:10]}] 手动保存历史失败: {e}") #

    # --- 2. ChatState ---
    def load_states(self) -> Dict[str, ChatState]:
        """
        (v5.1 修复) 从 data/heartflow_states.json 加载状态
        """
        chat_states = {}
        try:
            if os.path.exists(self.states_file_path):
                # ！！！ v5.1 修复：恢复 json.load ！！！
                with open(self.states_file_path, 'r', encoding='utf-8') as f:
                    states_data = json.load(f)
                
                for chat_id, state_dict in states_data.items():
                    # 使用 **kwargs 从字典重新实例化 dataclass
                    chat_states[chat_id] = ChatState(**state_dict)
                logger.info(f"💖 心流：成功加载 {len(chat_states)} 个群聊状态。")
            else:
                logger.info("💖 心流：未找到状态文件，将创建新状态文件。")
                # (v5) 立即保存一个空状态
                self.save_states({}) 
        except Exception as e:
            # (v5.1) 此处会捕获 NameError (line 80)
            logger.error(f"💖 心流：加载状态文件失败: {e}")
        return chat_states

    def save_states(self, chat_states: Dict[str, ChatState]):
        """
        (迁移) 保存状态到 data/heartflow_states.json
        来源: main.py -> _save_states
        """
        try:
            # 使用 asdict 将 ChatState 对象转换为可序列化的字典
            serializable_states = {chat_id: asdict(state) for chat_id, state in chat_states.items()}
            
            os.makedirs(os.path.dirname(self.states_file_path), exist_ok=True)
            
            with open(self.states_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_states, f, ensure_ascii=False, indent=4)
            logger.info(f"💖 心流：成功保存 {len(chat_states)} 个群聊状态。")
        except Exception as e:
            logger.error(f"💖 心流：保存状态文件失败: {e}")

    # --- 3. UserProfile (v3.0) ---
    def load_user_profiles(self) -> Dict[str, UserProfile]:
        """
        (v5.1 修复) 从 data/heartflow_user_profiles.json 加载用户画像
        """
        user_profiles = {}
        try:
            if os.path.exists(self.user_profiles_file_path):
                # ！！！ v5.1 修复：恢复 json.load ！！！
                with open(self.user_profiles_file_path, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                
                for user_id, profile_dict in profiles_data.items():
                    user_profiles[user_id] = UserProfile(**profile_dict)
                logger.info(f"💖 心流：成功加载 {len(user_profiles)} 个用户画像。")
            else:
                logger.info("💖 心流：未找到用户画像文件，将创建新画像文件。")
                # (v5) 立即保存一个空画像
                self.save_user_profiles({})
        except Exception as e:
            # (v5.1) 此处会捕获 NameError (line 123)
            logger.error(f"💖 心流：加载用户画像文件失败: {e}")
        return user_profiles
        
    def save_user_profiles(self, user_profiles: Dict[str, UserProfile]):
        """
        (迁移) 保存用户画像到 data/heartflow_user_profiles.json
        来源: main.py -> _save_user_profiles
        """
        try:
            serializable_profiles = {user_id: asdict(profile) for user_id, profile in user_profiles.items()}
            
            os.makedirs(os.path.dirname(self.user_profiles_file_path), exist_ok=True)
            
            with open(self.user_profiles_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_profiles, f, ensure_ascii=False, indent=4)
            logger.info(f"💖 心流：成功保存 {len(user_profiles)} 个用户画像。")
        except Exception as e:
            logger.error(f"💖 心流：保存用户画像文件失败: {e}")

    # --- 4. PersonaCache (v2.1) ---
    def load_persona_cache(self) -> Dict[str, Any]:
        """
        (v5.1 修复) 从 data/persona_cache.json 加载人格摘要
        """
        cache = {}
        try:
            if os.path.exists(self.persona_cache_file):
                # ！！！ v5.1 修复：恢复 json.load ！！！
                with open(self.persona_cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                logger.info(f"💖 心流：成功加载 {len(cache)} 个人格摘要缓存。")
            else:
                logger.info("💖 心流：未找到人格缓存文件，将创建新缓存文件。")
                # (v5) 立即保存一个空缓存
                self.save_persona_cache({})
        except Exception as e:
            logger.error(f"💖 心流：加载人格缓存文件失败: {e}")
        return cache
        
    def save_persona_cache(self, cache: Dict[str, Any]):
        """
        (迁移) 保存人格摘要到 data/persona_cache.json
        来源: main.py -> _save_persona_cache
        """
        try:
            os.makedirs(os.path.dirname(self.persona_cache_file), exist_ok=True)
            with open(self.persona_cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"💖 心流：保存人格缓存文件失败: {e}")