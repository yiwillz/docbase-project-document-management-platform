<#
docbase 一键安装（Windows 10/11）。在解压后的安装包目录里，用"以管理员身份运行"的 PowerShell 执行：

  Set-ExecutionPolicy -Scope Process Bypass
  .\install.ps1 -DataDir E:\docbase-data

常用参数：
  -DataDir      文件存储目录（外接盘），必填
  -BackupDir    备份目录，默认 C:\docbase-backup
  -InstallDir   安装目录，默认 C:\docbase
  -Address      访问地址（IP 或名字，多个用逗号分隔），默认本机的局域网 IP
  -DatabaseUrl  用已有的数据库（不自动建库）；不给就用 PostgreSQL 超级用户自动建库和账号
  -NoService    试装：不注册服务、不开防火墙、不建 HTTPS，只装好并启动一次后端检查（开发验证用）

先装好：Python 3.12（勾选 Add to PATH）、PostgreSQL 16 或 17。详细说明见 docs\install-windows.md。
#>
param(
    [Parameter(Mandatory = $true)][string]$DataDir,
    [string]$BackupDir = 'C:\docbase-backup',
    [string]$InstallDir = 'C:\docbase',
    [string]$Address = '',
    [string]$DatabaseUrl = '',
    [string]$PgSuperUser = 'postgres',
    [string]$DbName = 'docbase',
    [switch]$NoService,
    [switch]$SkipAdmin
)

. (Join-Path $PSScriptRoot 'common.ps1')
trap { Show-Failure $_; exit 1 }
$Package = $PSScriptRoot
$Version = (Get-Content (Join-Path $Package 'VERSION') -Raw).Trim()
Write-Host "docbase $Version 安装" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. 检查
Write-Step '1/9 检查环境'
if (-not $NoService -and -not (Test-Admin)) { Stop-WithError '请用"以管理员身份运行"的 PowerShell 执行（注册服务需要管理员权限）' }
foreach ($need in @('app\backend\app\main.py', 'app\frontend\dist\index.html', 'wheelhouse', 'requirements.txt', 'bin\WinSW-x64.exe', 'bin\caddy.exe')) {
    if (-not (Test-Path (Join-Path $Package $need))) { Stop-WithError "安装包不完整，缺 $need（请重新解压安装包）" }
}
$Python = Find-Python312
if (-not $Python) { Stop-WithError '没找到 Python 3.12。请先安装 Python 3.12（安装时勾选 Add python.exe to PATH），再运行本脚本' }
Write-Ok "Python：$Python"
$Pg = Find-PostgreSQL
if (-not $Pg) { Stop-WithError '没找到 PostgreSQL。请先安装 PostgreSQL 17（或 16），记住安装时设的 postgres 密码' }
if ($Pg.Version -lt 16) { Stop-WithError "PostgreSQL 版本是 $($Pg.Version)，需要 16 或更新" }
Write-Ok "PostgreSQL $($Pg.Version)：$($Pg.Bin)（服务 $($Pg.Service)）"
$DataDir = [IO.Path]::GetFullPath($DataDir)
$BackupDir = [IO.Path]::GetFullPath($BackupDir)
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if ($DataDir.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase)) { Stop-WithError '文件存储目录不要放在安装目录里面（升级会换掉安装目录里的程序）' }
foreach ($d in @($DataDir, $BackupDir)) {
    try { New-Item -ItemType Directory -Force $d | Out-Null; $probe = Join-Path $d '.probe'; 'ok' | Set-Content $probe; Remove-Item $probe }
    catch { Stop-WithError "目录 $d 不能写：$($_.Exception.Message)" }
}
if ((Split-Path $DataDir -Qualifier) -eq (Split-Path $BackupDir -Qualifier)) {
    Write-Warn2 '文件存储和备份在同一块盘上：盘坏了两份一起丢。建议备份放到另一块盘或 NAS（以后改配置文件里的 DOCBASE_BACKUP_DIR）'
}
if (-not $Address) {
    $Address = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress
    if (-not $Address) { $Address = 'localhost' }
}
Write-Ok "访问地址：https://$($Address.Split(',')[0].Trim())"

# ---------------------------------------------------------------- 2. 程序文件
Write-Step '2/9 复制程序文件'
foreach ($d in @('config', 'logs', 'certs')) { New-Item -ItemType Directory -Force (Join-Path $InstallDir $d) | Out-Null }
if (-not $NoService) { Stop-DocbaseServices }   # 重新安装时先停掉
Copy-App $Package $InstallDir
Write-Ok "已复制到 $InstallDir\app"

