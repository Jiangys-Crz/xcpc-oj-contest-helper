# VP Helper

Ubuntu 上的比赛终端助手，支持 QOJ、Codeforces ICPC 虚拟参赛、Codeforces Gym，以及 Codeforces、CWNUOJ、牛客正式比赛实时接入，并提供各站补题工作区。上方显示成绩与完整提交记录，下方保留比赛目录中的普通 shell。

当前版本：0.7.0。

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
xcpc --help
```

`--local` 将程序安装在项目的 `.vp-install/`，入口放在 `bin/`；不要对这个命令使用 `sudo`。如需在新终端继续使用 `vp` 和 `submit`，将安装器打印的 `export PATH=...` 行添加到 Ubuntu 的 `~/.bashrc`（使用 zsh 时添加到 `~/.zshrc`）。安装后不要移动目录；移动后请重新运行安装器。

随仓库保留的 wheels 包括原始 x86_64/Python 3.12 版本，以及本次准备的 ARM64/Python 3.14 版本。不匹配当前架构或 Python 版本的 wheel 会自动被忽略；缺少兼容依赖时，安装器会联网从 PyPI 安装原生 wheel。使用 `--offline` 可禁止 PyPI 回退；如果缺少 tmux、Python venv 或系统证书，仍需通过 apt 安装这些系统包。`--python /path/to/python3` 可选择其他原生 Python 3.12+。

macOS / Ubuntu 双系统用户需要先将本文件夹或随附安装包放到 Ubuntu 能访问的位置，再切换到 Ubuntu 执行安装。macOS 下不运行此安装器。

## 更新

安装本版本后，以后可直接运行：

```bash
xcpc update
```

程序从本项目 Gitee 的 `main` 分支获取最新版本，并沿用原来的本地或全局安装位置、Python 和自定义提交器设置。全局安装需要时由安装器请求 sudo 权限。更新使用独立缓存，不覆盖原项目中未提交的修改；账号、保存目录、比赛代码和历史记录保留。新版本安装检查成功后才切换，旧版本保留以便恢复。缺少 Git 时会尝试安装 Git；Python 依赖优先使用随附 wheels，必要时联网补齐。

第一次升级到包含 `xcpc update` 的版本，仍需要先按原安装方式更新一次：在项目目录执行 `git pull --ff-only` 后，本地安装运行 `bash install.sh --local`，全局安装运行 `bash install.sh`。从压缩包安装、没有 Git 工作区的用户，先下载新版覆盖程序源文件并重新安装一次；后续 `xcpc update` 不要求原目录含有 Git 工作区。

## 帮助和保存目录

统一帮助入口：

```bash
xcpc --help
```

新建比赛目录和历史 CSV 默认保存在桌面，可以改为任意可写文件夹：

```bash
xcpc path                 # 显示当前目录，并提示输入新目录；回车保留设置
xcpc path ~/XCPC          # 直接指定目录；不存在时自动创建
```

路径含空格时请加引号。设置保存到 `~/.config/vp/config.json` 的全局 `workspace_directory` 字段，VP、live 和 bt 共用，后续启动自动生效。原有账号和 Firefox 设置保留。

修改只影响新建工作区及其历史 CSV；已有轮次的目录和 CSV 保留在原位置，仍可通过 `vp history` 查询或恢复，重开 live / bt 也会使用其原有目录。已保存的源代码不会自动搬移。配置和运行状态仍保存在原先的 XDG 用户目录中。

## 使用

启动比赛保留以下入口，`paa` 表示 CWNUOJ，`nk` 表示牛客：

```text
vp cf ID
vp qoj ID
vp gym ID
vp CONTEST_URL
live cf ID
live paa ID
live nk ID
live CONTEST_URL
bt CONTEST_URL
```

CF、QOJ、Gym 的 ID 为数字；CWNUOJ 的 ID 为链接中的 24 位比赛 ID。有域路径的 CWNUOJ 比赛使用完整链接。`vp` 的计分范围仍限定 ICPC；`live` 按原站赛制显示。

### VP 开始时间与多轮记录

执行 `vp` 后会提示开始时间，默认时区为 `Asia/Shanghai`（可通过 `TZ` 修改）：

- 回车或 `now`：立即准备开始。
- `20:30` 或 `20:30:15`：下一次到达该时刻时开始；当天已过则安排到次日。
- `2026-11-01 20:30`：指定未来日期和时间，也支持带时区的 ISO 时间。

每次 VP 都核对并使用原站确认的新开始时间。Codeforces / Gym 自动填写原站虚拟参赛表单的开始时间与时区，提交报名并核验返回的开始时间；分钟精度的表单向上取整到下一分钟。QOJ 在倒计时到点后，由后台监控自动读取登录状态并开始原生 VP。无需再到网页手动点击开始。启动请求超时后只查询是否已成功，不重复发送 POST。

再次执行 `vp` 会请求新建原站 VP，只有原站允许并确认新的开始时间，才切换为有效的新轮次。QOJ 不支持再次 VP、原站已有未结束的 VP、缺少报名表单或明确拒绝启动时，本次命令报错退出，不启动本地计时、不打开比赛面板，当前轮次与历史记录保持原状。失败请求如已创建准备目录，会保留失败原因用于查询。不会自动退回本地模拟 VP。

已结束、已中断和补题记录保留。新轮次有独立 ID、代码目录和开始时间；旧轮次仅在新轮的原站开始时间停止计分。旧目录继续提交的记录归入旧轮补题并显示蓝色，不进入新轮成绩。被替换、尚未开始的 QOJ 预约不会在稍后意外启动。

手动结束本地 VP：

```bash
xcpc end                # 结束当前本地 VP
xcpc end 轮次ID         # 结束指定本地 VP
```

只停止本地计时和计分，不向原站发送结束或取消请求。代码、成绩和历史提交保留；已开始的轮次仍可在原目录提交，结束后的提交显示为蓝色补题。尚未开始的本地预约会取消，不再到点自动申请 VP；CF/Gym 已在原站预约的状态不作修改。`xcpc end` 不结束正式实时比赛或补题工作区。

等待期间请保持 Ubuntu 开机联网，后台任务运行在 tmux 中。脱离面板不会暂停计时；重启系统后可通过历史轮次恢复监控。

```bash
vp history                       # 所有 VP、实时比赛与补题工作区
vp history 轮次ID                 # 查看该轮成绩、代码目录、完整提交及补题记录
vp history 轮次ID --open          # 恢复该轮面板；不新建 VP
vp history --live                # 只看实时比赛
```

历史列表读取本地保存内容；查看具体轮次时会尝试同步原站判定，登录或网络不可用时保留并显示缓存记录。原有 CSV 不删除。

### 补题

比赛或 VP 结束后，可在原目录继续使用 `submit A.cpp`。所有补题判定（含待判和未通过）统一显示蓝色，不影响比赛正式成绩。

也可以直接为任一支持站点创建补题目录，无需先在本程序启动过比赛：

```bash
bt https://codeforces.com/contest/比赛编号
bt https://codeforces.com/gym/比赛编号
bt https://qoj.ac/contest/比赛编号
bt https://oj.cwnupaa.com/contest/比赛ID
bt https://ac.nowcoder.com/acm/contest/比赛编号
```

`bt` 复用该账号、该比赛已有的补题工作区，使用原站允许的补题权限；不自动报名。CWNUOJ 使用比赛内的补题提交入口，保留 `tid`，记录仍关联该比赛。Codeforces 账号若仍有该场未结束的原生 VP，需在其结束后使用 `bt`，防止补题改变那场原生 VP。其他平台也由原站决定题目和补题提交权限。

在工作区中提交代码，或明确指定该目录中的源文件。统一 `submit` 根据源文件绑定的比赛和轮次选择提交器，切换平台后旧目录仍可使用。不同轮次配置冲突时会拒绝提交。题面下载是可选步骤，PDF / HTML 下载失败不阻止看板、提交与补题。

## Codeforces 实时比赛

先安装本版本，在 Ubuntu 的 Firefox 登录已经报名比赛的 Codeforces 账号，然后执行：

```text
live https://codeforces.com/contest/比赛编号
```

`live` 只接入当前账号已有的参赛资格，不报名、不取消报名、不启动虚拟参赛。Codeforces 支持普通 `/contest/` 比赛的 CF、ICPC、IOI 计分；Gym 和私有 Group 比赛暂不属于实时模式支持范围。CWNUOJ 的接入方式见下一节。可在开赛前接入等待，或比赛进行中接入；报名验证失败时会给出提示并退出。开赛前若原站尚未提供可验证的报名信息，可在开赛后再次执行同一命令。实时模式不要求 Codeforces API key/secret，现有 VP/Gym 的配置要求保持不变。

比赛目录位于保存目录（默认桌面）下的 `CF 比赛编号 live`。面板按官方开始时间计时，中途接入、关闭终端、重启后重新执行同一条 `live URL` 命令，都保留比赛文件和已保存的提交记录。原站修改开赛时间后会重新同步计时。题面尽量保存为各题的 HTML；PDF 不作为接入前提，题面下载失败不会阻止使用面板或提交。未公布的题面在公开后再尝试获取。已有同名题面文件不会覆盖。

在比赛目录内执行：

```bash
submit A.cpp
submit solution.cpp --problem A
submit A.cpp --lang 73
```

默认按文件扩展名从本场实际提供的编译器中选择，可用 `--lang` 指定编译器 ID 或唯一匹配的名称。当前目录与源码目录绑定不同比赛时拒绝提交。提交前核对账号、比赛和官方时间；开赛前拒绝提交，比赛结束后使用原站补题表单，记录标为蓝色补题。POST 不自动重发，超时或无法确认新提交编号时，请先检查原站 My submissions 或面板，再决定是否重试。服务器确认编号后，本地面板立即显示 `PENDING`。

成绩以原站公开榜单为准：CF/IOI 显示总分与每题分数，ICPC 显示过题数与官方罚时；同时显示官方排名及 Hack 成功/失败数量。CF 预测试通过显示 `PRETESTS-PASSED`，题目暂定分以 `*` 标记；系统测试、Hack 和重测后的降分会继续同步，不按本地 ICPC 规则重算。等待系统测试和系统测试中均不视为最终完赛。封榜时明确标注，保留原站公开榜单的可见结果。

`scored` 为公开正式榜单中该题得分大于 0 的队伍数；`attempted ≥` 为从可见得分和错误次数确认的尝试队伍数下界，不含无法从榜单确认的排队或隐藏尝试。完整提交次数无法由榜单推断，不显示猜测值。公开正式榜单未包含本人时，仍可同步本人提交，但排名与得分显示为未知。不使用虚拟参赛的原赛回放和金银铜线。

本人提交通常约 5 秒查询一次，有待判时约 2.1 秒；公开榜单约 15 秒刷新，多个进程共享每次 API 请求至少 2.1 秒的间隔，网络错误时退避。题面单独下载，不拖延榜单和判定同步。查询会重新读取完整本人提交历史，以发现较早提交的重测结果；大量历史记录或网络延迟会延长刷新时间。原站返回比赛终结状态后，仍需确认本人提交已同步、无待判、榜单未冻结，才记录为完成。

实时比赛历史单独保存到所选目录的 `cf_live_history.csv`，包括官方得分、排名、赛制和比赛阶段，不写入原有 `vp_history.csv`。查看命令：

```bash
vp history --live
```

实时参赛仅使用独立 `live` 入口，不再提供 `vp live` 别名。接口依据：[Codeforces API 方法](https://codeforces.com/apiHelp/methods)、[返回对象](https://codeforces.com/apiHelp/objects)。

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

## 牛客实时比赛

安装本版本，在 Ubuntu 的 Firefox 登录 [牛客竞赛](https://ac.nowcoder.com)，并在网站完成比赛报名，然后执行：

```bash
live https://ac.nowcoder.com/acm/contest/比赛编号
```

登录沿用本项目的 Firefox Cookie 读取与验证流程，同时读取适用于竞赛站的 `.nowcoder.com` Cookie；配置单独保存在 `nowcoder` 项。程序根据原站登录上下文核对数字 UID，自动填写 `handle` 和 `uid`。无需填写手机号、密码或 API key。手动 Cookie 配置仍可用，此时 `handle` 填写数字 UID，而不是昵称或手机号。

支持已报名的个人 ACM、OI、IOI、周赛，按原站起止时间计时；允许开赛前等待及中途接入。不会自动报名或解锁比赛；团队赛、重现赛暂未支持。重新执行同一链接会复用目录、代码和本地提交记录。题面作为可选 HTML 下载，失败时仍可看榜和提交。

分数、排名、罚时和单题成绩来自原站榜单，OI 隐藏成绩及封榜按网站权限显示。分页读取题目与本人提交记录，使用 UID 精确筛选、持续同步重测，赛后提交标记为补题。解封后的最终榜单必须由原站确认已启用 `onlyContestRank`，避免把补题成绩当作正式比赛成绩；无法核实时显示未知。牛客历史保存为所选目录的 `nowcoder_live_history.csv`，统一通过 `vp history --live` 查看。

比赛目录中提交：

```bash
submit A.cpp
submit A.cpp --lang 38     # C++ g++ 13；默认 2：clang++18
submit B.py               # Python3
submit B.py --lang 25      # PyPy3
submit solution.cpp --problem A
```

提交请求参考 [vscode-nowcoderac](https://github.com/Handy2233/vscode-nowcoderac/blob/master/src/services/nowcoderService.ts) 并与牛客当前页面核对：从题目页面读取 `questionId`、`tagId`、`subTagId`、`doneQuestionId`，以 URL 编码表单向 `/nccommon/submit_cd` 发送代码、语言 ID 和语言名，原站返回提交 ID 后通知面板。登录不采用参考项目的账号密码流程，继续使用 Firefox Cookie。支持普通标准输入输出题的 C/C++、Java、Python3/PyPy3、Go、Rust、Kotlin；需要特殊模板的题目提示转到网站提交。验证码、会话过期、网络超时或未返回提交 ID 时不自动重发，请先在 Firefox 完成验证并检查原站记录。

测试覆盖分页、计分与封榜、正式榜/补题区分、Firefox 父域 Cookie 与身份验证、目录恢复、表单字段/编译器和单次提交行为。公开接口已联网核对；登录后的完整比赛流程及真实代码提交仍需在已登录 Firefox、已有参赛资格的比赛中验证。

先在 Ubuntu 的 Firefox 登录 QOJ 或 Codeforces，再运行比赛命令。`vp`、`bt` 和内置 `submit` 自动读取 Firefox 的登录状态，并通过网站导航中的登录用户核对账号；未填写 handle 时也可以从已验证的浏览器登录自动取得。Codeforces / Gym 的 API key 与 API secret 仍在首次使用时配置。配置保存在 `~/.config/vp/config.json`，权限为 600；也可以参考 `config.example.json` 填写。`cf` 与 `gym` 默认共用 Codeforces 配置。

自动读取支持 Firefox 的传统目录、XDG 配置目录，以及 Ubuntu Snap / Flatpak 的新旧配置位置；可从 profiles.ini 和直接保存 Cookie 的配置文件夹发现登录资料。读取该网站的持久 Cookie 和最新会话恢复记录；其他网站及容器、隐私会话不参与导入。程序只读取浏览器文件。VP 与 BT 共用登录加载和账号核验；Firefox 暂未读到可用 Cookie 时，也会验证本项目已保存的同账号会话。验证通过后将登录数据保存到本地配置，账号不匹配或网络验证失败时保留原配置。GET 遇到登录过期会重新读取并验证一次；启动 VP 和提交代码的 POST 不自动重发。Firefox 尚未保存新会话时稍候再运行命令，或登录时勾选“记住我”。

登录过期时，在 Firefox 重新登录同一个账号后，通过历史轮次恢复已有 VP；live、bt 可重新执行原命令。无需复制 Cookie。多 Firefox 配置可在对应平台账号中指定 `firefox_profile` 为该 profile 的完整路径；sudo 启动时默认使用原普通用户的 Firefox。root 与普通用户的应用配置仍分别保存，root 使用 `/root/.config/vp/config.json`，普通用户使用自己的 `~/.config/vp/config.json`。

已有手动配置也可继续使用。要固定使用手动 Cookie，在账号中设置 `"cookie_source": "manual"`；填写 `cookie` 时只复制请求中 `Cookie` 的值，省略 `Cookie:` 标签和其他请求头。

每个 VP 轮次在设置的保存目录中有独立代码文件夹，例如 `2024 ICPC Nanjing Reg VP1-轮次ID`。历史恢复保留已有代码和输入数据；重开 live / bt 复用其原有目录。各次 VP 的开始时间和成绩状态独立保存。`submit A.cpp` 在对应比赛目录内运行。

### Firefox 已登录但程序未识别

先执行只读检查；输出不含 Cookie 值或密码，可以提供检查结果定位原因：

```bash
xcpc doctor paa
xcpc doctor nk
```

也支持 `cf`、`gym`、`qoj`。检查会区分配置目录未发现、数据库无法读取、未读到该站 Cookie、账号与配置不匹配以及网站验证失败。Firefox 154 的 Snap 配置位于 `~/snap/firefox/common/.mozilla/firefox/` 时也会检查该目录下的实际配置文件夹；旧缓存位置不会挡住同一账号迁移后的新位置。若需要严格固定某个配置文件夹，可在账号配置中同时设置 `firefox_profile` 与 `firefox_profile_strict: true`。

普通窗口以外的隐私或容器 Cookie 不会与普通登录混用。若检查提示没有持久 Cookie，可退出 Firefox 后重试，让登录状态保存到磁盘。账号识别仍通过原站返回的登录用户核对。

请用同一个 Linux 用户运行 `vp` 和 `submit`；root 与普通用户的桌面目录和 VP 状态互相独立。QOJ 提交器会在当前目录、源码所在目录及其上级目录查找 `contest.json`，因此也支持从子目录提交或在别处指定源码路径。当前目录与源码绑定不同比赛、账号或题号时会要求切换到源码的比赛目录；同场同账号但不同轮次的配置同样会拒绝混用。找不到配置时给出恢复指引。重新运行同一场 `vp` 会新建轮次；恢复已有轮次使用 `vp history 轮次ID --open`。

## 成绩与界面

- `vp` 准备题面与提交配置后，在现有账号上启动并验证原站 VP，以平台开始时间计时。
- QOJ 启动请求只发送一次；登记结果暂未返回时会短暂重查。请求超时也会先核对原站记录，确认成功后直接沿用原站开始时间。网站拒绝启动时显示它返回的提示；仍无法确认时，使用 `vp history 轮次ID --open` 继续查询；新的 `vp` 命令会提示先处理结果未确认的轮次。
- 按正式参赛资格计算排名，排除打星队与其他 VP；解题数和罚时相同共享名次，最后 AC 用时只决定展示顺序。
- 奖项采用原赛明确配置。资格、规则或榜单完整性无法验证时，保留未知结果。
- `rank` 列保持在成绩表左侧，排名和奖项在同一格内分两行显示，例如上行 `=47`、下行 `Silver Prize`。
- 内置 submit 在服务器确认新提交编号后立即通知面板显示 `PENDING`，随后用平台判定替换；本人提交与榜单查询独立执行。
- QOJ 的 0 分提交会读取详情区的测试点判定，区分 WA、CE、RE 等结果；前面通过的测试点不会覆盖后面的失败。详情暂时无法读取时显示 `RESULT UNAVAILABLE`，按退避间隔重查，并继续更新其他提交。影响罚时的结果未确认时，罚时与排名暂不显示确定值。
- 提交记录按时间从新到旧完整展开，`practice` 标注紧贴判定。5 小时后的补题不计入比赛成绩，所有补题判定统一显示蓝色。
- 空白、标题与提交列表继承终端默认背景，支持终端自身的透明效果。
- 最终成绩保存到桌面的 `vp_history.csv`。
- `vp history 轮次ID` 显示已保存的原赛正式队终榜金、银、铜线。每格上行是过题数/罚时，下行是名次界限，例如 `9/1200` 与 `(≤30)`；并列成绩使用相同分界，打星队不计入奖线。比赛完成后保存奖线，旧记录从本地 VP 状态和已核验的原赛缓存补齐。只有奖项名额时过题数/罚时显示 `—`，资格或数据缺失时整格显示 `—`。轮次成绩与奖线保存在独立状态文件中，补题不会改写正式赛内成绩。

在上方面板用方向键、`PgUp` / `PgDn`、`Home` / `End` 或鼠标滚轮查看提交。`Ctrl+B`、`D` 脱离 tmux 后继续同步；`live` 和 `bt` 可再次运行原命令恢复；VP 使用 `vp history 轮次ID --open` 恢复。按 `q` 关闭评分面板。

如果终端关闭或重启导致 tmux 会话消失，使用 `vp history 轮次ID --open` 重建已有 VP 面板，保留其开始时间、目录及提交记录，不重新报名。`vp` 启动命令始终表示新建轮次。

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
