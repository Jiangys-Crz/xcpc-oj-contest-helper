# VP Helper

Ubuntu 上的比赛终端助手，支持 QOJ、Codeforces ICPC 虚拟参赛、Codeforces Gym，以及 Codeforces、CWNUOJ 正式比赛实时接入。上方显示成绩与完整提交记录，下方保留比赛目录中的普通 shell。

当前版本：0.3.0。

## 安装

支持 Ubuntu x86_64 和 ARM64（aarch64）。优先使用系统原生 Python 3.12 或更新版本，包括 Ubuntu 26.04 的 Python 3.14；安装器使用独立虚拟环境，保留系统 Python。Ubuntu 22.04 x86_64 可以继续使用仓库附带的独立 Python 3.12 运行时；ARM64 需要原生 Python 3.12+。

```bash
git clone https://gitee.com/Zyihan_Crz/xcpc-vp-system.git
cd xcpc-vp-system
bash install.sh
```

上述命令安装到 `/opt/vp-helper`，命令入口位于 `/usr/local/bin`。也可以将程序安装在当前项目文件夹内：

```bash
bash install.sh --local
export PATH="$PWD/bin:$PATH"
vp --help
live --help
submit --help
```

`--local` 将程序安装在项目的 `.vp-install/`，入口放在 `bin/`；不要对这个命令使用 `sudo`。如需在新终端继续使用 `vp` 和 `submit`，将安装器打印的 `export PATH=...` 行添加到 Ubuntu 的 `~/.bashrc`（使用 zsh 时添加到 `~/.zshrc`）。安装后不要移动目录；移动后请重新运行安装器。

随仓库保留的 wheels 包括原始 x86_64/Python 3.12 版本，以及本次准备的 ARM64/Python 3.14 版本。不匹配当前架构或 Python 版本的 wheel 会自动被忽略；缺少兼容依赖时，安装器会联网从 PyPI 安装原生 wheel。使用 `--offline` 可禁止 PyPI 回退；如果缺少 tmux、Python venv 或系统证书，仍需通过 apt 安装这些系统包。`--python /path/to/python3` 可选择其他原生 Python 3.12+。

macOS / Ubuntu 双系统用户需要先将本文件夹或随附安装包放到 Ubuntu 能访问的位置，再切换到 Ubuntu 执行安装。macOS 下不运行此安装器。

## 使用

将 `ID` 替换为对应平台的数字比赛编号：

```text
vp cf ID
vp qoj ID
vp gym ID
```

例如：

```bash
vp qoj 3169
submit A.cpp
vp history
```

也支持 `vp CONTEST_URL`。上述 VP 命令的计分范围限定为 ICPC；普通 Codeforces Round 的 CF / IOI 计分请使用下面的 `live` 模式。

## Codeforces 实时比赛

先安装本版本，在 Ubuntu 的 Firefox 登录已经报名比赛的 Codeforces 账号，然后执行：

```text
live https://codeforces.com/contest/比赛编号
```

`live` 只接入当前账号已有的参赛资格，不报名、不取消报名、不启动虚拟参赛。Codeforces 支持普通 `/contest/` 比赛的 CF、ICPC、IOI 计分；Gym 和私有 Group 比赛暂不属于实时模式支持范围。CWNUOJ 的接入方式见下一节。可在开赛前接入等待，或比赛进行中接入；报名验证失败时会给出提示并退出。开赛前若原站尚未提供可验证的报名信息，可在开赛后再次执行同一命令。实时模式不要求 Codeforces API key/secret，现有 VP/Gym 的配置要求保持不变。

比赛目录位于桌面的 `CF 比赛编号 live`。面板按官方开始时间计时，中途接入、关闭终端、重启后重新执行同一条 `live URL` 命令，都保留比赛文件和已保存的提交记录。原站修改开赛时间后会重新同步计时。题面尽量保存为各题的 HTML；PDF 不作为接入前提，题面下载失败不会阻止使用面板或提交。未公布的题面在公开后再尝试获取。已有同名题面文件不会覆盖。

在比赛目录内执行：

```bash
submit A.cpp
submit solution.cpp --problem A
submit A.cpp --lang 73
```

