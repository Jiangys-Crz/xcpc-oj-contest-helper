/** Public tutorial content. All names, paths and contest IDs below are examples. */
export type Platform = 'ubuntu' | 'macos' | 'windows';

export type PlatformGuide = {
  label: string;
  terminal: string;
  installCommand: string;
  programPath: string;
  commandPath: string;
  configPath: string;
  statePath: string;
  extensionPath?: string;
  workspaceExample: string;
  folderCdExample: string;
  createFileCommand: string;
  setupNotes: string[];
};

export type GuideCommand = {
  label: string;
  command: string | Record<Platform, string>;
  note?: string;
};

export type GuideSection = {
  id: string;
  title: string;
  intro?: string;
  paragraphs?: string[];
  steps?: string[];
  commands?: GuideCommand[];
  platformNotes?: Partial<Record<Platform, string[]>>;
  image?: 'browser' | 'folders' | 'terminal' | 'rooms' | 'firefox';
  imageCaption?: string;
  table?: { headers: string[]; rows: string[][] };
};

export const platformGuides: Record<Platform, PlatformGuide> = {
  ubuntu: {
    label: 'Ubuntu',
    terminal: '从应用列表打开“终端”，或按 Ctrl + Alt + T。使用平时登录 Firefox 的普通 Linux 用户。',
    installCommand: 'curl -fsSL https://vp.cwnupaa.com/install.sh | bash',
    programPath: '/opt/vp-helper',
    commandPath: '/usr/local/bin',
    configPath: '~/.config/vp',
    statePath: '~/.local/state/vp',
    workspaceExample: '/home/player/XCPC',
    folderCdExample: 'cd "$HOME/XCPC/示例训练赛"',
    createFileCommand: 'touch A.cpp',
    setupNotes: [
      '安装脚本会检查并补齐 Python 3.12+、venv、Git、tmux 和证书等运行依赖，按提示输入 sudo 密码即可。输入密码时终端通常不显示字符。',
      '命令安装到系统公共目录，其他电脑用户也能运行；每个人仍分别保存自己的账号、浏览器登录和比赛记录。',
      '日常使用 vp、live、bt、submit、xcpc 等命令时不要加 sudo。root 读取的是 root 的配置，无法直接使用普通用户的 Firefox 登录。',
      '比赛界面使用 tmux 分屏；浏览器继续使用普通 Firefox，不需要 Chrome / Edge 扩展，也不需要为了读登录状态关闭 Firefox。',
    ],
  },
  macos: {
    label: 'macOS',
    terminal: '按 Command + Space，搜索“终端”并打开；也可从“应用程序 → 实用工具 → 终端”进入。',
    installCommand: 'curl -fsSL https://vp.cwnupaa.com/install.sh | bash',
    programPath: '/Library/Application Support/XCPC/app',
    commandPath: '/usr/local/bin',
    configPath: '~/Library/Application Support/XCPC',
    statePath: '~/Library/Application Support/XCPC/State',
    extensionPath: '~/Library/Application Support/XCPC/browser-extension',
    workspaceExample: '/Users/player/XCPC',
    folderCdExample: 'cd "$HOME/XCPC/示例训练赛"',
    createFileCommand: 'touch A.cpp',
    setupNotes: [
      '脚本会检查 Python 3.12+、Git、tmux；缺少时通过 Homebrew 补齐，尚无 Homebrew 时按安装器提示完成安装。',
      '安装公共程序时会要求本机管理员密码；输入密码时不显示字符是正常现象。后续日常比赛仍使用平时的终端和浏览器账号。',
      '程序使用公共目录内的运行环境，不需要退出 Conda 再开始安装，也不需要填写浏览器可执行文件路径。',
      '比赛界面使用 tmux 分屏。原站登录通过平时使用的 Chrome / Edge 配置中的 XCPC 扩展同步，每个 macOS 用户单独加载扩展。',
    ],
  },
  windows: {
    label: 'Windows',
    terminal: '从开始菜单打开 PowerShell 或 Windows Terminal 中的 PowerShell 标签页。直接使用平时的用户环境，Conda 的 (base) 环境也可。',
    installCommand: 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://vp.cwnupaa.com/install.ps1 | iex"',
    programPath: '%ProgramFiles%\\XCPC\\app',
    commandPath: '%ProgramFiles%\\XCPC\\bin',
    configPath: '%APPDATA%\\XCPC',
    statePath: '%LOCALAPPDATA%\\XCPC\\State',
    extensionPath: '%APPDATA%\\XCPC\\browser-extension',
    workspaceExample: 'D:\\XCPC',
    folderCdExample: 'Set-Location -LiteralPath "D:\\XCPC\\示例训练赛"',
    createFileCommand: 'New-Item -ItemType File -Path .\\A.cpp',
    setupNotes: [
      '支持 Windows 10 / 11 与 Windows PowerShell 5.1 或更新版本。脚本会补齐系统 Python 3.12+ 和公共 Git；Windows Terminal 为可选软件，不会自动安装，也不会因缺少它而失败。',
      '缺少公共 Python 时，优先从本站国内服务器获取官方 Python 3.13 安装包，校验 SHA256 和 Python Software Foundation 数字签名后安装；下载或校验失败时回退 WinGet。',
      '从普通 PowerShell 启动即可；安装器会自动弹出 UAC 管理员授权，为所有电脑用户安装，再回到原用户配置浏览器连接。不必另开管理员窗口；已经在管理员 PowerShell 中时也可安装。',
      '新安装的系统 Python 和机器注册的公共 Git 可在同一次安装中直接识别，无需先退出 Conda 或重开窗口才能继续安装。执行策略 Bypass 只用于这次安装进程。',
      '比赛详情在当前终端显示：已有 Windows Terminal 会话可同窗口上下分屏，普通控制台按 q 返回原命令行，不会新开终端窗口；不需要 WSL 或 tmux。原站登录使用普通 Chrome / Edge 中的 XCPC 扩展。',
      '安装失败时原窗口会显示具体错误与日志路径。日志位于 %LOCALAPPDATA%\\XCPC\\Logs\\install-日期时间-编号\\setup.log，管理员窗口关闭后仍可查看。',
    ],
  },
};

