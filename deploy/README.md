# 线上部署与维护

目标：`https://vp.cwnupaa.com`，项目目录 `/home/paa/xcpc-vp-system`。Next.js + Node 24 + SQLite，独立 PM2 和 `xcpc-vp` 系统用户。系统原有 Node、PM2、Caddy 进程和其他服务保持原配置。

## 构建和安装

在开发电脑安装 Docker，进入项目根目录执行：

```bash
bash deploy/build-local.sh
```

脚本先在本机原生环境完成 `next build`，再在本机 Linux x64 容器中安装对应平台的生产依赖并打包运行时，产物为 `outputs/cloud-release/xcpc-vp-cloud-linux-x64.tar.gz`，包含 standalone、静态资源、独立 Node 24 和 PM2。实际服务器不运行 npm 安装或应用构建。

首次安装以 root 执行 `deploy/prepare-server.sh`，建立本项目用户和目录；将运行配置写入 `shared/env.json`（root:xcpc-vp、0640）：

```json
{
  "VP_ADMIN_ACCOUNT": "admin",
  "VP_ADMIN_PASSWORD": "设置独立的初始管理员密码",
  "VP_EXTERNAL_APP_KEY": "协会分配的真实密钥",
  "VP_EXTERNAL_BASE_URL": "https://www.cwnupaa.com/api",
  "VP_PUBLIC_URL": "https://vp.cwnupaa.com"
}
```

密码和密钥不要提交到 Git。`provision-env.py` 提供隐藏输入并通过 SSH stdin 写入的示例，复用已建立的 SSH 连接；部署其他服务器时修改连接参数。管理员只在数据库首次初始化时生成，密码保存为 scrypt 哈希。后续可在网站设置中修改协会接口密钥。

上传本地构建产物及安装脚本，用产物真实 SHA256 安装：

```bash
bash deploy/install-release.sh /tmp/xcpc-vp-cloud-linux-x64.tar.gz SHA256值
```

安装器验证产物，切换 `current`，启动或平滑更新本项目独立 PM2；健康检查通过后，在 `/root/.hydro/Caddyfile` 仅追加本项目独立配置的 import，验证后调用原 Caddy 二进制平滑重载。不重启现有 Caddy。安装失败恢复本项目原指针及配置；原配置有并发修改时停止覆盖。

## 目录与服务

- `releases/`：已构建的应用，`current` 指向当前版本。
- `runtimes/`：独立 Node / PM2 运行时，`runtime` 指向当前运行时。
- `shared/data/vp.sqlite`：持久数据库，WAL 模式；更新不会覆盖。
- `shared/env.json`：私有运行配置；不要下载到公开位置。
- `shared/pm2/`：独立 PM2_HOME，与原服务隔离。
- `shared/logs/`：应用及本域名访问日志。
- `shared/data/backups/`：每天 Asia/Shanghai 04:20 附近备份，保留最近 14 份。
- `deployment-backups/`：部署前配置备份，仅 root 可读。

```bash
systemctl status xcpc-vp-pm2.service
systemctl reload xcpc-vp-pm2.service
systemctl status xcpc-vp-backup.timer
systemctl start xcpc-vp-backup.service
curl -f https://vp.cwnupaa.com/api/health
```

查询 PM2 时必须指定本项目的 PM2_HOME，不能对系统其他 PM2 执行 restart/kill：

```bash
runuser -u xcpc-vp -- env PM2_HOME=/home/paa/xcpc-vp-system/shared/pm2 \
  /home/paa/xcpc-vp-system/runtime/node \
  /home/paa/xcpc-vp-system/runtime/pm2/node_modules/pm2/bin/pm2 status
```

恢复数据库前停止本项目服务，先保存当前数据文件，使用一致性备份恢复；不要在服务写入期间只复制主数据库而丢弃 WAL。恢复后仍使用 `xcpc-vp` 所有权、0600 权限，再启动本项目服务。

## 发布终端更新

`shared/terminal-latest.json` 是公开版本清单的服务器文件，内容为 `version`、Git `revision`、源码包 `sha256` 和固定域名 `sourceUrl`。源码包在本地从已提交的文件生成，包含 Python 模块、安装器、第三方提交器和 wheels，排除个人配置、凭据、数据库、node_modules、构建输出。

后续代码提交和发布统一使用 [GitHub 仓库](https://github.com/Jiangys-Crz/xcpc-oj-contest-helper)的 `main` 分支；本地 `origin` 和默认推送目标均指向该仓库。原 Gitee 仓库已删除，不再作为源码、推送或更新来源。

新终端的 `xcpc update` 默认从 GitHub `main` 更新。源码包仍上传到 `shared/public/downloads/terminal/REVISION.tar.gz`，验证散列后再原子切换清单，供旧终端首次迁移、一键安装及显式 `xcpc update --cloud` 使用；包一旦发布不要改变内容。公开清单必须引用已经推送到 GitHub 的提交。Caddy 只公开这个下载目录，账号配置和数据库不在公开目录中。
