# heartflow/core/decision_engine.py
# (v4.1.3 修复 - 移除不兼容的导入)
# (BUG 8/13 统一重构 - 导入 api_utils)
import json
import time
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.api.provider import LLMResponse

# (使用相对路径导入 v4.0 模块)
from ..datamodels import JudgeResult, ChatState, UserProfile
from ..config import HeartflowConfig
from ..utils.prompt_builder import PromptBuilder
from ..core.state_manager import StateManager
# --- (BUG 8/13 重构) ---
from ..utils.api_utils import elastic_simple_text_chat

class DecisionEngine:
    """
    (新) v4.0 决策引擎 (瘦身版)
    职责：只负责调用“判断模型”并返回 JudgeResult
    来源：迁移自 v3.5 decision_engine.py
    """

    def __init__(self, 
                 context: Context, 
                 config: HeartflowConfig, 
                 prompt_builder: PromptBuilder,
                 state_manager: StateManager # <-- (v4.1 Bug 修复) 接收
                 ):
        self.context = context
        self.config = config
        self.prompt_builder = prompt_builder
        self.state_manager = state_manager # <-- (v4.1 Bug 修复) 存储
        
        # (v2.1) 状态
        self.judge_provider_index: int = 0
        self.overload_cooldown_until: dict[str, float] = {}
        self.needs_overload_summary: set = set()

    async def judge_message(self, event: AstrMessageEvent, chat_state: ChatState) -> JudgeResult:
        """
        (v8 修复) 使用小模型进行智能判断
        (v8 修复: 获取 bonus_score 并传递)
        """
        try:
            # --- (v4.1 Bug 修复) ---
            user_profile = None
            if self.config.enable_user_profiles: #
                user_profile = self.state_manager._get_user_profile(event.get_sender_id()) #
            # --- (Bug 修复结束) ---

            # 1. 构建 Prompt (委托 v4.0 PromptBuilder)
            complete_prompt = await self.prompt_builder.build_judge_prompt(
                event, 
                chat_state, 
                user_profile 
            ) #
            
            # ！！！ v8 修复：获取奖励分 ！！！
            bonus_score = event.get_extra("heartflow_bonus_score", 0.0)
            if bonus_score > 0:
                logger.debug(f"心流：检测到 {bonus_score} 奖励分。")
            
            # 2. 获取模型列表
            specific_list = self.config.judge_provider_names #
            general_list = self.config.general_pool #
            
            list_to_try_first = specific_list if specific_list else general_list #

            if not list_to_try_first:
                logger.error("所有模型均未配置（“判断模型”和“全局池”都为空）") #
                return JudgeResult(should_reply=False, reasoning="无可用模型")

            if specific_list:
                 logger.debug(f"心流：尝试 {len(specific_list)} 个专属“判断模型”...") #
            else:
                 logger.debug(f"心流：“判断模型”未配置，自动使用 {len(general_list)} 个“全局池”模型...") #

            # 3. 调用 (v8 修复：传入 bonus_score)
            result, success_index = await self._attempt_model_list( #
                list_to_try_first, 
                complete_prompt, 
                [], 
                chat_state,
                self.judge_provider_index,
                bonus_score # ！！！ v8 修复 ！！！
            )
            
            if result:
                if list_to_try_first is specific_list: #
                    self.judge_provider_index = (success_index + 1) % len(specific_list) #
                return result

            # 4. 备用 (v8 修复：传入 bonus_score)
            if specific_list and general_list: #
                logger.warning(f"“判断模型”列表已过载，尝试使用 {len(general_list)} 个“全局池”作为备用...") #
                
                result, _ = await self._attempt_model_list( #
                    general_list,
                    complete_prompt,
                    [], 
                    chat_state,
                    0, # 备用列表从 0 开始
                    bonus_score # ！！！ v8 修复 ！！！
                )
                
                if result:
                    return result # 备用成功
            
            # 5. 过载 (v2.1 逻辑)
            logger.error(f"所有模型（包括专属和全局池）均尝试失败，触发过载静默: {event.unified_msg_origin}") #
            
            chat_id = event.unified_msg_origin
            self.overload_cooldown_until[chat_id] = time.time() + self.config.overload_cooldown_seconds #
            self.needs_overload_summary.add(chat_id) #
            
            return JudgeResult(should_reply=False, reasoning=f"所有模型均失败，进入过载冷却")
            
        except Exception as e:
            logger.error(f"judge_message 异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return JudgeResult(should_reply=False, reasoning=f"判断引擎异常: {e}")

    async def judge_summary(self, event: AstrMessageEvent, count: int) -> JudgeResult:
        """
        (BUG 8/13 重构) v3.0 总结判断
        """
        try:
            # --- (BUG 8 修复) ---
            # 1. 构建弹性模型列表 (Summarize -> General -> Judge)
            providers_to_try = []
            if self.config.summarize_provider_name: #
                providers_to_try.append(self.config.summarize_provider_name)
            if self.config.general_pool: #
                providers_to_try.extend(self.config.general_pool)
            if self.config.judge_provider_names: #
                providers_to_try.extend(self.config.judge_provider_names)
            
            if not providers_to_try:
                return JudgeResult(should_reply=False, reasoning="总结判断：无可用摘要模型")
            # --- (修复结束) ---

            # 2. 构建 Prompt (不变)
            summary_prompt = await self.prompt_builder.build_summary_prompt(event.unified_msg_origin, count) #

            # 3. (BUG 8/13 重构) 调用统一的弹性 Helper
            decision_text = await elastic_simple_text_chat(
                self.context, 
                providers_to_try, 
                summary_prompt
            )
            
            if decision_text:
                decision = decision_text.strip().upper()
                if "YES" in decision:
                    return JudgeResult(should_reply=True, reasoning=f"总结判断({count}条)：是")
                else:
                    return JudgeResult(should_reply=False, reasoning=f"总结判断({count}条)：否")
            else:
                # (BUG 8 修复) 所有模型均失败
                logger.error(f"执行总结判断失败: 所有模型 {providers_to_try} 均失败")
                return JudgeResult(should_reply=False, reasoning=f"总结判断异常: 所有模型均失败")
        
        except Exception as e:
            logger.error(f"执行总结判断失败 (外层): {e}")
            return JudgeResult(should_reply=False, reasoning=f"总结判断异常: {e}")

    async def judge_overload(self, event: AstrMessageEvent) -> JudgeResult:
        """
        (BUG 8/13 重构) v2.1 过载恢复判断
        """
        try:
            # --- (BUG 8 修复) ---
            # 1. 构建弹性模型列表 (Summarize -> General -> Judge)
            providers_to_try = []
            if self.config.summarize_provider_name: #
                providers_to_try.append(self.config.summarize_provider_name)
            if self.config.general_pool: #
                providers_to_try.extend(self.config.general_pool)
            if self.config.judge_provider_names: #
                providers_to_try.extend(self.config.judge_provider_names)

            if not providers_to_try:
                return JudgeResult(should_reply=False, reasoning="过载恢复：无可用摘要模型")
            # --- (修复结束) ---
            
            # 2. 构建 Prompt (不变)
            overload_prompt = await self.prompt_builder.build_overload_prompt(event.unified_msg_origin) #
            
            # 3. (BUG 8/13 重构) 调用统一的弹性 Helper
            decision_text = await elastic_simple_text_chat(
                self.context, 
                providers_to_try, 
                overload_prompt
            )

            if decision_text:
                decision = decision_text.strip().upper()
                if "YES" in decision:
                    return JudgeResult(should_reply=True, reasoning="过载恢复判断：是")
                else:
                    return JudgeResult(should_reply=False, reasoning="过载恢复判断：否")
            else:
                # (BUG 8 修复) 所有模型均失败
                logger.error(f"执行过载总结判断失败: 所有模型 {providers_to_try} 均失败")
                return JudgeResult(should_reply=False, reasoning=f"过载恢复异常: 所有模型均失败")
        
        except Exception as e:
            logger.error(f"执行过载总结判断失败 (外层): {e}")
            return JudgeResult(should_reply=False, reasoning=f"过载恢复异常: {e}")

    # --- (BUG 8/13 重构) 移除 _attempt_simple_text_chat ---

    async def _attempt_model_list(
        self, 
        provider_names: list, 
        prompt: str, 
        contexts: list,
        chat_state: "ChatState",
        start_index: int = 0,
        bonus_score: float = 0.0 # ！！！ v8 修复：添加 bonus_score 参数 ！！！
    ) -> (JudgeResult, int):
        """
        (v8 修复) 负责API轮询、故障切换、JSON解析、评分计算 (应用 bonus_score)
        """
        if not provider_names:
            return None, 0
            
        provider_count = len(provider_names)
        ordered_provider_names = [
            provider_names[(start_index + i) % provider_count] 
            for i in range(provider_count)
        ]
        
        last_error = "No models in list"

        for i, provider_name in enumerate(ordered_provider_names):
            try:
                judge_provider = self.context.get_provider_by_id(provider_name) #
                if not judge_provider:
                    logger.warning(f"故障切换：未找到提供商 {provider_name}，尝试下一个") #
                    last_error = f"未找到提供商: {provider_name}"
                    continue
            except Exception as e:
                 logger.error(f"故障切换：获取提供商 {provider_name} 失败: {e}，尝试下一个") #
                 last_error = str(e)
                 continue 
            
            logger.debug(f"心流判断：正在尝试模型 {i+1}/{provider_count}: {provider_name}") #

            max_retries = self.config.judge_max_retries #
            for attempt in range(max_retries + 1):
                try:
                    llm_response = await judge_provider.text_chat(
                        prompt=prompt,
                        contexts=contexts
                    ) #
                    content = llm_response.completion_text.strip()
                    
                    if content.startswith("```json"): content = content[7:-3].strip()
                    elif content.startswith("```"): content = content[3:-3].strip()
                    
                    judge_data = json.loads(content) #

                    relevance = judge_data.get("relevance", 0)
                    willingness = judge_data.get("willingness", 0)
                    social = judge_data.get("social", 0)
                    timing = judge_data.get("timing", 0)
                    continuity = judge_data.get("continuity", 0)
                    inferred_mood = judge_data.get("inferred_mood", "neutral")
                    
                    # ！！！ v8 修复：应用奖励分 ！！！
                    overall_score_raw = (
                        (relevance * self.config.weights["relevance"]) +
                        (willingness * self.config.weights["willingness"]) +
                        (social * self.config.weights["social"]) +
                        (timing * self.config.weights["timing"]) +
                        (continuity * self.config.weights["continuity"])
                    ) / 10.0 #
                    
                    overall_score = overall_score_raw + bonus_score # 应用奖励
                    
                    if bonus_score > 0:
                        logger.info(f"心流判断：应用 {bonus_score:.2f} 奖励分。原始: {overall_score_raw:.2f} -> 最终: {overall_score:.2f}")
                    # --- v8 修复结束 ---
                    
                    should_reply_static = overall_score >= self.config.reply_threshold #
                    
                    success_index = (start_index + i) % provider_count #
                    
                    logger.info(f"心流判断成功 (模型: {provider_name}) | 评分: {overall_score:.2f} | 精力: {chat_state.energy:.2f}") #
                    
                    return JudgeResult(
                       relevance=relevance, willingness=willingness,
                       social=social, timing=timing, continuity=continuity,
                       inferred_mood=inferred_mood, 
                       reasoning=judge_data.get("reasoning", "") if self.config.judge_include_reasoning else "", #
                       should_reply=should_reply_static, 
                       confidence=overall_score, # (v8) confidence 
                       overall_score=overall_score # (v8) 
                    ), success_index
                
                except json.JSONDecodeError as e:
                    # (v4.1) JSON 格式错误，重试
                    logger.warning(f"模型 {provider_name} JSON解析失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}") #
                    last_error = f"JSON解析失败: {e}"
                    if attempt == max_retries:
                        logger.error(f"模型 {provider_name} 重试多次JSON解析失败，放弃此模型") #
                        break 
                
                # ！！！v4.1.3 修复：回退到通用的 Exception！！！
                except Exception as e:
                    # (v4.1.3) 捕获所有其他 API 异常 (如 500, Timeout, AuthError)
                    logger.error(f"模型 {provider_name} API调用异常: {e}，尝试下一个 Provider") #
                    last_error = str(e)
                    break # 放弃此模型，尝试下一个 Provider
        
        logger.warning(f"模型列表 {provider_names} 均尝试失败。最后错误: {last_error}") #
        return None, 0