/** Short, copy-and-follow instructions; full reference stays below. */
export const quickStartGuides: Record<Platform, {
  openTerminal: string;
  authorization: string;
  browserSteps: string[];
  folderCommand: string;
}> = {
  ubuntu: {
    openTerminal: '按 Ctrl + Alt + T 打开“终端”。',
    authorization: '按提示输入电脑密码并回车。输入密码时屏幕不显示字符是正常的，等待安装完成即可。',
    browserSteps: ['用普通 Firefox 窗口登录你要参加比赛的网站。', '保持 Firefox 登录即可，程序会自动读取；不需要装扩展，也不用关闭浏览器。'],
    folderCommand: 'xcpc path "$HOME/XCPC"',
  },
  macos: {
    openTerminal: '按 Command + Space，搜索“终端”并打开。',
    authorization: '按提示输入电脑的管理员密码并确认，等待安装完成。',
    browserSteps: [
      'Chrome 地址栏输入 chrome://extensions；Edge 输入 edge://extensions。',
      '开启“开发者模式”，点击“加载已解压的扩展程序”。',
      '选择下面的扩展文件夹。文件选择框可按 Command + Shift + G，粘贴路径后回车。',
      '在同一个浏览器普通窗口登录比赛网站，再点 XCPC 扩展的“同步登录状态”。看到“已连接本机 XCPC”即可。',
    ],
    folderCommand: 'xcpc path "$HOME/XCPC"',
  },
  windows: {
    openTerminal: '点击开始菜单，搜索并打开“PowerShell”。',
    authorization: '出现管理员授权时点击“是”，不要关闭正在安装的窗口。等待安装完成后，回到原窗口即可。',
    browserSteps: [
      'Chrome 地址栏输入 chrome://extensions；Edge 输入 edge://extensions。',
      '开启“开发者模式”，点击“加载已解压的扩展程序”。',
      '在文件选择框的地址栏粘贴下面的路径，回车后选择这个文件夹。',
      '在同一个浏览器普通窗口登录比赛网站，再点 XCPC 扩展的“同步登录状态”。看到“已连接本机 XCPC”即可。',
    ],
    folderCommand: 'xcpc path "$HOME\\XCPC"',
  },
};

export const firstContestModes = [
  {command: 'bt', label: '赛后补题', description: '练习已经结束的比赛，Codeforces、Gym、QOJ、CWNUOJ 和牛客都支持。'},
  {command: 'vp', label: '虚拟参赛', description: '支持 Codeforces、Gym 和 QOJ；运行后按提示设置时间和赛制。'},
  {command: 'live', label: '实时比赛', description: '接入正在进行的比赛。请先在原站报名，支持 Codeforces、Gym、CWNUOJ 和牛客。'},
] as const;

