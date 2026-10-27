<#
docbase 升级。在解压后的**新版本**安装包目录里，用"以管理员身份运行"的 PowerShell 执行：

  Set-ExecutionPolicy -Scope Process Bypass
  .\upgrade.ps1

做的事：备份 → 停服务 → 换程序（旧的留在 app.previous）→ 装依赖 → 升级数据库结构 → 启动 → 检查。
配置文件、文件存储、备份都不动。失败时屏幕上会给出回退步骤。
#>
param(
    [string]$InstallDir = 'C:\docbase',
    [switch]$SkipBackup
)

. (Join-Path $PSScriptRoot 'common.ps1')
trap { Show-Failure $_; exit 1 }
$Package = $PSScriptRoot
$Version = (Get-Content (Join-Path $Package 'VERSION') -Raw).Trim()
$EnvFile = Join-Path $InstallDir 'config\docbase.env'
if (-not (Test-Admin)) { Stop-WithError '请用"以管理员身份运行"的 PowerShell 执行' }
if (-not (Test-Path $EnvFile)) { Stop-WithError "$InstallDir 下没有已安装的 docbase（找不到 $EnvFile）；新安装请用 install.ps1" }
$Old = 'unknown'
if (Test-Path (Join-Path $InstallDir 'app\VERSION')) { $Old = (Get-Content (Join-Path $InstallDir 'app\VERSION') -Raw).Trim() }
Write-Host "docbase 升级：$Old → $Version" -ForegroundColor Cyan
$Python = Find-Python312
if (-not $Python) { Stop-WithError '没找到 Python 3.12' }

# ---------------------------------------------------------------- 1. 备份
Write-Step '1/6 升级前备份'
if ($SkipBackup) {
    Write-Warn2 '跳过了备份（-SkipBackup）'
} else {
    Invoke-BackendScript $InstallDir @('scripts\backup.py')
}

# ---------------------------------------------------------------- 2. 停服务、换程序
Write-Step '2/6 停服务、换程序'
Stop-DocbaseServices
$prev = Join-Path $InstallDir 'app.previous'
if (Test-Path $prev) { Remove-Item $prev -Recurse -Force }
Copy-Item (Join-Path $InstallDir 'app') $prev -Recurse
Copy-App $Package $InstallDir
Write-Ok "旧版本留在 $prev"

$rollback = @"
回退方法：
  1. Stop-Service docbase-proxy, docbase-worker, docbase-api
  2. 删除 $InstallDir\app，把 $prev 改名为 app
  3. 如果数据库结构已经升级：python scripts\restore.py --list 找到刚才的备份点，
     再 python scripts\restore.py --stamp <备份点> --to <库名> --data-dir <文件目录> --force（见 docs\install-windows.md）
  4. Start-Service docbase-api, docbase-worker, docbase-proxy
"@

$script:OnFail = $rollback
# ---------------------------------------------------------------- 3. 依赖
& {
    Write-Step '3/6 安装 Python 依赖'
    Install-Dependencies $Package $InstallDir $Python
    # ------------------------------------------------------------ 4. 数据库结构
    Write-Step '4/6 升级数据库结构'
    Invoke-BackendScript $InstallDir @('scripts\migrate.py')
    # ------------------------------------------------------------ 5. 服务配置（新版本可能改了）、启动
    Write-Step '5/6 更新服务配置并启动'
    $Pg = Find-PostgreSQL
    $ProxyDir = Join-Path $InstallDir 'proxy'
    Copy-Item (Join-Path $Package 'bin\caddy.exe') (Join-Path $ProxyDir 'caddy.exe') -Force
    $depend = $(if ($Pg -and $Pg.Service) { "<depend>$($Pg.Service)</depend>" } else { '' })
    $values = @{
        PYTHON = (Join-Path $InstallDir 'venv\Scripts\python.exe'); BACKEND_DIR = (Join-Path $InstallDir 'app\backend')
        ENV_FILE = $EnvFile; LOG_DIR = (Join-Path $InstallDir 'logs'); API_PORT = $ApiPort; DEPEND = $depend; PROXY_DIR = $ProxyDir
    }
    foreach ($id in @('docbase-api', 'docbase-worker', 'docbase-proxy')) { Register-DocbaseService $Package $InstallDir $id $values }
    Start-DocbaseServices
    # ------------------------------------------------------------ 6. 检查
    Write-Step '6/6 检查'
    $h = Wait-Health "http://127.0.0.1:$ApiPort/api/health" 120
    Write-Ok "后端正常：版本 $($h.version)"
}
$script:OnFail = $null
Write-Host ''
Write-Host "升级完成：$Old → $Version" -ForegroundColor Green
