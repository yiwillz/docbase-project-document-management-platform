# docbase 安装与运维手册（Linux）

面向：负责安装和维护 docbase 的 IT 人员。Windows 的安装见 `install-windows.md`。

Linux 上有两种装法，用同一套程序，数据和备份格式也一样（可以互相迁移）：

| 方式 | 适用 | 需要 |
|---|---|---|
| **docker**（推荐） | 任何 64 位 Linux（Ubuntu、Debian、CentOS / Rocky、麒麟、统信等） | Docker Engine 24+ 和 compose 插件 |
| **systemd**（不用 docker） | Ubuntu 24.04 LTS | 能连 apt 源（或公司内网镜像）装 Python 3.12、PostgreSQL、Caddy |

安装包：`docbase-版本-linux.tar.gz`（离线：Python 依赖在包里；带 `docbase-image.tar` 的是 docker 离线版）。

## 1. 组成和端口

| 部分 | 作用 | 端口 |
|---|---|---|
| HTTPS 代理（Caddy） | HTTPS 入口，HTTP 自动跳转到 HTTPS | 443、80（对局域网） |
| docbase 后端 | 接口和页面 | 8000（只在本机 / 容器网络内） |
| docbase worker | 后台任务：存量导入、交付包、镜像、每天的备份和清理、存储检查 | 无 |
| PostgreSQL | 数据库 | 5432（只在本机 / 容器网络内） |

## 2. 硬件要求

内存 ≥ 8 GB；文件存储 ≥ 1 TB（本机盘、挂载的 NAS 均可，路径固定）；备份最好放另一块盘或 NAS；固定 IP。

## 3. docker 方式

### 3.1 安装

```bash
tar -xzf docbase-版本-linux.tar.gz
cd docbase-版本-linux
sudo ./docker-install.sh --data-dir /srv/docbase/data
```

参数：`--backup-dir`（默认 `/srv/docbase/backup`）、`--log-dir`（默认 `/srv/docbase/logs`）、`--address`（IP 或名字，多个用逗号分隔，默认本机第一个 IP）、`--tz`（时区，备份时间按它算，默认系统时区）。

脚本依次做：检查 docker → 载入镜像（包里有 `docbase-image.tar` 时离线载入）→ 生成 `.env`（数据库密码随机、只有 root 能读）→ 建目录并给容器用户（uid 10001）写权限 → 启动四个容器 → **建第一个系统管理员**（输入用户名和两次密码）→ 导出根证书 `docbase-root.crt`。

**安装目录就是解压出来的这个目录**（里面有 `.env` 和 `docker-compose.yml`），以后的命令都在这里执行；建议解压到固定位置，如 `/opt/docbase-docker`。

四个容器都设成 `restart: unless-stopped`：服务器重启后自动启动，崩溃后自动重启。数据库数据在 docker 卷 `docbase_pgdata` 里。

### 3.2 常用命令

```bash
cd /opt/docbase-docker
sudo docker compose ps                     # 状态
sudo docker compose logs -f api            # 后端日志（worker、proxy、db 同理）
sudo docker compose restart api worker     # 改了配置后重启
sudo docker compose down                   # 全部停止（数据不丢）
sudo docker compose up -d                  # 启动
./smoke-test.sh https://localhost 管理员 密码   # 冒烟测试：健康、页面、登录、建一个测试项目并归档
```

更多配置（存量导入目录、保留版本数等）：在安装目录建 `docbase.env`，写 `DOCBASE_IMPORT_ROOTS=…` 这样的行（可用的项见 `templates/docbase.env.template`），再 `docker compose up -d`。存量导入的源目录、共享盘镜像目录要挂进容器：建 `docker-compose.override.yml`：

```yaml
services:
  api: &extra
    volumes:
      - /mnt/old-projects:/mnt/old-projects:ro
  worker: *extra
```

### 3.3 备份、恢复、日志

worker 每天 1 点（`.env` 里的 `BACKUP_HOUR`）自动备份到备份目录，保留 30 天。手动：

```bash
sudo docker compose exec worker python scripts/backup.py            # 马上备份
sudo docker compose exec worker python scripts/backup.py --list     # 列出备份点
# 恢复演练：恢复到新库 docbase_drill、文件放到备份目录下的 restore-drill，最后核对数量
sudo docker compose exec worker python scripts/restore.py --to docbase_drill --data-dir /var/backups/docbase/restore-drill
# 打包最近日志发给维护方（放在日志目录里）
sudo docker compose exec worker python scripts/collect_logs.py --out /var/log/docbase
```

真正出事要覆盖正在用的库：`sudo docker compose stop api worker proxy`，再
`sudo docker compose run --rm worker python scripts/restore.py --stamp 日期 --to docbase --data-dir /var/lib/docbase/data --force`，最后 `sudo docker compose up -d`。

### 3.4 升级

```bash
tar -xzf docbase-新版本-linux.tar.gz && cd docbase-新版本-linux
sudo ./docker-upgrade.sh --from /opt/docbase-docker
```

