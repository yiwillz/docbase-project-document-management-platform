# 把 docbase 迁移到新机器（Windows）

适用：换电脑、换服务器、旧机器要报废。迁移后数据（数据库 + 文件）完全一样，用户、权限、历史记录都保留。

准备：新机器按 `install-windows.md` 第 2、3 节准备好（Python 3.12、PostgreSQL、外接盘、固定 IP）；
**新旧机器用同一个 docbase 版本**（版本不同的话，先在旧机器上升级到同一版本再迁移）。

## 步骤

### 1. 在新机器上安装

按 `install-windows.md` 第 4 节正常安装，建一个临时管理员即可（恢复后会被旧数据里的账号替换）。

```powershell
.\install.ps1 -DataDir E:\docbase-data
```

装好后先停掉新机器上的服务：

```powershell
Stop-Service docbase-proxy, docbase-worker, docbase-api
```

### 2. 在旧机器上做最后一次备份

通知大家停止使用，然后在旧机器上：

```powershell
Stop-Service docbase-proxy, docbase-api          # 先停对外服务，确保没人再改
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\backup.py
Stop-Service docbase-worker
```

### 3. 把备份和证书拷到新机器

- **整个备份目录**（如 `C:\docbase-backup`）复制到新机器的备份目录。
- 想让各电脑不用重新安装根证书：把旧机器的 `C:\docbase\proxy\data` 文件夹复制到新机器同一位置（覆盖）。
- 如果用的是公司证书：把 `C:\docbase\certs\` 里的证书和私钥一起复制。

### 4. 在新机器上恢复

```powershell
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\restore.py --list
..\..\venv\Scripts\python.exe scripts\restore.py --to docbase --data-dir E:\docbase-data --force
```

按提示输入库名 `docbase` 确认。最后列出的数量要全部"一致"。

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
