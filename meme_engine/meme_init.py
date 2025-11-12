# heartflow/meme_engine/meme_init.py
# (v4.0 重构 - 迁移 v3.5 版本)
import os
import shutil
import logging
from pathlib import Path

# (v4.0) 使用相对路径从同级目录导入
from .meme_config import MEMES_DIR, DEFAULT_MEMES_SOURCE_DIR #

logger = logging.getLogger(__name__)

def init_meme_storage():
    """初始化表情包存储，如果 data/memes_data/memes 目录不存在或为空，则尝试从插件目录复制默认表情包"""
    try:
        # 确保目标表情包目录存在
        MEMES_DIR.mkdir(parents=True, exist_ok=True) #
        logger.debug(f"确保表情包目录存在: {MEMES_DIR}") #

        # 检查目标目录是否为空
        try:
            next(MEMES_DIR.iterdir()) #
            is_empty = False
        except StopIteration:
            is_empty = True #
        except FileNotFoundError:
            logger.error(f"表情包目录意外不存在: {MEMES_DIR}") #
            return

        if is_empty:
            logger.info(f"'{MEMES_DIR}' 为空或不存在，尝试从 '{DEFAULT_MEMES_SOURCE_DIR}' 复制默认表情包...") #
            
            # 检查默认表情源目录是否存在
            if DEFAULT_MEMES_SOURCE_DIR.exists() and DEFAULT_MEMES_SOURCE_DIR.is_dir(): #
                # 使用 shutil.copytree 复制整个目录树
                shutil.copytree(DEFAULT_MEMES_SOURCE_DIR, MEMES_DIR, dirs_exist_ok=True) #
                logger.info(f"默认表情包已成功复制到: {MEMES_DIR}") #
            else:
                logger.warning(f"找不到默认表情包源目录或该路径不是一个目录: {DEFAULT_MEMES_SOURCE_DIR}") #
                logger.warning("请在插件目录下创建 'default_memes' 文件夹并放入分类好的表情包，或手动将表情包放入 'data/memes_data/memes/' 目录。") #
        else:
            logger.info(f"表情包目录 '{MEMES_DIR}' 已存在且非空，跳过复制默认表情。") #

    except Exception as e:
        logger.error(f"初始化表情包目录失败: {e}") #
        import traceback
        logger.error(traceback.format_exc()) #