默认按文件扩展名从本场实际提供的编译器中选择，可用 `--lang` 指定编译器 ID 或唯一匹配的名称。当前目录与源码目录绑定不同比赛时拒绝提交。提交前核对账号、比赛、官方时间和 `CODING` 状态；结束后不会自动当作练习提交。POST 不自动重发，超时或无法确认新提交编号时，请先检查原站 My submissions 或面板，再决定是否重试。服务器确认编号后，本地面板立即显示 `PENDING`。

成绩以原站公开榜单为准：CF/IOI 显示总分与每题分数，ICPC 显示过题数与官方罚时；同时显示官方排名及 Hack 成功/失败数量。CF 预测试通过显示 `PRETESTS-PASSED`，题目暂定分以 `*` 标记；系统测试、Hack 和重测后的降分会继续同步，不按本地 ICPC 规则重算。等待系统测试和系统测试中均不视为最终完赛。封榜时明确标注，保留原站公开榜单的可见结果。

`scored` 为公开正式榜单中该题得分大于 0 的队伍数；`attempted ≥` 为从可见得分和错误次数确认的尝试队伍数下界，不含无法从榜单确认的排队或隐藏尝试。完整提交次数无法由榜单推断，不显示猜测值。公开正式榜单未包含本人时，仍可同步本人提交，但排名与得分显示为未知。不使用虚拟参赛的原赛回放和金银铜线。

本人提交通常约 5 秒查询一次，有待判时约 2.1 秒；公开榜单约 15 秒刷新，多个进程共享每次 API 请求至少 2.1 秒的间隔，网络错误时退避。题面单独下载，不拖延榜单和判定同步。查询会重新读取完整本人提交历史，以发现较早提交的重测结果；大量历史记录或网络延迟会延长刷新时间。原站返回比赛终结状态后，仍需确认本人提交已同步、无待判、榜单未冻结，才记录为完成。

实时比赛历史单独保存到桌面的 `cf_live_history.csv`，包括官方得分、排名、赛制和比赛阶段，不写入原有 `vp_history.csv`。查看命令：

```bash
vp history --live
```