# ---------------------------------------------------------------- 3. Python 依赖
Write-Step '3/9 安装 Python 依赖（离线，不需要上网）'
Install-Dependencies $Package $InstallDir $Python
Write-Ok '完成'

# ---------------------------------------------------------------- 4. 数据库
Write-Step '4/9 数据库'
$EnvFile = Join-Path $InstallDir 'config\docbase.env'
if (Test-Path $EnvFile) {
    Write-Ok "已有配置文件 $EnvFile，沿用其中的数据库（重新安装 / 修复）"
} elseif ($DatabaseUrl) {
    Write-Ok '用指定的数据库'
} else {
    $secure = Read-Host "请输入 PostgreSQL 超级用户 $PgSuperUser 的密码（安装 PostgreSQL 时设的）" -AsSecureString
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    $psql = Join-Path $Pg.Bin 'psql.exe'
    $role = 'docbase'
    $dbPassword = New-Secret
    function Invoke-Psql([string]$Sql) {
        $out = & $psql -h localhost -U $PgSuperUser -d postgres -v ON_ERROR_STOP=1 -tA -c $Sql 2>&1
        if ($LASTEXITCODE -ne 0) { Stop-WithError "数据库操作失败：$out" }
        return ($out | Out-String).Trim()
    }
    if ((Invoke-Psql "SELECT 1 FROM pg_roles WHERE rolname = '$role'") -eq '1') {
        Invoke-Psql "ALTER ROLE $role LOGIN PASSWORD '$dbPassword'" | Out-Null
    } else {
        Invoke-Psql "CREATE ROLE $role LOGIN PASSWORD '$dbPassword'" | Out-Null
    }
    if ((Invoke-Psql "SELECT 1 FROM pg_database WHERE datname = '$DbName'") -eq '1') {
        Write-Warn2 "数据库 $DbName 已经存在，沿用（不会清空）"
        Invoke-Psql "ALTER DATABASE $DbName OWNER TO $role" | Out-Null
    } else {
        Invoke-Psql "CREATE DATABASE $DbName OWNER $role ENCODING 'UTF8' TEMPLATE template0" | Out-Null
    }
    Remove-Item Env:\PGPASSWORD
    $DatabaseUrl = "postgresql+psycopg://${role}:$dbPassword@localhost:5432/$DbName"
    Write-Ok "数据库 $DbName、账号 $role（随机密码，写在配置文件里）"
}

# ---------------------------------------------------------------- 5. 配置文件
Write-Step '5/9 配置文件'
if (-not (Test-Path $EnvFile)) {
    Expand-Template (Join-Path $Package 'templates\docbase.env.template') $EnvFile @{
        DATABASE_URL = $DatabaseUrl; DATA_DIR = $DataDir; BACKUP_DIR = $BackupDir
        LOG_DIR = (Join-Path $InstallDir 'logs'); FRONTEND_DIR = (Join-Path $InstallDir 'app\frontend\dist'); PG_BIN = $Pg.Bin
    }
    # 配置文件里有数据库密码：只给管理员组和系统账号读（用 SID，中文系统上组名不一样）
    if (-not $NoService) { icacls $EnvFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null }
    Write-Ok "已写 $EnvFile"
} else {
    Write-Ok '沿用已有的配置文件'
}

# ---------------------------------------------------------------- 6. 数据库结构、第一个管理员
Write-Step '6/9 升级数据库结构'
Invoke-BackendScript $InstallDir @('scripts\migrate.py')
if (-not $SkipAdmin) {
    Write-Step '   第一个系统管理员'
    Invoke-BackendScript $InstallDir @('scripts\create_user.py', '--admin', '--if-none')
}

