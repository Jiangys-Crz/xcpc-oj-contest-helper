# CWNU XCPC Vp System

线上组队、多队虚拟赛、训练历史与队内虚拟打印，配合原有终端完成原站参赛和提交。网站使用 Next.js 全栈，终端继续支持 Ubuntu、macOS 和 Windows。

当前版本：**0.9.2**。线上地址：[CWNU XCPC Vp System](https://vp.cwnupaa.com)。

一键安装教程：[网站安装教程](https://vp.cwnupaa.com/install)。Ubuntu / macOS 在终端运行：

```bash
curl -fsSL https://vp.cwnupaa.com/install.sh | bash
```

Windows 在 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://vp.cwnupaa.com/install.ps1 | iex"
```

脚本校验源码包后安装并补齐运行环境。安装完成重新打开终端，执行 `xcpc login`；macOS / Windows 还需按教程加载 Chrome / Edge 登录扩展。已有安装优先调用原更新器，保留自定义目录。下文也保留下载源码手动安装的方式。

| 平台 | VP | 实时比赛 live | 补题 bt |
| --- | --- | --- | --- |
| Codeforces | 支持 Div. / Educational / ICPC 场次，原站 VP 按 ICPC 计分 | 支持原场 CF / IOI / ICPC 计分 | 支持 |
| Codeforces Gym | 支持原站允许的比赛 | 支持比赛链接 | 支持 |
| QOJ | 支持原站允许的 ICPC / OI / IOI 比赛 | 不支持 | 支持 |
| CWNUOJ（`oj.cwnupaa.com`，简称 `paa`） | 不支持 | 支持 XCPC / IOI / OI | 支持 |
| 牛客（简称 `nk`） | 不支持 | 支持 ACM / IOI / OI | 支持 |

## 线上登录、组队和多队 VP

1. 打开 [vp.cwnupaa.com](https://vp.cwnupaa.com)，使用协会官网账号或邮箱、密码登录。首次登录同步账号、昵称、真实姓名、学号和邮箱；学号用于查找成员，不作为登录账号。
2. 在“队伍管理”创建队伍，填写中文和英文队名。创建者为队长，队伍总人数为 1–3 人。按姓名或学号搜索已登录过网站的用户并邀请，对方在“消息”中接受后加入；拒绝后可重新邀请。队长可以编辑队名、移除成员或解散队伍。
3. 在“VP 列表”创建房间，设置名称、QOJ / Codeforces / Gym 比赛链接、开始时间、赛制和封榜分钟数。赛制选项按链接筛选：CF / Gym 为 ICPC、CCPC，QOJ 另有 OI、IOI；需与该场原站规则相符。封榜填写距离结束的分钟数，必填，默认 `0` 表示不封榜。创建者在详情右上方管理信息和人员，可同时邀请个人和队伍。创建者也必须邀请自己或自己的队伍并接受，才算报名。队伍参赛邀请由队长接受。
4. 本地安装后，先登录同一个协会账号：

   ```text
   xcpc login
   xcpc account
   ```

   再次运行 `xcpc login` 可更新保存的密码或切换账号；`xcpc logout` 退出云端账号。macOS / Windows 扩展弹窗也提供云端登录按钮。云账号配置独立于原站 Cookie 和 Codeforces API 配置。

5. 接受房间邀请后，复制房间链接，在本地运行：

   ```text
   vp https://vp.cwnupaa.com/vp/ID
   ```

   程序验证参赛身份并采用网站设置的比赛和时间，不再询问开始时间。个人显示“昵称-姓名”，队伍显示“队名-成员姓名”。队伍只有队长可以创建本地 VP、提交与上传比赛状态，其他队员可在网站查看比赛和队内打印。

6. 原站仍需登录：Ubuntu 使用 Firefox，macOS / Windows 使用扩展；CF / Gym 还需要配置 API。网站登录用于组队、权限和数据同步，原站报名与判题继续走当前平台账号。原站拒绝 VP 时不会伪造本地成功。
7. 在该比赛目录提交和虚拟打印：

   ```text
   submit A.cpp
   printfile A.cpp
   ```

   打印命令显示英文文件信息，输入 `y` 确认后上传源码。在房间“虚拟打印”中查看列表和全屏代码；只有该个人或同一参赛队伍的成员可以看到，房间创建者也不能查看其他队的代码。

登录后，本地 VP、live、BT 的比赛信息和提交记录自动同步到“历史记录”。团队记录对参赛时的队员共享，并单独标记“个人 / 队伍”；`VP / live / BT` 是另一项比赛类型。榜单 Rank 显示原站排名。原 AC 为绿色，失败为红色，赛后新 AC 为蓝色；补题记录不显示开始、结束时间。

历史详情和房间“我的提交”按 DOMjudge 的结构显示成绩行、题目尝试次数与提交列表；队伍成员共享本队的提交，提交编号可跳转原站查看结果。VP 进行期间不能查看其他队伍或个人的提交详情，房间创建者也不能绕过此限制。

VP 房间采用创建时设置的封榜分钟数，`60` 表示结束前一小时封榜，`0` 表示不封榜；比赛结束后自动解封。封榜期间，公共排行榜的所有行只显示封榜前的成绩及后续的待公开次数；本人和队友仍可在“我的提交”和本队历史详情查看真实判题结果。Rank 仍取原站排名，没有可用的封榜前原站排名时显示 `?`。比赛时长由终端读取原站比赛后同步；尚未连接终端时暂按五小时显示。已经预约原站 VP 的房间不能再修改赛制、时间和封榜规则。

CCPC 新赛制中，公共统计的题号需有至少 50 支原赛队伍（包括打星队）通过才揭示；未揭示的题号显示 `?`，按通过人数、尝试人数、提交数排序。封榜或比赛结束后揭示全部题号。房间参赛队伍不计入这 50 支原赛队伍，个人成绩行仍显示完整题号；无法读取原赛统计时不推测数据。

断网时保留本地记录和待同步队列，恢复网络后重试。切换云账号不会把原账号的比赛自动归给新账号。协会密码仅保存在本机私有配置，服务器不保存协会密码；请勿分享个人配置文件。

超级管理员使用登录页“管理员登录”进入，在“网站设置”配置协会外部登录 appKey。初始管理员账号、密码由部署环境提供，不写入代码仓库。服务器部署与备份说明见 [deploy/README.md](deploy/README.md)。

## 1. 安装

默认安装到系统公共目录，命令对这台电脑的所有用户生效；安装和更新公共程序需要管理员权限。账号、Cookie、设置和比赛文件按用户分别保存，日常比赛命令和浏览器请使用同一个普通用户运行。

没有 Git 时，可先从 [Gitee 项目仓库](https://gitee.com/Zyihan_Crz/xcpc-vp-system)下载 ZIP 并解压，再进入源码目录运行对应系统的安装脚本。脚本会补齐 Git，安装后即可使用 `xcpc update` 更新。

各系统的安装脚本会检查环境并补齐缺少的依赖，已有可用环境会继续使用：

| 系统 | 运行环境 | 比赛面板 | 浏览器登录 |
| --- | --- | --- | --- |
| Ubuntu | Python 3.12+、Git；旧版 Ubuntu x86_64 可使用随附 Python | tmux | Firefox，自动读取登录状态 |
| macOS | Python 3.12+、Git、tmux，缺少时通过 Homebrew 安装 | tmux | 已有 Chrome / Edge 加载扩展 |
| Windows | Python 3.12+、Git、Windows Terminal，缺少时通过 WinGet 安装 | Windows Terminal 原生分屏 | 已有 Chrome / Edge 加载扩展 |

Windows 不需要安装 tmux 或 WSL。macOS / Windows 安装器不会替你安装或更换浏览器。

### Ubuntu

支持 x86_64、ARM64，推荐系统 Python 3.12 或更新版本；Ubuntu 26.04 的 Python 3.14 也支持。Ubuntu 22.04 x86_64 可使用随仓库提供的独立 Python 运行时。

```bash
git clone https://gitee.com/Zyihan_Crz/xcpc-vp-system.git
cd xcpc-vp-system
bash install.sh
xcpc --help
```

默认安装到 `/opt/vp-helper`，命令位于 `/usr/local/bin`。安装器会自动补齐 Python、venv、Git、tmux 和证书；需要安装系统依赖时会请求 sudo 权限。日常参赛请用普通用户运行。

也可以安装到当前项目内：

```bash
bash install.sh --local
export PATH="$PWD/bin:$PATH"
xcpc --help
```

本地安装的程序位于项目的 `.vp-install`，命令位于 `bin`；只在缺少系统依赖时需要 sudo。安装器自动配置 Bash / Zsh 的命令路径，保留原有设置。当前终端执行安装器打印的完整 `export PATH=...` 行，新终端自动生效。安装后不要移动程序目录，移动后应重新安装。

Ubuntu **继续使用 Firefox 读取登录状态，无需扩展**，也无需关闭 Firefox。比赛面板使用 tmux。

### macOS

在仓库页面下载 ZIP 并解压，进入解压后的项目文件夹，在系统“终端”中运行：

```bash
sh install-macos.sh
```

脚本会自动补齐 Python 3.12+、Git 和 tmux；需要时先安装 [Homebrew](https://brew.sh/)。首次配置 Homebrew 可能要求系统密码和确认，按终端提示完成即可。

安装器会保留原有设置并自动配置 Bash / Zsh 的命令路径（包括 Homebrew 路径）。**新打开的终端直接生效**；当前终端先执行：

```bash
export PATH="/usr/local/bin:$PATH"
```

确认命令可用：

```bash
xcpc --help
```

程序默认安装到 `/Library/Application Support/XCPC/app`，命令位于 `/usr/local/bin`，对所有电脑用户生效。安装器配置系统命令路径，并使用公共 Homebrew Python，避免依赖某个用户的 Conda / 私有 Python。安装公共程序时会提示输入本机管理员密码。首次安装需要联网下载依赖。

接下来按第 2 节加载 Chrome / Edge 扩展。macOS 比赛面板使用 tmux。

### Windows

支持 Windows 10 / 11 原生运行，使用 Windows PowerShell 5.1 或更新版本。先从 [Gitee 项目仓库](https://gitee.com/Zyihan_Crz/xcpc-vp-system)下载并解压源码，进入包含 `install.ps1` 的文件夹。在资源管理器地址栏输入 `powershell` 并回车，打开当前目录的普通用户 PowerShell，然后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

无需先手动安装 Python、Git 或 Windows Terminal。脚本会复用公共目录内可用的 Python 3.12+，并通过 WinGet 补齐系统级 Python 3.13、Git，以及当前用户缺少的 Windows Terminal；WinGet 不可用时，会先尝试为当前用户注册或修复 App Installer。Windows 安装器出现确认提示时按提示完成即可。如果自动修复失败，脚本会显示原因和 [App Installer 官方安装入口](https://apps.microsoft.com/detail/9nblggh4nns1)。已有 Git 时，也可以用 `git clone` 获取源码后运行同一条安装命令。

使用平时的 PowerShell（包括 Conda 的 `(base)` 环境）启动即可；安装器会弹出一次 UAC 请求安装公共程序，随后回到原用户配置浏览器连接。已经打开管理员 PowerShell 时也可安装。新安装的系统 Python 会直接从注册表识别，无需先退出 Conda 或重开窗口才能继续安装。

`ExecutionPolicy Bypass` 仅作用于这次安装进程，不修改系统的永久执行策略。安装器通过 UAC 请求管理员权限，为整台电脑配置命令路径；安装结束后重新打开 Windows Terminal 或 PowerShell，再执行：

```powershell
xcpc --help
```

XCPC 程序默认位于 `%ProgramFiles%\XCPC\app`，命令位于 `%ProgramFiles%\XCPC\bin`，所有电脑用户均可运行。每位用户独立登录自己的浏览器和云端账号。接下来按第 2 节加载已有 Chrome / Edge 的扩展。

打开比赛后，Windows Terminal 使用原生分屏：上方显示详情，下方是比赛目录中的 PowerShell，无需 tmux。没有找到 `wt.exe` 时，详情会在独立终端打开，当前终端进入比赛目录；输入 `exit` 返回。

## 2. 设置网站登录

### Ubuntu：使用 Firefox

在普通 Firefox 窗口中登录需要使用的比赛网站，再运行比赛命令。程序自动读取该站 Cookie 并核对登录用户，支持传统安装、Snap 和 Flatpak 的 Firefox。

不需要复制 Cookie，也无需为了读取登录状态退出 Firefox。隐私窗口和容器中的登录不会与普通窗口混用。

### macOS / Windows：使用 Chrome 或 Edge 扩展

使用你平时登录比赛网站的普通浏览器配置。**不需要专用窗口，也不需要填写浏览器可执行文件路径。** 每个电脑用户首次运行 `xcpc login`、`xcpc doctor cf` 或比赛命令时，会自动建立该用户自己的扩展连接和扩展文件夹；也可先运行 `xcpc doctor cf` 再加载扩展。

1. Chrome 打开 `chrome://extensions`；Edge 打开 `edge://extensions`。
2. 开启“开发者模式”，选择“加载已解压的扩展程序”。
3. 选择安装器复制好的整个扩展文件夹：

   | 系统 | 扩展文件夹 |
   | --- | --- |
   | macOS | `~/Library/Application Support/XCPC/browser-extension` |
   | Windows | `%APPDATA%\XCPC\browser-extension` |

   macOS 可在文件选择框按 `Command+Shift+G` 粘贴路径；Windows 可在地址栏输入路径。

4. 在扩展详情中允许访问列出的比赛网站。Chrome / Edge 的站点访问设置应允许扩展访问所需网站；不要只保留“点击时”访问。
5. 在安装了扩展的同一个浏览器用户配置中登录网站。点击“XCPC 登录同步”图标，再点“同步登录状态”。
6. 确认弹窗显示“已连接本机 XCPC”，并查看各站 Cookie 数量。

从旧版本更新后，在扩展管理页对 **XCPC 登录同步** 点一次“重新加载”，允许新增的站点权限，再点“同步登录状态”。0.8.2 为同一批比赛域名同时声明 HTTP / HTTPS Cookie 读取权限：Chromium 按 Cookie 的 Secure 属性检查权限，只有 HTTPS 权限时会漏掉部分实际用于 HTTPS 页面的登录 Cookie。这不会改变程序通过 HTTPS 访问比赛网站的方式。

扩展支持已列出的比赛域名、对应 `www` 域名、CWNUOJ / 牛客父域登录，以及 QOJ 的 `contest.ucup.ac` 入口。支持与本站匹配的第一方分区 Cookie，排除隐私窗口和跨站分区 Cookie。不同浏览器用户配置的登录不会混合。

扩展弹窗中的“已连接”表示已连接本机程序；某站 Cookie 为 0 时，说明这份浏览器配置尚未同步到该站可用 Cookie，不等同于网站判定账号退出。请检查扩展站点权限和登录所用的浏览器用户配置。

### 检查登录

```text
xcpc doctor cf
xcpc doctor gym
xcpc doctor qoj
xcpc doctor paa
xcpc doctor nk
```

检查输出不显示 Cookie 值或密码。macOS / Windows 会显示收到的浏览器配置和该站可用 Cookie 数量；读取失败、账号不匹配、网站拦截和网络错误会分别提示。

## 3. 配置 Codeforces API

**三个系统都需要配置 Codeforces handle、API key 和 API secret。** CF 的 VP / BT / live 与 Gym 的 VP / BT 共用同账号配置；QOJ、CWNUOJ、牛客不需要这些密钥。

登录 [Codeforces API 设置页](https://codeforces.com/settings/api)，创建一组 key / secret，然后运行：

```text
xcpc api cf
```

用户名提示为“请输入 Codeforces 用户名：”，随后输入 API key 和 API secret。Codeforces 用户名匹配不区分大小写，保留你输入的显示形式。支持粘贴，secret 不回显。请粘贴同一组密钥，不要填写账号密码。

首次运行 CF / Gym 比赛命令时，缺少配置也会自动提示。macOS / Windows 会先完成 API 配置，再检查浏览器登录；浏览器暂未同步时，已完整输入的 API 配置会保留。

以后修改密钥仍使用 `xcpc api cf`，也支持 `xcpc api gym`。CF / Gym 配置为同一 handle 时同步更新；不同账号分别保存，不跨账号复制密钥。已有比赛目录无需重建。

API 用于读取比赛和判题数据；原站 VP 报名、代码提交仍需对应浏览器账号登录。API key / secret 只保存在全局个人配置，不复制到比赛目录。

## 4. 设置比赛保存目录

默认保存到桌面。可通过交互方式修改：

```text
xcpc path
```

也可以直接指定目录，路径有空格时请加引号：

```bash
# Ubuntu / macOS 示例
xcpc path "$HOME/XCPC"
```

```powershell
# Windows 示例
xcpc path "D:\XCPC"
```

每场比赛使用按比赛名称命名的文件夹。同场再次执行命令时恢复已有会话，代码、子目录和提交记录都会保留。修改默认保存路径后，已有比赛仍保留在原位置。

## 5. 开始 VP

```text
vp cf 2181
vp gym 100123
vp qoj 3617
vp https://codeforces.com/contest/2181
vp https://qoj.ac/contest/3617
```

按提示输入开始时间，采用 Asia/Shanghai 时区：

```text
HH:MM
YYYY-MM-DD HH:MM
```

- CF / Gym：回车默认预约 3 分钟后开始，程序自动向官网设置开始时间。
- QOJ：回车默认 3 分钟后开始；到设置时间自动发送一次原站开始请求。
- 原站拒绝 VP 时取消本次启动；结果尚未确认时先核对原站状态，不重复发送报名请求。
- 每场比赛只支持一次 VP；再次执行原命令恢复该场会话。不同比赛可以同时进行。
- VP 尝试下载题面；PDF 下载失败不阻止使用。

开始前按链接对应平台的提示选择赛制，不再由比赛名称替你决定。CF / Gym 仅提供原站 VP 支持的 ICPC 和 CCPC 展示规则；QOJ 可选择 ICPC、CCPC、OI、IOI，所选计分须与该场原站规则一致，回车默认使用原站规则。ICPC / CCPC / ACM 优先读取原站的封榜时间；原站未提供时，输入距离结束的封榜分钟数，回车默认 `0`，表示不封榜。线上房间采用房间的赛制和封榜设置，不重复询问。参考排名依赖可验证的原赛数据，缺少数据时仍显示本人成绩和提交。

### 手动结束本地 VP

在比赛目录内运行：

```text
xcpc end
```

也可以指定历史轮次：

```text
xcpc end 轮次ID
```

只结束本地计时与计分，不操作原站 VP；代码与记录保留，后续提交进入补题。

## 6. 接入实时比赛

先在原站完成报名并登录对应账号，再运行：

```text
live cf 2272
live paa 6adb59680b835c39ff13486c
live nk 142115
live https://codeforces.com/contest/2272
live https://oj.cwnupaa.com/contest/6adb59680b835c39ff13486c
live https://ac.nowcoder.com/acm/contest/142115
```

`live` 接入已有参赛资格，不代替原站报名。CWNUOJ 的限时参赛场次需要先在原站开始。BT 和 live 不下载或保存题面。

牛客 ACM 默认按 ICPC 处理；IOI 取最高原站得分，OI 取最后一次已判提交的得分。Codeforces live 保留原站 CF / IOI / ICPC 计分，CF 分数以原站公布值为准。隐藏结果显示未公开，不会用猜测的分数或一直 Pending 代替原站已判结果。

## 7. 补题与恢复比赛面板

比赛结束后使用原比赛链接进入补题：

```text
bt https://codeforces.com/contest/2181
bt https://codeforces.com/gym/100123
bt https://qoj.ac/contest/3617
bt https://oj.cwnupaa.com/contest/6adb59680b835c39ff13486c
bt https://ac.nowcoder.com/acm/contest/142115
```

已有 VP / live 记录时，恢复原比赛目录和提交记录；没有记录时建立补题工作区。补题会读取原站已有的提交，包括网页提交，只记录每题尝试次数和是否通过，不按任何赛制计算分数或罚时。补题不计时，不显示公共统计，Rank 固定。原赛 AC 方格保留绿色，补题新 AC 方格显示蓝色；下方提交记录沿用正常判定颜色。

在对应比赛目录或子目录中，可以直接恢复详情：

```text
xcpc dashboard
```

已结束的比赛恢复为补题界面；正在进行的比赛恢复当前面板。重新执行同场 `vp`、`live`、`bt` 命令也会复用已有会话，保留文件内容。

## 8. 提交代码

在对应比赛目录中保存代码文件，再在下方终端运行：

```text
submit A.cpp
submit B.py
submit solution.cpp --problem A
```

文件名为 `A.cpp`、`B.py` 等时，自动识别题号。使用其他文件名时请指定 `--problem`。语言选项和支持的语言因平台而异，可运行 `submit --help` 查看入口说明。

每次提交都会先显示文件、比赛、题号、语言和提交地址，输入 `y` 确认后才发送。服务器返回编号后显示原站判题链接，面板持续同步结果。直接在网页提交的本人记录也会读取，无需通过本地 submit 产生回执。

请求超时或尚未取得编号时，先查看提示中的原站记录链接，确认是否已经提交成功，再决定是否重试。

## 9. 查看记录和操作面板

```text
vp history
vp history 轮次ID
vp history 轮次ID --open
vp history --live
```

- 上方面板可用方向键、`PgUp` / `PgDn`、`Home` / `End` 或鼠标滚轮查看记录。
- Ubuntu / macOS：`Ctrl+B` 后按 `D` 脱离 tmux，面板继续同步；重新执行原命令可恢复。
- Windows：保留比赛终端可继续同步；关闭后在原比赛目录执行 `xcpc dashboard` 恢复。
- `q` 关闭详情面板，不代表结束原站 VP；结束本地 VP 使用 `xcpc end`。
- 窗口太小时请放大终端或上方面板。成绩表的 rank、team、solved、penalty 列按内容收紧，剩余宽度由题目列均分，所有表头和值居中。

## 10. 更新

```text
xcpc update
xcpc update --gitee
xcpc --version
```

默认从 `vp.cwnupaa.com` 获取版本清单和源码包，验证 SHA256 后安装；网站更新源不可用时自动回退到 Gitee `main`。`--gitee` 可直接使用原更新方式。校验失败时停止安装。两种方式都沿用原安装位置并保留账号、比赛保存路径、代码和历史。

`xcpc --version` 在离线状态下显示当前版本、源码版本、安装与命令目录、Python 和依赖命令路径、比赛保存路径、云端账号资料及各原站配置状态。密码、Cookie、API key / secret 和令牌只显示是否配置，不显示具体值；在线登录验证仍使用 `xcpc doctor`。

0.8.2 等旧版仍可运行原来的 `xcpc update`，从 Gitee 升级到新版；以后默认使用网站更新源。不要手动删除安装目录中的旧 release；原生命令入口和 Windows 本地连接会通过初始启动环境转到当前版本。

**macOS / Windows 更新后，还需要重新加载扩展：**

1. 打开 `chrome://extensions` 或 `edge://extensions`。
2. 点击 XCPC 扩展的重新加载按钮，确认显示新版本；有新增站点权限时允许访问。
3. 点击扩展“同步登录状态”，再运行 `xcpc doctor cf` 或其他站点检查命令。

旧版尚无 `xcpc update` 时，先在源码目录执行 `git pull --ff-only`，再按对应系统的安装步骤重装一次。本地 Ubuntu 安装仍使用 `bash install.sh --local`。

### Windows：所有命令启动器都报错时恢复

如果 `xcpc`、`vp`、`submit` 等都因旧版启动器报错，连 `xcpc update` 也无法运行，可以在普通用户 PowerShell 中读取当前版本位置，直接调用安装环境中的 Python 更新：

```powershell
$App = Join-Path $env:ProgramFiles 'XCPC\app'
$Current = Get-Content -LiteralPath (Join-Path $App 'current.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
& (Join-Path $Current.release 'venv\Scripts\python.exe') -I -m vp_helper.updater
```

0.9.0 等旧的个人安装，将第一行改为 `$App = Join-Path $env:LOCALAPPDATA 'XCPC\app'`。自定义过 `--target-root` 时，使用原来的程序目录。这条命令绕过 `xcpc.exe` 等命令启动器，从 Gitee 更新，并沿用原来的程序目录和命令目录。

如果 `current.json` 缺失、当前 Python 也无法启动，或旧版本没有更新模块，重新下载最新源码并解压，进入源码目录后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

使用自定义安装目录时，附上原来的 `--target-root` 和 `--target-bin` 参数。新版安装器会修复最初安装环境中的命令转发代码和浏览器连接入口；账号、比赛目录、代码和历史记录保留。完成后重新打开终端，运行 `xcpc --help`，并按上面的步骤重新加载浏览器扩展。

## 11. 常见问题

### 浏览器已登录，但终端未收到 Cookie

macOS / Windows 先查看扩展弹窗中的各站 Cookie 数量。如果对应站点为 0：

1. 确认登录网页与扩展位于同一个 Chrome / Edge 用户配置中；不同浏览器或不同用户配置不共享登录。
2. 在扩展详情中允许访问该比赛网站，包括需要使用的主域、`www` 域和父域登录网站。
3. 确认程序与扩展都已更新并重新加载，再点击同步。
4. 执行 `xcpc doctor cf`、`xcpc doctor qoj`、`xcpc doctor paa` 或 `xcpc doctor nk` 查看原因。

如果显示“本地连接未建立”，先在使用浏览器的同一个普通系统用户下运行 `xcpc doctor cf`，初始化或修复个人扩展连接，再重新加载扩展。组织策略限制扩展或 Native Messaging 时，需要浏览器管理者允许。

Ubuntu 使用 `xcpc doctor 站点` 检查 Firefox 配置位置和数据库读取情况；不要使用 sudo 运行比赛命令，也不要把隐私窗口或容器登录当作普通窗口登录。

### 已读取 Cookie，但仍验证失败

检查输出中的具体原因：账号不匹配时，在浏览器登录比赛原来的账号；网络、TLS 或网站人机验证失败时，先在该终端和浏览器确认网站可访问，再重试。已有比赛不会自动切换成其他账号。

### Codeforces API 报错或需要换密钥

运行 `xcpc api cf`，重新输入同一组 key / secret；生成页面为 [Codeforces API 设置](https://codeforces.com/settings/api)。浏览器登录与 API 配置都需要有效，API 密钥不能代替网页提交登录。

### 找不到命令

重新运行本系统的一键安装命令，按提示完成管理员授权，再打开新终端执行 `xcpc --version`。Ubuntu / macOS 系统命令在 `/usr/local/bin`，Windows 在 `%ProgramFiles%\XCPC\bin`；旧个人安装会保留文件，新的一键安装会建立系统公共版本。明确使用过 `--local` 或自定义程序目录时，按原设置恢复。

### 原比赛文件夹不见了

再次执行原比赛命令，或通过历史轮次恢复，程序会重新创建缺失的目录。原始代码文件如果已被外部删除，程序无法恢复；提交历史和开始时间仍按本地保存的状态恢复。

### 离线安装与自定义程序路径

Ubuntu 可用 `bash install.sh --offline` 禁止从 PyPI 回退，缺少系统依赖时仍可能需要 apt。可用 `--python /path/to/python3` 选择 Python。

macOS / Windows 安装脚本支持 `--target-root`、`--target-bin` 和 `--offline`。`--offline` 只使用已有系统环境和随附 wheels，缺少 Python、Git 或本系统比赛面板工具时会提示退出，不会下载或安装这些依赖。随附 wheels 主要用于 Ubuntu，macOS / Windows 首次安装建议联网。

`--target-root` 设置程序安装目录，`--target-bin` 设置命令目录；macOS / Windows 安装脚本会自动配置命令路径，包括自定义的 `--target-bin`，完成后重新打开终端。比赛文件保存路径仍使用 `xcpc path` 设置。

## 配置与数据位置

系统公共程序：Ubuntu `/opt/vp-helper`，macOS `/Library/Application Support/XCPC/app`，Windows `%ProgramFiles%\XCPC\app`。以下个人配置和数据不与其他电脑用户混用。`xcpc --version` 可以查看本机实际安装范围和路径。

| 内容 | Ubuntu | macOS | Windows |
| --- | --- | --- | --- |
| 账号与设置 | `~/.config/vp/config.json` | `~/Library/Application Support/XCPC/config.json` | `%APPDATA%\XCPC\config.json` |
| 比赛状态 | `~/.local/state/vp` | `~/Library/Application Support/XCPC/State` | `%LOCALAPPDATA%\XCPC\State` |
| 默认比赛目录 | 桌面 | 桌面 | 系统桌面 |

设置 `XDG_CONFIG_HOME` / `XDG_STATE_HOME` 时优先采用这些位置；原生浏览器与终端需要使用一致设置，普通安装无需设置。扩展原始登录快照保存在本地状态目录，比赛目录的私有 `contest.json` 保存该站提交所需的会话；这些私有文件不纳入仓库。

## 来源与许可证

提交器参考 [hikariyo 的 QOJ submit](https://gist.github.com/hikariyo/b3cec2e736d87be57d1e4faafb04f908)、[ehnryx/cf_submit](https://github.com/ehnryx/cf_submit) 和 [vscode-nowcoderac](https://github.com/Handy2233/vscode-nowcoderac)。原赛数据按可用情况读取 [XCPCIO](https://github.com/xcpcio/board-data) 与 [SRK Collection](https://github.com/algoux/srk-collection)。来源、版本及许可证保留在 `third_party`；仓库不包含个人比赛数据和账号配置。