也保留 `vp live cf ID` 和 `vp live CONTEST_URL` 作为等价入口。所有入口均使用同一个实时模式。接口依据：[Codeforces API 方法](https://codeforces.com/apiHelp/methods)、[返回对象](https://codeforces.com/apiHelp/objects)。

## CWNUOJ 实时比赛（HydroOJ）

支持本地部署站点 [CWNUOJ](https://oj.cwnupaa.com)，按其 Hydro 5.0.7 和定制排行榜、记录列表的实际返回适配。安装本版本，在 Ubuntu 的 Firefox 登录 CWNUOJ，先在网站报名，然后执行：

```text
live https://oj.cwnupaa.com/contest/比赛ID
```

比赛 ID 为链接中的 24 位十六进制字符串，不是题目编号。也支持域内比赛链接 `https://oj.cwnupaa.com/d/域名/contest/比赛ID`；`/d/system/contest/ID` 与默认域链接合并为同一场。其他 Hydro 站点暂不自动接入。

配置保存在 `~/.config/vp/config.json` 的 `cwnuoj` 项，与 QOJ、Codeforces 分开；无需 API key 或 secret。默认自动读取 Firefox 中 `oj.cwnupaa.com` 及适用于该站的 `.cwnupaa.com` 登录 Cookie，通过原站自己的 `UserContext` 核对实际用户名和数字 UID，忽略访客身份及页面上其他用户的链接。只需在网站正常登录，不需要在配置中填写账号密码。手动 Cookie 方式仍可用，`handle` 应填写登录后的 CWNUOJ 用户名，而不是登录邮箱；`uid` 会从已验证的原站会话取得。

支持个人 ACM/XCPC、OI、IOI 和 Codeforces 赛制。读取原站报名状态和比赛起止时间，未报名时拒绝接入；不会自动报名、开赛、取消报名或解封榜单。限时参赛场次必须先在 CWNUOJ 开始，再接入已存在的 `startAt`；团队场次暂未支持。重进同一链接会复用桌面比赛目录及本地提交记录，保留代码文件。

排名、总分、过题数、罚时和单题成绩直接采用原站公开成绩表。CWNUOJ 的定制汇总存在时，显示原站提供的 `submitted`、`attempted`、`accepted`；否则只显示能够核实的公开统计。OI 隐藏成绩和封榜不通过管理员接口绕过，尚未开放的结果标记为 `HIDDEN` 或未知，题面下载失败不影响接入。HTML 题面保存在比赛目录，已有文件不覆盖。

比赛目录内提交：

```bash
submit A.cpp
submit solution.cpp --problem A
submit A.cpp --lang cc.cc20o2
submit A.py --lang py.pypy3
```

编译器从本题的 `langRange` 读取，未提供的语言会拒绝。发送前核对当前账号、UID、报名、比赛、题号映射及剩余时间；只向当前比赛对应题目的提交入口发送一次请求，不自动重发 POST，不转为赛后练习。收到原站 24 位提交编号后，再核对记录所属用户、题目及比赛，通知面板显示 `PENDING`。OI 场次可能只确认收件而不公开记录编号，此时保留原站的隐藏结果；请先检查原站记录再决定是否重试。

本站记录列表带有定制的分页和搜索行为。客户端按原站可见权限读取分页列表，以数字 UID 精确筛选本人记录，忽略他人、其他比赛和其他域的提交；用原站 ObjectId 的创建时间计时，重新查询旧提交以同步重测。实时历史保存到 `cwnuoj_live_history.csv`，`vp history --live` 可同时查看 Codeforces 与 CWNUOJ 的历史。原有 VP 历史文件保持独立。

先在 Ubuntu 的 Firefox 登录 QOJ 或 Codeforces，再运行比赛命令。`vp` 和内置 `submit` 自动读取 Firefox 的登录状态，并通过网站导航中的登录用户核对账号；未填写 handle 时也可以从已验证的浏览器登录自动取得。Codeforces / Gym 的 API key 与 API secret 仍在首次使用时配置。配置保存在 `~/.config/vp/config.json`，权限为 600；也可以参考 `config.example.json` 填写。`cf` 与 `gym` 默认共用 Codeforces 配置。

自动读取支持 Firefox 的普通安装及 Ubuntu Snap 目录，读取该网站的持久 Cookie 和最新会话恢复记录；其他网站及容器、隐私会话不参与导入。程序只读取浏览器文件。验证通过后将登录数据保存到本地配置，账号不匹配或网络验证失败时保留原配置。GET 遇到登录过期会重新读取并验证一次；启动 VP 和提交代码的 POST 不自动重发。Firefox 尚未保存新会话时稍候再运行命令，或登录时勾选“记住我”。

登录过期时，在 Firefox 重新登录同一个账号，再运行原命令即可，无需复制 Cookie。多 Firefox 配置可在对应平台账号中指定 `firefox_profile` 为该 profile 的完整路径；sudo 启动时默认使用原普通用户的 Firefox。root 与普通用户的应用配置仍分别保存，root 使用 `/root/.config/vp/config.json`，普通用户使用自己的 `~/.config/vp/config.json`。

已有手动配置也可继续使用。要固定使用手动 Cookie，在账号中设置 `"cookie_source": "manual"`；填写 `cookie` 时只复制请求中 `Cookie` 的值，省略 `Cookie:` 标签和其他请求头。

每场比赛在桌面共用一个目录，例如 `2024 ICPC Nanjing Reg`。再次进入保留已有代码和输入数据，更新提交配置与题面；各次 VP 的开始时间和成绩状态独立保存。`submit A.cpp` 在对应比赛目录内运行。

请用同一个 Linux 用户运行 `vp` 和 `submit`；root 与普通用户的桌面目录和 VP 状态互相独立。QOJ 提交器会在当前目录、源码所在目录及其上级目录查找 `contest.json`，因此也支持从子目录提交或在别处指定源码路径。当前目录与源码绑定不同比赛、账号或题号时会要求切换到源码的比赛目录；同场同账号的配置以当前目录为准。找不到配置时给出恢复指引。重新运行同一场 `vp` 命令会补齐或刷新提交配置，沿用已经确认的原站开始时间。

## 成绩与界面

- `vp` 准备题面与提交配置后，在现有账号上启动并验证原站 VP，以平台开始时间计时。
- QOJ 启动请求只发送一次；登记结果暂未返回时会短暂重查。请求超时也会先核对原站记录，确认成功后直接沿用原站开始时间。网站拒绝启动时显示它返回的提示；仍无法确认时，可再次运行同一场命令检查已有登记。
- 按正式参赛资格计算排名，排除打星队与其他 VP；解题数和罚时相同共享名次，最后 AC 用时只决定展示顺序。
- 奖项采用原赛明确配置。资格、规则或榜单完整性无法验证时，保留未知结果。
- `rank` 列保持在成绩表左侧，排名和奖项在同一格内分两行显示，例如上行 `=47`、下行 `Silver Prize`。
- 内置 submit 在服务器确认新提交编号后立即通知面板显示 `PENDING`，随后用平台判定替换；本人提交与榜单查询独立执行。
- QOJ 的 0 分提交会读取详情区的测试点判定，区分 WA、CE、RE 等结果；前面通过的测试点不会覆盖后面的失败。详情暂时无法读取时显示 `RESULT UNAVAILABLE`，按退避间隔重查，并继续更新其他提交。影响罚时的结果未确认时，罚时与排名暂不显示确定值。
- 提交记录按时间从新到旧完整展开，`practice` 标注紧贴判定。5 小时后的补题不计入比赛成绩，未通过显示橙色，通过显示紫色。
- 空白、标题与提交列表继承终端默认背景，支持终端自身的透明效果。
- 最终成绩保存到桌面的 `vp_history.csv`。
- `vp history` 增加 `gold`、`silver`、`bronze` 三列，显示原赛正式队终榜的金、银、铜线。每格上行是过题数/罚时，下行是名次界限，例如 `9/1200` 与 `(≤30)`；并列成绩使用相同分界，打星队不计入奖线。比赛完成后保存奖线，旧记录从本地 VP 状态和已核验的原赛缓存补齐。只有奖项名额时过题数/罚时显示 `—`，资格或数据缺失时整格显示 `—`。历史查询不联网，也不改写原有 CSV。

在上方面板用方向键、`PgUp` / `PgDn`、`Home` / `End` 或鼠标滚轮查看提交。`Ctrl+B`、`D` 脱离 tmux 后继续同步；再次运行同一场比赛命令可恢复。按 `q` 关闭评分面板。

如果终端关闭或重启导致 tmux 会话消失，再次运行同一场比赛命令也会重建面板，沿用本地记录中的原始开始时间、比赛目录及提交记录，不会重新向原站申请 VP。比赛计时不会因面板关闭而暂停。此恢复针对 `current.json` 指向的比赛记录。

已安装的 0.1.15 如果在同场比赛恢复时提示 `A native VP is already running`，可使用该安装的 Python 运行随仓库提供的修复脚本（以原虚拟环境路径为例）：

```bash
~/Program/VP/VP/bin/python ~/Program/VP/tools/repair-vp.py
vp qoj 4538
```

脚本自动定位该 Python 安装的 `vp_helper/cli.py` 和 `tui.py`，检查两个文件均兼容后，先在原文件旁创建备份，再修复恢复条件和 CCPC 匿名统计显示；不修改比赛状态、选手代码、提交记录或历史 CSV，无需重新安装。若安装路径不同，请替换第一行的 Python 路径；脚本路径应指向本仓库中的文件。请使用原先参赛的普通用户执行。

QOJ 已完成真实账号联调，包括南京与香港的正式排名、奖项和提交记录。CF / Gym 的账号流程需要已有登录会话和 API 凭据；请按实际平台状态核实。

## 赛时排名与统计

首次运行比赛命令时，程序根据平台 ID 读取比赛信息，自动获取该场原赛资格、榜单和提交记录。有完整原赛提交记录时，先用它重算终榜，与平台每支队伍的每题结果和罚时核对，再按 VP 当前时间每秒回放原赛成绩，计算当前排名与统计。QOJ 使用 XCPCIO 或 SRK 原赛记录；CF 原生参赛队伍使用官方 API 提交记录，导入 ghost 仍须匹配原赛资格和记录。

有待判提交时，在上一轮查询返回后约 2 秒再次查询本人判定；其余时间约 5 秒，网络故障时退避重试。平台榜单约 30 秒重新核对；这些间隔不包含网络耗时。CF 请求统一遵守 API 限速。

QOJ 在 VP 进行中返回的账号榜单已经按当前时间截断，程序使用不携带登录凭据的独立会话读取公开完整终榜来校验回放。档案时间窗口或 QOJ 导入时间与原站 VP 有差异时，分别处理；只有统一时间偏移后的全部终榜结果一致，才接受时间对齐。本人计时和补题分界仍按 QOJ 原站的 VP 时长。导出记录中的管理员测试提交不属于参赛队伍，不计入回放。正式与打星队伍的每题结果仍须和原站终榜逐一一致，才会显示实时排名与统计。

- `submitted`：首次通过前的有效计分提交次数，含待判。
- `attempted`：有有效尝试的正式队伍数。
- `accepted`：通过的正式队伍数。

统计包含本人赛内提交，排除打星和赛后补题；编译错误按本场计分规则处理。封榜时公开统计隐藏冻结后的结果，rank 显示 `?`，当前 VP 的原站解封后恢复。缺少资格数据或完整事件记录时，面板显示具体原因；只有终榜时，赛时排名与统计保持 `—`。奖项只使用明确的原赛奖牌配置。

2026 年起的 CCPC 区域赛自动启用题号揭示：某题被 50 支不同的原赛队伍通过后，才在公开统计中显示题号，正式队和打星队一并计数，当前 VP 不参与这个阈值。排名、奖项和上面的三项公开统计仍按正式参赛资格计算。尚未揭示的列只将题号显示为 `?`，仍显示提交次数（submitted）、尝试队伍数（attempted）和通过队伍数（accepted）。已揭示的列按题号排列在前，匿名列按通过、尝试、提交数量降序排列在后，避免从 A–M 的位置直接推断题号；数据不完整时显示 `—`。本人每题成绩与提交记录仍按实际题号显示。到原站规定的封榜时刻，所有题号同时揭示，公开统计固定在封榜前，原站解封后恢复完整结果。

例如 `vp qoj 4582` 会识别为 `2026 CCPC Changchun Reg`，采用 5 小时赛长、4 小时封榜。比赛名称中的 `Site` 及中英文双语标题均可识别；其他人的较短 rated VP 时长不再覆盖公开比赛时长。QOJ 目前仍以传统 ICPC 规则运行该场原站 VP，题号揭示由本地面板模拟。

## 数据与第三方组件

仓库和安装包不包含任何比赛数据。首次使用时自动获取，可识别的 ICPC / CCPC 区域赛从 [XCPCIO board-data](https://github.com/xcpcio/board-data) 读取正式参赛资格、奖牌配置和原赛提交记录，按学校、队名和成员身份与平台榜单匹配。资格校验通过、提交记录复算与终榜一致后，才保存相应缓存。

XCPCIO 尚未收录的 CCPC 区域赛，会尝试 [SRK Collection](https://github.com/algoux/srk-collection) 的公开原赛档案。SRK 档案须明确标记正式参赛资格，且题目链接与当前 QOJ 比赛的全部题目 ID 一致；提交记录须逐题复算并与 QOJ 公开终榜匹配，才启用赛时回放。学校、队名、成员等身份信息完全一致且仅有一份非空记录的换账号空记录会被排除；存在多份实际参赛记录时保留校验失败提示。

生成的数据保存在用户自己的 `~/.cache/vp/originals/`（设置 `XDG_CACHE_HOME` 时使用 `$XDG_CACHE_HOME/vp/originals/`），后续使用复用缓存。缓存可以删除，下次使用会重新获取；首次获取需要网络。不需要修改仓库或手动添加比赛 JSON 文件。无法取得完整可靠原赛数据的比赛，仍显示本人成绩和提交，并说明参考排名或统计缺失的原因。XCPCIO 的 MIT 许可证保留在 `third_party/xcpcio/LICENSE`。

SRK Collection 的档案按其 AGPL-3.0 许可证提供，来源和许可证保留在 `third_party/srk/`；仓库仅包含独立编写的档案读取器，不内置 SRK 比赛数据或组件代码。

提交器来自 [hikariyo 的 QOJ submit.py](https://gist.github.com/hikariyo/b3cec2e736d87be57d1e4faafb04f908) 与 [ehnryx/cf_submit](https://github.com/ehnryx/cf_submit)，固定版本、兼容补丁与现有许可证保存在 `third_party/`。Python 运行时的来源与校验值见 `runtimes/manifest.json`。

本仓库包含运行程序、安装依赖、使用文档和隔离的自动化回归测试；比赛数据、账号配置、验证输出及个人比赛文件不纳入仓库。安装依赖后可运行 `python -m unittest discover -s tests -v`；真实 tmux 恢复测试需要 Ubuntu 和 tmux。测试中的账号、Cookie、题目和提交结果均为合成数据，不会发起真实比赛报名或代码提交。
