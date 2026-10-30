"""The common help and settings entry point."""
from __future__ import annotations

import sys

from .models import VPError
from . import __version__
from .storage import set_workspace_directory, workspace_directory


HELP = """XCPC 比赛终端助手

虚拟参赛（同场恢复已有会话；新建时设置开始时间）
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
  优先恢复最新 VP/live 的原目录；补题不计时，Rank 固定，不显示公共统计区。

云端账号与多人组队 VP
  xcpc login              登录协会账号；再次运行可更新密码或切换账号
  xcpc logout             退出云端登录
  xcpc account            查看云端账号和同步状态
  vp https://vp.cwnupaa.com/vp/房间ID
  printfile A.cpp         虚拟打印代码，仅本队成员可见，需英文确认
  云端队伍参赛仅队长可本地创建和提交；比赛数据自动同步到线上。

历史记录
  vp history
  vp history 轮次ID
  vp history 轮次ID --open
  vp history --live

恢复当前比赛面板
  xcpc dashboard    在比赛目录或子目录运行；已结束的比赛进入补题界面

手动结束本地 VP
  xcpc end             优先结束当前目录对应的本地 VP，不操作原站
  xcpc end 轮次ID      结束指定轮次；保留代码，后续提交计为补题，新 AC 方格变蓝

路径设置
  xcpc path              显示当前目录并输入新目录
  xcpc path 指定文件夹    直接设置保存目录，路径有空格时请加引号

登录检查
  xcpc doctor paa        检查 CWNUOJ 的 Firefox 登录
  xcpc doctor nk         检查牛客；也支持 cf / gym / qoj

Codeforces API 设置
  xcpc api cf           粘贴或输入 key 和 secret，VP / BT / live 共用
  xcpc api gym          配置 Gym 使用的 Codeforces 账号

更新
  xcpc update           优先从 VP 云端更新，失败回退 Gitee，保留原安装位置及个人数据
  xcpc update --gitee   使用 Gitee 更新

帮助
  xcpc --help

只有原站允许并确认启动，vp 才创建有效轮次；原站拒绝则取消本次命令。
不同比赛可同时进行 VP / live，各轮计分与提交互不混用；补题不占用 VP。
每场比赛只支持一次 VP；同场命令恢复会话，不重复报名。
目录按比赛名称命名；BT/live 不下载题面。
VP 标题含 CCPC 时使用 CCPC 新赛制，否则按 ICPC。
比赛结束后仍可补题；原 AC 绿色，补题新 AC 方格蓝色，记录沿用 VP 判定颜色。
新建目录默认保存在桌面，可用 xcpc path 修改。
修改保存目录后，已有轮次保留在原位置，可从历史记录恢复。"""


def main() -> None:
    args = sys.argv[1:]
    try:
        if args in ([], ["-h"], ["--help"]):
            help_text = HELP.replace("XCPC 比赛终端助手", "XCPC 比赛终端助手 " + __version__, 1)
            if sys.platform in ('darwin', 'win32'):
                help_text = help_text.replace('Firefox 登录', 'Chrome / Edge 扩展登录')
            print(help_text)
        elif args and args[0] == "login":
            from .cloud import login_command
            login_command(args[1:])
        elif args == ["logout"]:
            from .cloud import logout
            logout()
        elif args == ["account"]:
            from .cloud import account_status
            account_status()
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
        elif len(args) == 2 and args[0] == "api" and args[1] in ("cf", "gym"):
            from .accounts import configure_api
            configure_api(args[1])
        elif len(args) == 2 and args[0] == "doctor":
            aliases = {"paa":"cwnuoj", "nk":"nowcoder", "cf":"cf", "gym":"gym", "qoj":"qoj"}
            if args[1] not in aliases:
                raise VPError("用法：xcpc doctor paa / nk / cf / gym / qoj")
            from .browser_login import login_diagnostics
            from .accounts import account_key
            from .storage import config_directory, read_json
            platform = aliases[args[1]]
            config = read_json(config_directory() / "config.json")
            account = config.get(account_key(config, platform), {})
            print("XCPC " + __version__ + " · 此检查只显示配置位置和验证结果，不显示 Cookie 或密码。")
            for line in login_diagnostics(platform, account):
                print(line)
        elif args and args[0] == "end" and len(args) <= 2:
            from .sessions import end_attempt
            _, state = end_attempt(args[1] if len(args) == 2 else None)
            print("本地 VP 已结束：" + state.get("attempt_id", "") + " · " + state["directory"])
            print("代码与历史记录已保留；已开始轮次的后续提交计为补题，新 AC 方格显示蓝色，记录保留判定颜色。")
        elif args in (["update"], ["update", "--gitee"]):
            from .updater import main as update
            sys.argv = [sys.argv[0], *args[1:]]
            update()
        elif args == ['dashboard']:
            from .cli import dashboard
            dashboard()
        else:
            raise VPError("请运行 xcpc --help 查看命令。")
    except (VPError, OSError, EOFError) as exc:
        print("xcpc: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
