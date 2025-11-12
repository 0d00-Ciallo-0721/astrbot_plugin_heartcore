# heartflow/meme_engine/meme_sender.py
# (v4.0 重构 - 迁移 v3.5 版本)
import os
import random
import logging
from pathlib import Path

from astrbot.api import logger # 使用 astrbot 提供的 logger
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent, MessageChain #
from astrbot.api.message_components import Image #

# (v4.0) 路径现在从 meme_config.py 导入
# (注意：v3.5 的代码注释说不导入，但在 v4.0 中，调用者会传入 MEMES_DIR)

async def send_meme(
    context: Context,                   # 传入 AstrBot 上下文
    event: AstrMessageEvent,            # 传入当前消息事件，用于获取发送目标
    emotion_tag: str,                   # 心情判断引擎返回的标签
    probability: int,                   # 发送概率 (0-100)
    memes_dir: Path                     # 表情包根目录 (data/memes_data/memes/)
):
    """
    根据心情标签发送一个随机表情包。
    """
    # 1. 检查标签有效性
    if not emotion_tag or emotion_tag == "none": #
        return

    # 2. 检查概率
    if random.randint(1, 100) > probability: #
        logger.debug(f"表情发送：'{emotion_tag}' 命中，但未通过 {probability}% 概率检查") #
        return

    try:
        # 3. 构建表情子目录路径
        emotion_path = memes_dir / emotion_tag #

        # 4. 检查目录是否存在
        if not emotion_path.is_dir(): #
            logger.warning(f"表情发送：找不到表情目录 {emotion_path}") #
            return

        # 5. 获取目录下所有支持的图片文件
        supported_extensions = (".jpg", ".jpeg", ".png", ".gif") #
        memes = [
            f
            for f in emotion_path.iterdir() #
            if f.is_file() and f.suffix.lower() in supported_extensions
        ]

        if not memes:
            logger.warning(f"表情发送：表情目录为空或无支持的图片格式 {emotion_path}") #
            return

        # 6. 随机选择一个表情文件
        selected_meme_path = random.choice(memes) #

        # 7. 发送图片
        message_to_send = MessageChain([Image.fromFileSystem(str(selected_meme_path))]) #
        
        success = await context.send_message(
            event.unified_msg_origin,
            message_to_send,
        ) #

        if success:
            logger.info(f"💖 表情发送：已发送 '{emotion_tag}' 表情到 {event.unified_msg_origin}") #
        else:
             logger.warning(f"表情发送：context.send_message 返回 False，可能平台不支持或未找到会话 {event.unified_msg_origin}") #

    except Exception as e:
        logger.error(f"表情发送：发送 '{emotion_tag}' 表情失败: {e}") #
        import traceback
        logger.error(traceback.format_exc()) #