"""The common help and settings entry point."""
from __future__ import annotations

import sys

from .models import VPError
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

路径设置
  xcpc path              显示当前目录并输入新目录
  xcpc path 指定文件夹    直接设置保存目录，路径有空格时请加引号

帮助
  xcpc --help

每次 vp 新建独立轮次；重复 VP 使用本地重新计时，原站评测。
比赛结束后仍可提交补题，补题记录显示蓝色。
新建目录默认保存在桌面，可用 xcpc path 修改。
修改保存目录后，已有轮次保留在原位置，可从历史记录恢复。"""


def main() -> None:
    args = sys.argv[1:]
    try:
        if args in ([], ["-h"], ["--help"]):
            print(HELP)
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
        else:
            raise VPError("请运行 xcpc --help 查看命令。")
    except (VPError, OSError) as exc:
        print("xcpc: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
