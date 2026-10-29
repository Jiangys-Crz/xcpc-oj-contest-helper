"""The common help and settings entry point."""
from __future__ import annotations

import sys

from .models import VPError
from . import __version__
from .storage import set_workspace_directory, workspace_directory


HELP = """XCPC 比赛终端助手

虚拟参赛（输入命令后设置开始时间；回车立即开始）
  vp cf ID
  vp qoj ID
  vp gym ID
  vp CONTEST_URL

实时比赛（需已有原站参赛资格）
  live cf ID
  live paa ID       CWNUOJ
  live nk ID        牛客
  live CONTEST_URL

补题（支持牛客、CWNUOJ、Codeforces、Gym、QOJ）
  bt CONTEST_URL
  submit A.cpp     在比赛或补题目录中提交

历史记录
  vp history
  vp history 轮次ID
  vp history 轮次ID --open
  vp history --live

手动结束本地 VP
  xcpc end             结束当前本地 VP，不操作原站
  xcpc end 轮次ID      结束指定轮次；保留代码，后续提交计为蓝色补题

路径设置
  xcpc path              显示当前目录并输入新目录
  xcpc path 指定文件夹    直接设置保存目录，路径有空格时请加引号

登录检查
  xcpc doctor paa        检查 CWNUOJ 的 Firefox 登录
  xcpc doctor nk         检查牛客；也支持 cf / gym / qoj

更新
  xcpc update           更新到 Gitee 最新版本，保留原安装位置及个人数据

帮助
  xcpc --help

只有原站允许并确认启动，vp 才创建有效轮次；原站拒绝则取消本次命令。
比赛结束后仍可提交补题，补题记录显示蓝色。
新建目录默认保存在桌面，可用 xcpc path 修改。
修改保存目录后，已有轮次保留在原位置，可从历史记录恢复。"""


def main() -> None:
    args = sys.argv[1:]
    try:
        if args in ([], ["-h"], ["--help"]):
            print(HELP.replace("XCPC 比赛终端助手", "XCPC 比赛终端助手 " + __version__, 1))
        elif args == ["path"]:
            print("当前保存目录：" + str(workspace_directory()))
            try:
                value = input("新的保存目录（回车保留当前设置）：").strip()
            except EOFError:
                return
            if value:
                print("保存目录已设置为：" + str(set_workspace_directory(value)))
        elif len(args) == 2 and args[0] == "path":
            if not args[1].strip():
                raise VPError("保存目录不能为空。")
            print("保存目录已设置为：" + str(set_workspace_directory(args[1])))
        elif len(args) == 2 and args[0] == "doctor":
            aliases = {"paa":"cwnuoj", "nk":"nowcoder", "cf":"cf", "gym":"gym", "qoj":"qoj"}
            if args[1] not in aliases:
                raise VPError("用法：xcpc doctor paa / nk / cf / gym / qoj")
            from .browser_login import login_diagnostics
            from .storage import config_directory, read_json
            platform = aliases[args[1]]
            config = read_json(config_directory() / "config.json")
            account = config.get(platform, config.get("gym", {}) if platform == "cf" else {})
            print("XCPC " + __version__ + " · 此检查只显示配置位置和验证结果，不显示 Cookie 或密码。")
            for line in login_diagnostics(platform, account):
                print(line)
        elif args and args[0] == "end" and len(args) <= 2:
            from .sessions import end_attempt
            _, state = end_attempt(args[1] if len(args) == 2 else None)
            print("本地 VP 已结束：" + state.get("attempt_id", "") + " · " + state["directory"])
            print("代码与历史记录已保留；已开始轮次的后续提交显示为蓝色补题。")
        elif args == ["update"]:
            from .updater import main as update
            update()
        else:
            raise VPError("请运行 xcpc --help 查看命令。")
    except (VPError, OSError) as exc:
        print("xcpc: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