if ($NoService) {
    # ------------------------------------------------------------ 试装：直接起一次后端检查
    Write-Step '7/9 试装：启动一次后端检查（不注册服务）'
    $py = Join-Path $InstallDir 'venv\Scripts\python.exe'
    $env:DOCBASE_ENV_FILE = $EnvFile
    $proc = Start-Process $py -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8099' `
        -WorkingDirectory (Join-Path $InstallDir 'app\backend') -PassThru -WindowStyle Hidden
    try {
        $h = Wait-Health 'http://127.0.0.1:8099/api/health' 90
        Write-Ok "后端正常：数据库 $($h.database)、版本 $($h.version)"
        $page = Invoke-WebRequest 'http://127.0.0.1:8099/projects' -UseBasicParsing -TimeoutSec 10
        if ($page.Content -notmatch '<div id="app"') { Stop-WithError '页面没有正常返回' }
        Write-Ok '页面正常'
    } finally {
        taskkill /PID $proc.Id /T /F | Out-Null
    }
    Write-Host ''
    Write-Host "试装完成（没有注册服务）：$InstallDir" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------- 7. HTTPS 代理
Write-Step '7/9 HTTPS 代理（Caddy）'
$ProxyDir = Join-Path $InstallDir 'proxy'
New-Item -ItemType Directory -Force $ProxyDir | Out-Null
Copy-Item (Join-Path $Package 'bin\caddy.exe') (Join-Path $ProxyDir 'caddy.exe') -Force
$sites = ($Address.Split(',') | ForEach-Object { "https://$($_.Trim())" }) + 'https://localhost'
Expand-Template (Join-Path $Package 'templates\Caddyfile.template') (Join-Path $ProxyDir 'Caddyfile') @{
    SITES = ($sites | Select-Object -Unique) -join ', '; API_PORT = $ApiPort; LOG_DIR = (Join-Path $InstallDir 'logs')
}
& (Join-Path $ProxyDir 'caddy.exe') validate --config (Join-Path $ProxyDir 'Caddyfile') --adapter caddyfile 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-WithError "Caddyfile 检查没通过：$ProxyDir\Caddyfile" }
Write-Ok "已生成 $ProxyDir\Caddyfile"

# ---------------------------------------------------------------- 8. 注册服务
Write-Step '8/9 注册 Windows 服务（开机自动启动，崩溃自动重启）'
$depend = $(if ($Pg.Service) { "<depend>$($Pg.Service)</depend>" } else { '' })
$values = @{
    PYTHON = (Join-Path $InstallDir 'venv\Scripts\python.exe'); BACKEND_DIR = (Join-Path $InstallDir 'app\backend')
    ENV_FILE = $EnvFile; LOG_DIR = (Join-Path $InstallDir 'logs'); API_PORT = $ApiPort; DEPEND = $depend; PROXY_DIR = $ProxyDir
}
foreach ($id in @('docbase-api', 'docbase-worker', 'docbase-proxy')) {
    Register-DocbaseService $Package $InstallDir $id $values
    Write-Ok "已注册 $id"
}
# 防火墙：只对"专用 / 域"网络开 443、80（80 只用来跳转到 https）
Get-NetFirewallRule -DisplayName 'docbase HTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'docbase HTTPS' -Direction Inbound -Protocol TCP -LocalPort 443, 80 -Action Allow -Profile Domain, Private | Out-Null
Write-Ok '防火墙已放行 443、80（专用 / 域网络）'
Start-DocbaseServices

# ---------------------------------------------------------------- 9. 检查
Write-Step '9/9 检查'
$h = Wait-Health "http://127.0.0.1:$ApiPort/api/health" 120
Write-Ok "后端正常：数据库 $($h.database)、文件存储正常、版本 $($h.version)"
# 内网证书：Caddy 第一次启动时自己生成根证书，导出给其他电脑安装
$root = Join-Path $ProxyDir 'data\caddy\pki\authorities\local\root.crt'
for ($i = 0; $i -lt 30 -and -not (Test-Path $root); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $root) {
    Copy-Item $root (Join-Path $InstallDir 'certs\docbase-root.crt') -Force
    Import-Certificate -FilePath $root -CertStoreLocation Cert:\LocalMachine\Root | Out-Null   # 本机浏览器也信任
    Write-Ok "根证书：$InstallDir\certs\docbase-root.crt（发给每台要访问的电脑安装一次）"
} else {
    Write-Warn2 '没找到 Caddy 的根证书，HTTPS 可能还没准备好；稍后看 logs\service 里 docbase-proxy 的日志'
}
$https = & curl.exe -sk -o NUL -w '%{http_code}' "https://localhost/api/health"
if ($https -eq '200') { Write-Ok 'HTTPS 正常' } else { Write-Warn2 "HTTPS 检查返回 $https，稍后再试或看 docbase-proxy 日志" }

Write-Host ''
Write-Host '安装完成' -ForegroundColor Green
Write-Host "  访问地址：   https://$($Address.Split(',')[0].Trim())"
Write-Host "  安装目录：   $InstallDir（配置 config\docbase.env、日志 logs）"
Write-Host "  文件存储：   $DataDir"
Write-Host "  备份目录：   $BackupDir（每天 1 点自动备份，保留 30 天）"
Write-Host '  服务：       docbase-api、docbase-worker、docbase-proxy（services.msc 里能看到）'
Write-Host '  下一步：     把 certs\docbase-root.crt 装到每台访问的电脑上（见 docs\install-windows.md）'
