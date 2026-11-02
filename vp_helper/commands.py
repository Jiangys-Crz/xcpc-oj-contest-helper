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
  submit A.cpp [-y] 在比赛或补题目录中提交，-y 跳过确认
  优先恢复最新 VP/live 的原目录；补题不计时，Rank 固定，不显示公共统计区。

云端账号与多人组队 VP
  xcpc login              登录协会账号；再次运行可更新密码或切换账号
  xcpc logout             退出云端登录
  xcpc account            查看云端账号和同步状态
  vp https://vp.cwnupaa.com/vp/房间ID
  printfile A.cpp [-y]    虚拟打印代码，仅本队成员可见，-y 跳过确认
  赛时仅队长可本地创建和提交；赛后队员也可用房间链接同步记录并补题。

历史记录
  vp history
  vp history 轮次ID
  vp history 轮次ID --open
  vp history --live

恢复当前比赛面板
  xcpc dashboard    在比赛目录或子目录运行；已结束的比赛进入补题界面

手动导入外榜（在 VP 比赛目录或子目录运行）
  xcpc rank 外榜链接     支持 board.xcpcio.com 的 ICPC 和 rank.iccpc.cn 的 CCPC
  xcpc rank             交互输入外榜链接，按 VP 已用时间回放
  xcpc rank --clear     恢复自动加载原榜（房间指定外榜除外）

手动结束本地 VP
  xcpc end             优先结束当前目录对应的本地 VP，不操作原站
  xcpc end 轮次ID      结束指定轮次；保留代码，后续提交计为补题，新 AC 方格变紫

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
  xcpc update           从 GitHub 更新，保留原安装位置及个人数据
  xcpc update --github  使用 GitHub main 更新（与默认方式相同）
  xcpc update --cloud   使用公网兼容下载入口，不可用时回退 GitHub

帮助
  xcpc --help
  xcpc --version       查看版本、安装位置和配置信息（敏感值隐藏）

只有原站允许并确认启动，vp 才创建有效轮次；原站拒绝则取消本次命令。
不同比赛可同时进行 VP / live，各轮计分与提交互不混用；补题不占用 VP。
每场比赛只支持一次 VP；同场命令恢复会话，不重复报名。
目录按比赛名称命名；BT/live 不下载题面。
新建 VP 按平台选择原站支持的赛制；CF/Gym 为 ICPC、CCPC，QOJ 另支持 OI、IOI。
ICPC/CCPC 优先读取原站封榜时间；未知时手动输入距离结束的分钟数，默认 0 不封榜。
比赛结束后仍可补题；原 AC 绿色，补题新 AC 方格紫色，记录沿用 VP 判定颜色。
新建目录默认保存在桌面，可用 xcpc path 修改。
修改保存目录后，已有轮次保留在原位置，可从历史记录恢复。"""


def main() -> None:
    args = sys.argv[1:]
    try:
        if args not in ([], ['--help'], ['-h'], ['--version'], ['update'], ['update', '--github'], ['update', '--cloud'], ['update', '--gitee']):
            from .chromium_login import ensure_user_bridge
            ensure_user_bridge(force=bool(args and args[0] == 'doctor'))
        if args in ([], ["-h"], ["--help"]):
            help_text = HELP.replace("XCPC 比赛终端助手", "XCPC 比赛终端助手 " + __version__, 1)
            if sys.platform in ('darwin', 'win32'):
                help_text = help_text.replace('Firefox 登录', 'Chrome / Edge 扩展登录')
            print(help_text)
        elif args == ["--version"]:
            from .version_info import print_version_info
            print_version_info()
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
            from .version_info import runtime_user
            print('运行用户：' + runtime_user())
            if sys.platform == 'linux':
                import os
                if os.geteuid() == 0:
                    print('当前为 root。请切回登录 Firefox 的普通用户运行检查和比赛命令；每个用户的登录状态独立保存。')
            for line in login_diagnostics(platform, account):
                print(line)
        elif args and args[0] == "end" and len(args) <= 2:
            from .sessions import end_attempt
            _, state = end_attempt(args[1] if len(args) == 2 else None)
            print("本地 VP 已结束：" + state.get("attempt_id", "") + " · " + state["directory"])
            print("代码与历史记录已保留；已开始轮次的后续提交计为补题，新 AC 方格显示紫色，记录保留判定颜色。")
        elif args in (["update"], ["update", "--github"], ["update", "--cloud"], ["update", "--gitee"]):
            from .updater import main as update
            sys.argv = [sys.argv[0], *args[1:]]
            update()
        elif args and args[0] == 'rank':
            from .external_rank import command
            command(args[1:])
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
