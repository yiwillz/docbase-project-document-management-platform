# 把 docbase 迁移到新机器（Windows）

适用：换电脑、换服务器、旧机器要报废。目标是保留数据库、文件、用户、权限和历史；必须以恢复后的数量与文件哈希核对为准，不能只凭服务启动认定迁移完成。

准备：新机器按 [Windows 手册](install-windows.md) 使用在线安装入口，或预装环境后离线安装，准备存储、备份与固定 IP；
**新旧机器用同一个 docbase 版本**（版本不同的话，先在旧机器上升级到同一版本再迁移）。

## 步骤

### 1. 在新机器上安装

使用同版本完整包：联网时双击 `install.cmd` 自动准备环境和临时管理员；离线且 Python/PostgreSQL 已准备好时，以管理员 PowerShell 执行下面命令。恢复后初始账号被旧数据中的账号替换，新的 `setup-credentials.json` 中应用初始密码不能用来登录恢复后的旧账号。

```powershell
.\install.ps1 -Offline -DataDir E:\docbase-data
```

装好后先停掉新机器上的服务：

```powershell
Stop-Service docbase-proxy, docbase-worker, docbase-api
```

### 2. 在旧机器上做最后一次备份

通知大家停止使用，然后在旧机器上：

```powershell
Stop-Service docbase-proxy, docbase-api, docbase-worker  # 停止请求与后台写入后做最终备份
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\backup.py
```

### 3. 把备份和证书拷到新机器

先检查备份清单 `missing` 为空且数据库备份存在。当前“最近备份成功”不能替代完整性检查；有缺失时先修复或补齐，不切换正式服务。

- **整个备份目录**复制到新机器的备份目录。
- 想让各电脑不用重新安装根证书：把旧机器的 `C:\docbase\proxy\data` 文件夹复制到新机器同一位置（覆盖）。
- 如果用的是公司证书：把 `C:\docbase\certs\` 里的证书和私钥一起复制。

### 4. 在新机器上恢复

```powershell
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\restore.py --list
..\..\venv\Scripts\python.exe scripts\restore.py --to docbase --data-dir E:\docbase-data --force
```

按提示输入库名 `docbase` 确认。数量应全部“一致”，但这不包含文件哈希校验；还需确认备份清单 `missing` 为空，核对恢复实体 SHA-256，并抽查文件可打开。

### 5. 启动并检查

```powershell
Start-Service docbase-api, docbase-worker, docbase-proxy
```

用旧的管理员账号登录，抽查：项目数、几个应交项的状态、打开几个文件。

### 6. 切换访问地址

- 新机器用旧机器的 IP（旧机器先关机或改 IP），大家的书签不用改；
- 或者用新 IP：在新机器的 `proxy\Caddyfile` 里确认地址，并通知大家新地址。

### 7. 旧机器

确认新机器正常运行一周以上，再处理旧机器：先 `uninstall.ps1`，再按公司规定清除旧盘上的数据（`E:\docbase-data`、备份目录、`C:\docbase\config`）。

## 迁移到 Linux

数据库和文件的备份格式在 Windows 和 Linux 上一样：在 Linux 上按 `install-linux.md` 装好后，用同样的 `restore.py` 恢复即可。