先备份 → 载入新镜像 → 把新的 compose 文件复制过去、改 `.env` 里的版本 → 重新创建容器（后端启动时自动升级数据库结构）→ 检查。回退：把 `.env` 里的 `DOCBASE_VERSION` 改回旧版本再 `docker compose up -d`；数据库结构已经升级的话用备份恢复。

### 3.5 没有离线镜像时

在能上网的机器上从源码构建：在源码根目录 `docker build -f deploy/linux/Dockerfile -t docbase:版本 --build-arg VERSION=版本 .`，再 `docker save docbase:版本 -o docbase-image.tar` 拷过去。

## 4. systemd 方式（Ubuntu 24.04）

### 4.1 安装

```bash
tar -xzf docbase-版本-linux.tar.gz
cd docbase-版本-linux
sudo ./install.sh --data-dir /srv/docbase/data
```

参数：`--backup-dir`（默认 `/srv/docbase/backup`）、`--address`、`--proxy caddy|nginx|none`（默认 caddy）、`--database-url`（用已有的数据库）。

脚本依次做：apt 安装 python3.12、PostgreSQL、Caddy → 建系统用户 `docbase` 和目录 → 程序放到 `/opt/docbase/app`、离线装 Python 依赖 → 建库和账号（随机密码）→ 写 `/etc/docbase/docbase.env`（只有 root 和 docbase 能读）→ 升级数据库结构、建第一个系统管理员 → 注册并启动 `docbase-api`、`docbase-worker` 两个 systemd 服务（开机自启、崩溃 10 秒后重启）→ 配置 Caddy（HTTPS）、开 ufw 的 80 / 443 → 导出根证书到 `/etc/docbase/docbase-root.crt`。

目录：程序 `/opt/docbase`；配置 `/etc/docbase/docbase.env`；日志 `/var/log/docbase`（以及 `journalctl -u docbase-api`）；文件和备份是安装时指定的目录。

### 4.2 常用命令

```bash
systemctl status docbase-api docbase-worker caddy
sudo systemctl restart docbase-api docbase-worker      # 改了配置后
journalctl -u docbase-api -f                          # 实时日志
./smoke-test.sh https://localhost 管理员 密码           # 冒烟测试（在安装包目录里）
```

运维脚本以 docbase 用户运行（下面用 `dbpy` 代替这一串）：

```bash
alias dbpy='cd /opt/docbase/app/backend && sudo -u docbase env DOCBASE_ENV_FILE=/etc/docbase/docbase.env /opt/docbase/venv/bin/python'
dbpy scripts/backup.py              # 马上备份（每天 1 点也会自动备份）
dbpy scripts/backup.py --list
dbpy scripts/restore.py --to docbase_drill --data-dir /srv/docbase/backup/restore-drill   # 恢复演练
dbpy scripts/collect_logs.py --out /tmp                                                  # 打包日志
```

### 4.3 升级、卸载

```bash
cd docbase-新版本-linux && sudo ./upgrade.sh      # 备份 → 换程序（旧的留在 /opt/docbase/app.previous）→ 迁移 → 启动 → 检查
sudo ./uninstall.sh                               # 只删服务；--remove-program 另外删 /opt/docbase
```

数据库、文件、备份、`/etc/docbase` 卸载时都不删。

## 5. 证书

默认用 Caddy 的内网自签证书（同 Windows，见 `install-windows.md` 第 5 节）：把根证书（docker：安装目录的 `docbase-root.crt`；systemd：`/etc/docbase/docbase-root.crt`）装到每台访问的电脑上。

用公司 CA 签发的证书：
- docker：把证书和私钥放到安装目录 `certs/`，在 `docker-compose.override.yml` 里给 proxy 挂上 `./certs:/certs:ro`，把 `Caddyfile.docker` 里的 `tls internal` 改成 `tls /certs/docbase.crt /certs/docbase.key`，`docker compose up -d`。
- systemd：改 `/etc/caddy/Caddyfile` 的 `tls internal` 为证书路径（caddy 用户要能读），`sudo systemctl reload caddy`。
- 或者用 Nginx：配置示例在安装包的 `nginx/docbase.conf`（安装时加 `--proxy nginx`）。

## 6. 文件存储断开

文件目录在 NAS 等网络盘上断开时：页面顶部出现红条、上传和导入被拒绝；日志里记一条 ERROR；`/api/health` 返回 `"storage": false`。接回去后一分钟内自动恢复。docker 方式下，挂载点断开后要 `docker compose restart api worker` 才能重新看到。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `docker-install.sh` 提示连不上 docker | 用 sudo 运行；或 `systemctl start docker` |
| 容器里写不了文件目录 | 目录要给 uid 10001 写权限：`sudo chown -R 10001:10001 目录` |
| 浏览器显示"不安全" | 这台电脑还没装根证书 |
| 别的电脑打不开 | 防火墙（ufw / firewalld / 云安全组）没放行 443、80 |
| systemd 方式 `apt` 装不上 | 服务器连不上 apt 源：配置公司内网镜像，或改用 docker 方式（带离线镜像） |
| 忘记管理员密码 | docker：`sudo docker compose exec api python scripts/create_user.py --username 用户名 --admin`；systemd：`dbpy scripts/create_user.py --username 用户名 --admin` |