export const guideSections: GuideSection[] = [
  {
    id: 'install',
    title: '安装终端并确认命令可用',
    intro: '先选择上方对应的操作系统，再按顺序完成本页设置。下面的 player、示例训练赛、比赛编号和房间编号均用于说明，请替换为自己的路径或比赛。',
    steps: [
      '按当前系统的说明打开终端，复制一键安装命令并回车。',
      '按提示完成依赖安装和管理员授权，等待出现安装完成信息。公共程序需要管理员权限，个人登录和比赛文件仍按电脑用户分别保存。',
      '安装完成后关闭并重新打开终端。在 Windows 中可从开始菜单重新启动 PowerShell / Windows Terminal，再运行版本检查命令。',
      '确认能看到 XCPC 版本、安装目录、命令目录和比赛保存目录。以后即使不在下载源码的文件夹，也可直接运行全局命令。',
    ],
    commands: [
      { label: '检查版本、路径和配置状态', command: 'xcpc --version', note: '此命令离线读取配置，敏感字段只显示是否配置，不会显示密码、Cookie 或 API secret。' },
      { label: '查看完整命令说明', command: 'xcpc --help' },
    ],
    paragraphs: [
      '程序安装目录用于存放 XCPC 本身；比赛保存目录用于存放你的代码和题面，两者不是同一个目录。无需把代码放进 /opt、Program Files 或程序的 releases 文件夹。',
      '安装器会准备 XCPC 的运行环境；代码编辑器可使用你习惯的 VS Code、记事本或其他编辑器。提交后的编译和判题由对应原站完成。',
    ],
  },
  {
    id: 'browser',
    title: '让终端读取原站登录状态',
    intro: '先在浏览器中登录你要使用的比赛网站。网站云端账号、Codeforces API 和原站浏览器登录是三项不同的配置，不能互相替代。',
    platformNotes: {
      ubuntu: [
        '在普通 Firefox 窗口登录 Codeforces、QOJ、CWNUOJ 或牛客。浏览器与终端必须使用同一个 Linux 用户。',
        '程序自动寻找传统安装、Snap 或 Flatpak 的 Firefox 配置，并读取对应站点的登录状态；无需复制 Cookie，也无需关闭 Firefox。',
        '隐私窗口、容器和不同 Firefox 用户配置的登录状态不会混用。已登录但检查失败时，先确认实际登录的浏览器配置和终端用户一致。',
      ],
      macos: [
        '先运行 xcpc doctor cf，创建当前 macOS 用户的扩展文件夹和本机连接。此时若提示尚未收到登录信息，可继续按下面步骤加载扩展。',
        '在平时使用的 Chrome / Edge 中打开 chrome://extensions 或 edge://extensions，开启“开发者模式”，点击“加载已解压的扩展程序”。',
        '选择 ~/Library/Application Support/XCPC/browser-extension。文件选择窗口中可按 Command + Shift + G，粘贴该路径并回车。',
        '允许 XCPC 登录同步扩展访问需要使用的比赛网站，在同一浏览器用户配置中登录原站，再点击扩展“同步登录状态”。确认显示“已连接本机 XCPC”。',
      ],
      windows: [
        '先运行 xcpc doctor cf，创建当前 Windows 用户的扩展文件夹和本机连接。若尚未加载扩展，暂时提示未收到登录信息是正常的。',
        '在平时使用的 Chrome / Edge 中打开 chrome://extensions 或 edge://extensions，开启“开发者模式”，点击“加载已解压的扩展程序”。',
        '在文件选择窗口的地址栏输入 %APPDATA%\\XCPC\\browser-extension 并回车，选择这个文件夹。',
        '允许 XCPC 登录同步扩展访问所需比赛网站。在已登录原站的同一浏览器用户配置中点击“同步登录状态”，确认显示“已连接本机 XCPC”。',
      ],
    },
    commands: [
      { label: '检查 Codeforces 登录 / 初始化个人扩展连接', command: 'xcpc doctor cf' },
      { label: '检查 QOJ 登录', command: 'xcpc doctor qoj' },
      { label: '检查 CWNUOJ 登录', command: 'xcpc doctor paa' },
      { label: '检查牛客登录', command: 'xcpc doctor nk' },
    ],
    paragraphs: [
      'macOS / Windows 不需要专用浏览器窗口，也不需要配置 chrome.exe、msedge.exe 或浏览器可执行文件路径。使用平时的普通 Chrome / Edge 即可。',
      '切换了浏览器账号或用户配置后，重新同步并运行对应站点的 doctor 检查。比赛会话绑定原来的账号，不会自动改成另一个账号的记录。',
    ],
    image: 'browser',
    imageCaption: '原站登录操作示意：Ubuntu 使用普通 Firefox；macOS / Windows 在普通 Chrome / Edge 用户配置中加载并同步扩展。',
  },
  {
    id: 'accounts',
    title: '配置 Codeforces API 和云端账号',
    steps: [
      '需要使用 Codeforces / Gym 时，先登录 [Codeforces API 设置页](https://codeforces.com/settings/api)，创建一组 API key 与 API secret。',
      '运行 xcpc api cf，按提示输入 Codeforces 用户名和同一组 key / secret。用户名不区分大小写；浏览器应登录相同的 Codeforces 账号。',
      '运行 xcpc login，输入协会官网账号或邮箱及官网密码；此账号用于云端历史、组队、VP 房间和队内虚拟打印。',
      '运行 xcpc account，确认当前云端账号正确。每台电脑、每个电脑用户都需要分别完成自己的登录。',
    ],
    commands: [
      { label: '首次配置或更换同一 Codeforces 账号的 API 密钥', command: 'xcpc api cf', note: 'API secret 输入时不回显，粘贴完整值后按回车；不要连字段名或多余空格一起粘贴。QOJ、牛客和 CWNUOJ 不需要 Codeforces API。' },
      { label: '登录云端 / 更新保存的官网密码 / 切换云端账号', command: 'xcpc login' },
      { label: '查看云端账号与同步状态', command: 'xcpc account' },
      { label: '退出本机云端登录', command: 'xcpc logout', note: '退出云端登录不会删除本地代码、比赛记录或原站浏览器登录。' },
    ],
    table: {
      headers: ['配置', '作用', '需要的平台'],
      rows: [
        ['原站浏览器登录', '原站参赛验证、报名和提交代码', 'Codeforces / Gym / QOJ / CWNUOJ / 牛客'],
        ['Codeforces API', '读取 Codeforces 用户、比赛、榜单和提交数据', 'Codeforces / Gym，共用同账号配置'],
        ['协会云端登录', '云端历史、队伍、房间权限和虚拟打印', 'CWNU XCPC Vp System'],
      ],
    },
    platformNotes: {
      ubuntu: ['终端中通常可用 Ctrl + Shift + V 或右键粘贴；输入密码、secret 时没有字符回显并不代表没有输入。'],
      macos: ['系统终端中可用 Command + V 粘贴。API 与云端账号保存在当前 macOS 用户的个人配置中。'],
      windows: ['PowerShell / Windows Terminal 可使用右键或其粘贴快捷键。日常登录和浏览器同步使用平时的电脑用户即可。'],
    },
  },
  {
    id: 'folders',
    title: '设置保存位置与建立比赛文件夹',
    intro: '默认保存到系统桌面。建议先指定一个只用于训练的根目录，例如 XCPC，再让程序按真实比赛名称建立子文件夹。',
    steps: [
      '运行 xcpc path 查看当前保存位置并输入新路径，也可直接复制下面对应系统的设置命令。',
      '目标根目录不存在时，程序会自动创建，不需要预先运行 mkdir。路径有空格时用引号包住；Windows 的目标盘符必须已存在并且可写。',
      '从任意普通终端执行 vp、live 或 bt。验证成功后，程序会在保存根目录下创建按实际比赛名称命名的文件夹。',
      '等待比赛面板和下方命令终端打开。下方终端已进入本场比赛文件夹，在这里自行创建 A.cpp、B.py 等代码文件。',
      '同场重复执行命令会恢复已有会话和原目录，保留已有文件。不要先手动建立一个同名比赛文件夹来初始化比赛，以免与程序管理的目录发生冲突。',
    ],
    commands: [
      { label: '查看保存位置并交互修改', command: 'xcpc path' },
      { label: '直接设置保存根目录', command: {
        ubuntu: 'xcpc path "$HOME/XCPC"',
        macos: 'xcpc path "$HOME/XCPC"',
        windows: 'xcpc path "D:\\XCPC"',
      }, note: 'Ubuntu / macOS 的 $HOME 会自动替换为当前用户主目录；Windows 示例使用 D 盘，没有 D 盘时可改成其他可写目录。' },
      { label: '在新终端进入已创建的比赛目录', command: {
        ubuntu: platformGuides.ubuntu.folderCdExample,
        macos: platformGuides.macos.folderCdExample,
        windows: platformGuides.windows.folderCdExample,
      }, note: '“示例训练赛”只是示例文件夹名。请以程序实际输出的完整路径替换，不能直接用它代替真实比赛文件夹。' },
      { label: '在比赛目录中建立一个代码文件', command: {
        ubuntu: platformGuides.ubuntu.createFileCommand,
        macos: platformGuides.macos.createFileCommand,
        windows: platformGuides.windows.createFileCommand,
      }, note: '这些命令建立空文件，不会生成解题代码。已有 A.cpp 时直接用编辑器打开；Windows New-Item 不需要加 -Force。' },
    ],
    paragraphs: [
      '程序会维护比赛目录中的 contest.json 等配置；VP 还会尝试保存可取得的 PDF 题面。A.cpp、B.py、题解和子目录均由你自行建立。BT / live 不下载题面。',
      '不要手动修改或分享 contest.json，其中可能含有原站提交所需的私有登录信息。可以正常编辑同目录的源码，也可建立自己的代码子目录。',
      '修改 xcpc path 只影响新比赛的默认位置；已有比赛仍在原来的文件夹，不会自动搬家。原目录缺失时可重新创建目录，但主动删除的源码无法由程序恢复。',
      '云端会保存比赛和提交记录；它不是本地源码、题面或整个比赛文件夹的自动备份与跨电脑文件同步工具。',
    ],
    image: 'folders',
    imageCaption: '文件夹示意：根目录由 xcpc path 设置，比赛名称目录和 contest.json 由程序维护，A.cpp / B.py 等源码由你创建。',
  },
  {
    id: 'vp',
    title: '开始个人 VP：选择比赛、时间和赛制',
    intro: '个人 VP 支持 Codeforces、Gym 和 QOJ。下面编号仅演示命令格式，请替换成你准备参加的原站比赛；创建网站房间的参赛方法见后面的“多人组队 VP”。',
    commands: [
      { label: 'Codeforces：按编号开始', command: 'vp cf 2181' },
      { label: 'Gym：按编号开始', command: 'vp gym 100123' },
      { label: 'QOJ：按编号开始', command: 'vp qoj 3617' },
      { label: '也可直接输入原站比赛链接', command: 'vp https://qoj.ac/contest/3617' },
    ],
    steps: [
      '确认浏览器已登录原站账号，CF / Gym 的 API 已配置，然后执行其中一条比赛命令。',
      '按提示设置 VP 开始时间。回车默认从当前时间起三分钟后开始；可输入 HH:MM、HH:MM:SS，或 YYYY-MM-DD HH:MM。默认按 Asia/Shanghai 显示。',
      '根据链接对应的平台选择赛制。CF / Gym 提供 ICPC 与 CCPC 展示规则；QOJ 另提供 OI、IOI，所选计分必须与该场原站的实际规则相符。',
      'ICPC / CCPC 优先读取原站封榜时间；读不到时输入距离比赛结束的分钟数。0 或回车表示不封榜，60 表示结束前一小时封榜。',
      '原站确认后进入比赛面板。CF / Gym 会向原站设置预约开始时间；QOJ 到预定时间才发送立即开始请求。',
    ],
    paragraphs: [
      '预约期间保持电脑运行和面板进程正常，尤其是需要定时发出开始请求的 QOJ。Ubuntu / macOS 可脱离 tmux 后继续等待，不要结束详情进程或让电脑休眠。',
      '原站拒绝 VP 时，本次启动会取消；请求结果尚未确认时会继续核验，不会重复发送报名。不要通过删除文件夹来强行重新报名。',
      '同一场比赛只支持一次 VP；重复原命令用于恢复会话，不是重新计时。不同比赛可以分别进行。',
      'VP 题面 PDF 下载失败会跳过，仍可到原站读题。原赛榜单或提交数据暂不可用时，参考排名可能显示 — 或 ?，不会凭空生成成绩。',
    ],
    table: {
      headers: ['平台', 'VP 可选规则', '原站要求'],
      rows: [
        ['Codeforces / Gym', 'ICPC、CCPC 展示规则', '原站虚拟参赛固定按 ICPC 计分，即使原场是 Div. / Educational'],
        ['QOJ', 'ICPC、CCPC、OI、IOI', '选择与该场原站计分一致的规则，且原站允许该账号启动 VP'],
        ['CWNUOJ / 牛客', '不提供 VP', '已有原站参赛资格时使用 live；结束后使用 bt'],
      ],
    },
  },
  {
    id: 'live',
    title: '接入正在进行的原站比赛',
    intro: 'live 使用你已经在原站获得的参赛资格和官方时间，不会代替你报名，也不会另起一个本地比赛时间。',
    steps: [
      '先在原站报名，登录对应的原站账号；CWNUOJ 的限时场次还需要按原站要求开始参赛。',
      '执行 live 命令，等待程序验证报名状态、读取比赛时间并创建比赛目录。',
      '在打开的比赛目录中编写与提交代码，面板会同步本人在终端或网页产生的提交结果。',
      '原赛结束后继续补题时，使用相同比赛的 bt 命令或恢复 xcpc dashboard。',
    ],
    commands: [
      { label: 'Codeforces', command: 'live cf CONTEST_ID', note: '将 CONTEST_ID 换成实际数字编号；也可使用完整 Codeforces / Gym 比赛链接。' },
      { label: 'CWNUOJ（简称 paa）', command: 'live paa CONTEST_ID', note: '将 CONTEST_ID 换成 CWNUOJ 比赛链接末尾的完整编号。' },
      { label: '牛客（简称 nk）', command: 'live nk CONTEST_ID', note: '将 CONTEST_ID 换成牛客比赛链接中的数字编号。' },
      { label: '直接使用完整比赛链接', command: 'live CONTEST_URL', note: '将 CONTEST_URL 换成 Codeforces / Gym、CWNUOJ 或牛客的原站比赛链接，不能保留这个占位文字。' },
    ],
    paragraphs: [
      '牛客支持 ACM、IOI、OI，ACM 默认按 ICPC 处理；CWNUOJ 支持 XCPC、IOI、OI。Codeforces live 保留原场 CF / IOI / ICPC 的原站分数，不用 ICPC 罚时替代积分。',
      'BT 和 live 不保存题面文件。题目列表、比赛规则和判题信息从原站读取；官网暂时隐藏的结果会显示未公开，而不会猜测分数。',
    ],
  },
  {
    id: 'practice',
    title: '赛后补题与已有记录恢复',
    intro: 'bt 支持 Codeforces、Gym、QOJ、CWNUOJ 和牛客，参数使用完整的原站比赛链接；牛客的题单也可用于补题。',
    commands: [
      { label: '进入补题', command: 'bt CONTEST_URL', note: '把 CONTEST_URL 换成实际原站比赛链接，例如 https://qoj.ac/contest/3617。' },
      { label: '牛客题单示例：0x01 基本算法-位运算', command: 'bt https://ac.nowcoder.com/acm/contest/996', note: '题单仅支持 bt，不支持 live。程序按原题单名称建立文件夹，读取已有提交、尝试次数和通过状态。' },
      { label: '在已有比赛目录恢复面板', command: 'xcpc dashboard' },
    ],
    paragraphs: [
      '此前已有 VP / live 的比赛，bt 会恢复原记录及原文件夹；没有记录时才建立补题工作区。已有源码和子目录不会被清除，也不会为了补题新建一套重复目录。',
      '补题读取原站已有的本人提交，包括直接在网站提交的记录。每题显示尝试数量和是否通过，不按 ICPC、OI、IOI 或 CF 规则重新算分。',
      '补题不计时，Rank 不随补题变化，也不显示公共统计区。原 VP / live 已通过的题目保留绿色，补题后新通过的题目显示紫色；下方提交列表仍使用正常判定颜色。',
      '本地 VP 尚未结束时，先在本场目录运行 xcpc end，再进入补题。live 场次请等待原站比赛结束。',
    ],
  },
  {
    id: 'submit',
    title: '编写代码并确认提交',
    steps: [
      '在本场比赛面板下方的命令终端操作，或在新终端进入这场比赛的实际目录。确认该目录是程序建立的比赛目录。',
      '用编辑器打开 A.cpp 或 B.py，写入自己的代码并保存。程序不会自动生成这些源码文件；图中的代码文件是示例。',
      '使用题号命名时执行 submit A.cpp。文件名不是题号时，显式指定 --problem，例如 submit solution.cpp --problem A。',
      '核对英文预览中的 filename、contest、problem、language 和 url。输入 y 并回车才真正提交；输入 n 或直接回车会取消。',
      '成功返回编号后，可以打开终端给出的原站提交链接查看判题；保持面板运行，结果会持续更新。',
    ],
    commands: [
      { label: '提交 A 题的 C++ 文件', command: 'submit A.cpp' },
      { label: '提交 B 题的 Python 文件', command: 'submit B.py' },
      { label: '使用自定义文件名并指定题号', command: 'submit solution.cpp --problem A' },
      { label: '查看提交入口说明', command: 'submit --help' },
    ],
    paragraphs: [
      '示例确认提示为 Do you want to continue? (y/n)。请先保存文件，再提交；如果编辑器正在显示其他目录的同名文件，终端实际提交的仍是预览中列出的路径。',
      '原站支持的语言和编译器因比赛而异。若自动选择不合适，按提交入口提供的选项指定语言；以确认预览和原站实际提供的编译器为准。',
      '直接在原站网页提交的本人记录也会同步，不要求每次都通过本地 submit。请求超时或未取得编号时先查看原站记录，确认是否成功后再决定重试。',
    ],
    platformNotes: {
      ubuntu: ['下方终端可用 pwd 查看当前位置、ls 查看文件。可在文件管理器中打开对应目录，再用熟悉的编辑器保存代码。'],
      macos: ['下方终端可用 pwd / ls 确认目录，也可在访达中打开对应比赛目录。终端选中路径后复制时，请保留名称中的空格并在命令中加引号。'],
      windows: ['下方 PowerShell 可用 Get-Location / Get-ChildItem 确认目录与文件。用记事本保存时确认真实扩展名为 .cpp 或 .py，而不是 A.cpp.txt。'],
    },
    image: 'terminal',
    imageCaption: '比赛窗口示意：上方查看成绩和判题，下方在本场比赛目录中保存文件、执行 submit，并输入 y / n 确认。',
  },
  {
    id: 'dashboard',
    title: '操作面板、恢复会话与结束本地 VP',
    commands: [
      { label: '在本场比赛目录或其子目录恢复详情', command: 'xcpc dashboard' },
      { label: '手动结束当前目录对应的本地 VP', command: 'xcpc end' },
      { label: '结束指定轮次', command: 'xcpc end ATTEMPT_ID', note: '将 ATTEMPT_ID 换成 vp history 中显示的准确轮次 ID。' },
    ],
    paragraphs: [
      '恢复面板不会新建比赛或清空代码。正在进行的会话恢复实时详情，已经结束的会话恢复为补题界面；同场再次输入原 vp / live / bt 命令也会复用原会话。',
      'xcpc end 只结束本地 VP，不会替你结束原站虚拟参赛。后续提交作为补题记录，原比赛数据和文件保留。',
      '先点击上方详情区域，再用 ↑ / ↓、PgUp / PgDn、Home / End 或鼠标滚轮查看提交记录。输入 q 会关闭详情进程，不等于结束原站比赛。',
    ],
    platformNotes: {
      ubuntu: [
        '上方是详情，下方是 Bash / 你配置的 shell，默认进入比赛目录。可直接点击下方输入命令；也可按 Ctrl+B，松开后按方向键切换面板。',
        '暂时离开时，按 Ctrl+B，松开后按 D 脱离 tmux，详情仍在后台运行。再次执行原比赛命令可恢复；预约 QOJ 时保持电脑运行。',
      ],
      macos: [
        '比赛窗口同样使用 tmux。点击下方 shell 输入提交命令；Ctrl+B 后松开，再按方向键切换面板。',
        'Ctrl+B 后松开，再按 D 可脱离并保留后台面板。恢复时执行原比赛命令或在比赛目录运行 xcpc dashboard。',
      ],
      windows: [
        'vp、live、bt 和 xcpc dashboard 都在当前终端显示，不会新建终端窗口。Windows Terminal 不是必装软件，也不需要 WSL 或 tmux。',
        '已经处于 Windows Terminal 会话中时，当前窗口上下分屏：原面板在上方显示详情，下方约 35% 的空间用于在比赛目录输入命令。',
        '普通 PowerShell 或其他 Windows 控制台在当前控制台显示详情面板，按 q 返回原命令行。提交前确认进入该比赛目录，运行 submit 后可执行 xcpc dashboard 恢复；q 只关闭面板，不结束 VP。',
        '保持面板运行可继续同步。关闭后进入原比赛目录运行 xcpc dashboard 恢复，不要为了恢复面板重新建立同名目录。',
      ],
    },
  },
  {
    id: 'teams',
    title: '创建队伍并参加网站 VP 房间',
    steps: [
      '在网站登录同一个协会账号。需要组队时进入“队伍管理”，填写中文队名和英文队名；创建者为队长，队伍总人数为 1–3 人。',
      '按姓名或学号搜索已经登录过本网站的用户并邀请。对方在消息中接受后才正式加入；拒绝后可以重新邀请。',
      '在 VP 列表创建房间，设置名称、QOJ / CF / Gym 原站链接、开始时间、平台支持的赛制，以及距离结束的封榜分钟数。0 表示不封榜。',
      '创建者进入房间“人员管理”邀请个人或队伍。创建者自己默认也没有报名，需要邀请自己或自己的队伍并接受；队伍参赛邀请由队长接受。',
      '开始前可在 VP 列表取消报名，队伍由队长操作；房主可在人员管理中移除个人或整支队伍。退出后需重新接受邀请，恢复已有 VP，历史和文件保留。到了房间开始时间后不能取消报名或移除参赛者。',
      '由队长或个人参赛者在本机运行 xcpc login，使用自己在网站上接受邀请的协会账号。队伍由队长在自己的电脑执行房间参赛命令，其他队员不重复建立 VP。',
      '复制房间主页的完整参赛命令并执行。终端会验证个人 / 队伍身份，采用房间的原站比赛、开始时间、赛制和封榜设置。',
    ],
    commands: [
      { label: '本机登录同一协会账号', command: 'xcpc login' },
      { label: '进入已经接受邀请的房间', command: 'vp https://vp.cwnupaa.com/vp/ROOM_ID', note: '将 ROOM_ID 换成房间主页给出的真实编号，或直接复制网站提供的完整命令。' },
    ],
    paragraphs: [
      '个人身份显示“昵称-姓名”；队伍身份显示“队名-成员姓名”，成员之间用顿号分隔。队伍只有队长可以在本机创建、提交和上传本场比赛状态。',
      '队伍的同一条参赛历史会对参赛时的队长和成员共享，在每个人的历史记录中标记为“队伍”。成员看到的是同一场队伍记录，不会另造一条个人参赛记录。',
      '只查看本队历史、排行榜和队内打印的队员，登录网站即可，不需要在自己的电脑上再启动同一场 VP。',
      '房间有主页、排行榜、我的提交和虚拟打印。比赛进行期间无法查看其他参赛单位的提交详情；本人和队友可以查看本队的完整判题。',
      '排行榜的 Rank 沿用原站或原赛参考排名，不会改成房间内部的相对名次。封榜期间公共榜只展示允许公开的数据，本人 / 本队的“我的提交”仍可查看真实结果。',
      'CCPC 展示规则中，原赛至少 50 支队伍（含打星队）通过后才揭示公共统计的题号；封榜或结束后揭示全部。房间内的参赛队伍不额外计入这 50 支原赛队伍。',
    ],
    image: 'rooms',
    imageCaption: '组队房间流程示意：创建队伍 → 接受邀请 → 接受房间邀请 → 队长在终端进入房间 → 全队查看同一条参赛历史。',
  },
  {
    id: 'printing',
    title: '在队伍房间使用虚拟打印',
    intro: '虚拟打印仅适用于从网站房间链接进入的比赛工作区，不用于普通个人原站 VP / live / bt。',
    commands: [
      { label: '上传 A.cpp 为本队虚拟打印', command: 'printfile A.cpp' },
    ],
    steps: [
      '在本场房间的比赛目录中保存要打印的 UTF-8 源码文件，文件不超过 512 KiB。',
      '由可操作本场比赛的队长或个人参赛者执行 printfile A.cpp。',
      '核对英文预览中的文件名、语言、房间、参赛身份和大小，在 Do you really want to print this file? (y/n) 后输入 y 确认。',
      '在网站该房间的“虚拟打印”列表查看打印编号、文件名与时间，点击详情可阅读完整代码。',
    ],
    paragraphs: [
      '同一参赛队伍的成员可以查看本队打印；个人参赛者仅查看自己的打印。其他队伍和房间创建者不能借此查看你的代码。',
      '虚拟打印会上传这次确认的文件内容，不会自动同步整个代码文件夹，也不等于提交到原站判题。',
    ],
  },
  {
    id: 'history',
    title: '查看历史、判题结果与颜色',
    commands: [
      { label: '列出本机 VP / live / 补题记录', command: 'vp history' },
      { label: '查看某轮的提交与目录', command: 'vp history ATTEMPT_ID', note: '将 ATTEMPT_ID 换成上一步列表中的准确 ID。' },
      { label: '打开该轮原会话', command: 'vp history ATTEMPT_ID --open' },
      { label: '仅查看实时比赛记录', command: 'vp history --live' },
    ],
    paragraphs: [
      '云端登录后，比赛信息和提交结果自动同步到网站“历史记录”；可以按比赛名称搜索、按 VP / live / 补题筛选，并区分个人和队伍参赛。',
      '查看详情可看到原站链接、比赛开始与结束时间（补题不计时）、每次提交的题号、时间和判题状态。点击提交编号或链接可前往原站结果页。',
      '终端与网页都读取直接在原站网页提交的本人记录。网络暂时不可用时先保存本地进度，恢复后重试；更新程序不会清空已有代码与历史。',
    ],
    table: {
      headers: ['显示', '含义'],
      rows: [
        ['绿色通过方格', '原 VP / live 期间已通过'],
        ['红色方格', '有已判尝试，尚未通过'],
        ['紫色通过方格', '补题后新通过；原赛已通过仍保留绿色'],
        ['1 try / 2 tries', '该题的尝试次数'],
        ['蓝色方格、带 ? 或待公开次数', '仍在判题、官网隐藏或封榜待公开，以具体状态文字为准'],
        ['—', '尚无可显示记录或相关信息不可用'],
      ],
    },
  },
  {
    id: 'update',
    title: '更新终端和浏览器扩展',
    commands: [
      { label: '从服务器更新到最新版本', command: 'xcpc update' },
      { label: '旧版先用服务器更新一次', command: 'xcpc update --cloud' },
      { label: '更新后核对版本和实际路径', command: 'xcpc --version' },
    ],
    paragraphs: [
      '默认从 vp.cwnupaa.com 下载已发布更新包，校验 SHA256 后安装。此前默认从 GitHub 更新的版本，先运行一次 xcpc update --cloud；以后直接运行 xcpc update。原安装位置、账号、保存路径、代码和比赛记录均保留。',
      '服务器不可用时保留当前版本；需要使用源码仓库时，可显式运行 xcpc update --github，从 github.com/Jiangys-Crz/xcpc-oj-contest-helper 的 main 分支更新。',
      '公共程序更新可能再次请求管理员授权。结束后重新打开终端再恢复比赛面板，让新启动的进程使用新版本。',
      '如果连 xcpc 命令本身都无法运行，重新执行本页对应系统的一键安装命令；已有个人文件和账号会保留，不要手动删除安装目录或配置目录。',
    ],
    platformNotes: {
      ubuntu: ['Firefox 登录不依赖本项目的浏览器扩展，更新后可直接运行对应站点的 doctor 检查。'],
      macos: ['更新后打开 Chrome / Edge 扩展管理页，重新加载 XCPC 扩展，再点击同步登录状态。使用的仍是当前用户的原扩展文件夹。'],
      windows: ['更新后重新打开 PowerShell / Windows Terminal；Chrome / Edge 中重新加载 XCPC 扩展并同步。安装失败的日志不会随临时下载目录删除。'],
    },
  },
  {
    id: 'mirrors',
    title: '国内镜像、下载速度与官方源',
    intro: '安装器默认优先使用国内镜像，并优先尝试随附的依赖包；不需要先手动改 pip、Homebrew 或系统的软件源。',
    table: {
      headers: ['系统 / 下载内容', '默认加速范围'],
      rows: [
        ['Ubuntu', 'Ubuntu 软件包下载和 Python 依赖优先使用国内镜像；已有可用环境会继续复用'],
        ['macOS', 'Homebrew 相关下载与 Python 依赖使用国内镜像优先的安装流程'],
        ['Windows', '缺少公共 Python 时从本站国内服务器获取并校验官方安装包；WinGet 目录索引和 Python 依赖使用国内镜像，Git 安装包仍可能从原厂下载；Windows Terminal 不作为安装依赖'],
        ['XCPC 更新包', '默认从本站服务器下载并校验更新包；GitHub 仅用于显式源码更新'],
      ],
    },
    paragraphs: [
      '国内镜像不可用时，安装器会尝试官方来源；镜像也不能保证每一个第三方软件的原厂安装包都能通过国内地址下载。',
      '镜像设置用于本次安装流程，不会覆盖你已有的 pip 配置、Homebrew 配置或 Ubuntu 系统源文件。不要为了安装而关闭 TLS 证书验证。',
      '需要全部使用官方来源时，可用下面的方式设置 XCPC_MIRROR=official 后安装。默认值为 china；已有随附依赖仍会优先使用。',
    ],
    commands: [
      {
        label: '可选：使用官方来源重新执行安装',
        command: {
          ubuntu: 'curl -fsSL https://vp.cwnupaa.com/install.sh | XCPC_MIRROR=official bash',
          macos: 'curl -fsSL https://vp.cwnupaa.com/install.sh | XCPC_MIRROR=official bash',
          windows: "$env:XCPC_MIRROR = 'official'\n" + platformGuides.windows.installCommand,
        },
        note: '这不是首次安装的必做步骤；正常情况下直接使用第一节的一键安装命令即可。Windows 中该变量只影响当前终端会话，关闭此终端后恢复默认。',
      },
    ],
  },
  {
    id: 'troubleshooting',
    title: '常见问题与检查顺序',
    table: {
      headers: ['现象', '按这个顺序处理'],
      rows: [
        ['command not found / 无法识别 xcpc', '重新执行本页的一键安装命令并完成授权，关闭旧终端后新开窗口，运行 xcpc --version。以其中的命令目录和安装路径为准。'],
        ['浏览器已登录，doctor 仍提示未登录', '先核对当前电脑用户与浏览器配置。Ubuntu 确认普通 Firefox；macOS / Windows 确认扩展与已登录网站在同一 Chrome / Edge 用户配置，允许网站访问后同步。'],
        ['扩展提示本地连接未建立', '在该电脑用户下运行 xcpc doctor cf 初始化或修复连接，再重新加载扩展并同步；其他原站可换用 doctor qoj / paa / nk。'],
        ['Codeforces 账号不匹配或 API 错误', '浏览器登录比赛原来的 Codeforces 账号，运行 xcpc api cf 更新同一组 key / secret，再运行 doctor cf。用户名大小写不影响匹配。'],
        ['No contest.json / 无法识别比赛目录', '先成功执行本场 vp / live / bt，再进入程序输出的比赛目录；用 pwd 或 Get-Location 检查，不要在程序安装目录或别场目录提交。'],
        ['同名比赛目录已存在 / 提示不能覆盖', '保留原有文件，用 xcpc path 选择其他可写根目录，或恢复与该目录绑定的原记录。只需设置保存根目录，不要预先建立同名比赛目录或伪造 contest.json。'],
        ['原站拒绝 VP / 已有记录', '原站不允许时本次启动会取消。同场已有会话时重复原命令恢复，不能通过新建目录、删除记录来获得原站新的 VP 资格。'],
        ['长时间 Pending 或网络错误', '先打开提交后给出的原站链接，区分仍在判题、官网隐藏、封榜和终端网络异常。恢复面板或对应 doctor 检查后再同步，避免未确认结果就重复提交。'],
        ['原比赛文件夹不见了', '重复原比赛命令或从历史打开，可重建工作目录；如果源码已被你删除，重建目录不会恢复已丢失的文件。'],
      ],
    },
    commands: [
      { label: '先收集版本与配置状态', command: 'xcpc --version' },
      { label: '再检查具体原站登录', command: 'xcpc doctor cf', note: 'QOJ / CWNUOJ / 牛客分别换成 qoj、paa、nk。诊断输出不包含 Cookie 和密码。' },
    ],
    platformNotes: {
      ubuntu: [
        '如果输出显示运行用户是 root，请切回日常登录 Firefox 的普通用户后执行比赛命令；安装公共程序需要 sudo，并不意味着日常使用也要 sudo。',
        '如果复制一键命令后首先提示 curl: command not found，先运行 sudo apt update，再运行 sudo apt install -y curl，然后重新执行一键安装命令。下载入口尚未启动时无法自动补齐 curl。',
      ],
      macos: ['程序路径包含空格时保留引号；扩展选择路径可用 Command + Shift + G。不要通过清空个人配置来解决普通网络或登录验证失败。'],
      windows: ['若只看到 Installation failed (exit 1)，查看它前面的具体错误，以及 %LOCALAPPDATA%\\XCPC\\Logs\\install-日期时间-编号\\setup.log。管理员窗口关闭后，原窗口仍会显示错误摘要和日志路径。'],
    },
  },
];
