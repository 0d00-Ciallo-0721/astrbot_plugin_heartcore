# heartflow/meme_engine/meme_config.py
# (v4.0 重构 - 迁移 v3.5 版本)
import os
from pathlib import Path

# 获取当前 meme_config.py 文件所在的目录 (即 meme_engine 目录)
# /AstrBot/data/plugins/Heartflow/meme_engine/
PLUGIN_DIR_PARENT = Path(__file__).parent.absolute()

# 获取插件根目录 (meme_engine 的上一级)
# /AstrBot/data/plugins/Heartflow/
PLUGIN_DIR = PLUGIN_DIR_PARENT.parent.resolve()

# 获取 AstrBot 的 data 目录路径
# 从插件根目录向上两级 /AstrBot/data/
DATA_DIR = PLUGIN_DIR.parent.parent.resolve()

# --- 表情包物理存储目录 ---
# 根据服务器结构，表情包位于 data/memes_data/memes/
MEMES_DIR = (DATA_DIR / "memes_data" / "memes").resolve()

# --- 默认表情包的源目录 (在插件包内) ---
# 位于 Heartflow/default_memes/
DEFAULT_MEMES_SOURCE_DIR = (PLUGIN_DIR / "default_memes").resolve()