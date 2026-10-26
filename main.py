"""主程序：启动恐怖黎明存档修改器的图形界面。

用法（在项目根目录）：

    python main.py                     # 打开界面后手动选择存档
    python main.py path/to/player.gdc  # 启动时直接载入某个存档
    pythonw main.py                    # 不弹出黑色控制台窗口

整个项目只需要运行这一个文件。
"""
from __future__ import annotations

import os
import sys

# 把项目根目录加进模块搜索路径，这样在任意工作目录下都能找到 gd_save 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gd_save.gui import main  # noqa: E402  必须在 sys.path 调整之后再导入

if __name__ == "__main__":
    raise SystemExit(main())
