"""调试入口 / Debug entrypoint —— IRIS CLI 的根目录可断点启动脚本。

与已安装的 `iris` 命令（`pyproject.toml` 的 `[project.scripts]`）以及
`python -m iris.cli` 完全等价，只是多了一个真实文件：IDE 的"调试当前文件"
可以直接打上断点跑通整条 CLI 链路，不必先手工配置 module、工作目录或
`PYTHONPATH`。

用法 / Usage::

    python iris.py --help
    python iris.py rules list
    python iris.py emulate run firmware.bin --arch auto

在 IDE 中调试时，可导入 ``main``（等价于按下回车）直接启动，或导入 ``app``
拿到 Typer 应用对象自行构造 CliRunner 用例。

Why this file has to impersonate a package
-----------------------------------------
本文件名与 ``src/iris`` 包同名，这是无法回避的冲突，而 Python 会在两种常见
场景下让根目录排在包之前，于是 ``import iris`` 命中的是**本文件**：

* ``python -m iris.cli`` —— ``-m`` 会把当前工作目录放进 ``sys.path[0]``；
  runpy 解析 ``iris.cli`` 前先导入父包 ``iris``，抢在 ``src/iris`` 之前。
* pytest 默认的 prepend 导入模式 —— 把仓库根目录插到 ``sys.path[0]``。

因此本文件有两种身份：

* ``__name__ == "__main__"`` —— 作为脚本运行，转发到 ``iris.cli.main``；
* ``__name__ == "iris"`` —— 被当作包导入，把 ``__path__`` 指向真正的
  ``src/iris``，使 ``iris.cli`` / ``iris.api`` 等子模块照常解析，并执行真正的
  ``__init__.py`` 以补齐 ``__version__`` 等包级属性。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
_PKG = _SRC / "iris"

# 必须无条件移到首位：editable 安装（pip install -e .）已经把 src 加进了
# sys.path，"不在才插入"会跳过，根目录仍排在 src 之前。
if str(_SRC) in sys.path:
    sys.path.remove(str(_SRC))
sys.path.insert(0, str(_SRC))

if __name__ == "iris":
    # 代理身份：让本模块表现得像 src/iris 这个包。
    __path__ = [str(_PKG)]  # type: ignore[attr-defined]
    if _PKG.is_dir():
        __spec__.submodule_search_locations = __path__  # type: ignore[union-attr]
        _init = _PKG / "__init__.py"
        if _init.is_file():
            # 执行真包的 __init__.py 以补齐 __version__ 等包级属性——代理模块
            # 占了 iris 这个名字，真包的初始化逻辑不会自动执行。
            exec(compile(_init.read_bytes(), str(_init), "exec"))  # noqa: S102
else:
    from iris.cli import app, main  # 必须晚于上面的 sys.path 补丁

    __all__ = ["app", "main"]

    if __name__ == "__main__":
        main()
