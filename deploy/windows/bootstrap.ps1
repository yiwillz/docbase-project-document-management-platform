<# 在线一键部署：不需要预装 Python、PostgreSQL、Git。
   源码：powershell -ExecutionPolicy Bypass -File .\deploy\windows\bootstrap.ps1
   安装包：powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
   会申请管理员权限；首次运行需联网。密码保存到安装目录 config\setup-credentials.json，只有管理员和 SYSTEM 可读。
#>
param(
    [string]$InstallDir = 'C:\docbase',
    [string]$DataDir = 'C:\docbase-data',
    [string]$BackupDir = 'C:\docbase-backup',
    [string]$Address = 'localhost',
    [Security.SecureString]$PostgresPassword,
    [switch]$UseExistingFrontend,
    [switch]$PrepareOnly
)

. (Join-Path $PSScriptRoot 'common.ps1')
trap { Show-Failure $_; exit 1 }

if (-not (Test-Admin)) {
    # 将参数编码后传入新的管理员进程，支持中文、空格及单引号路径。
    $values = @{ InstallDir=$InstallDir; DataDir=$DataDir; BackupDir=$BackupDir; Address=$Address }
    $pairs = @($values.Keys | ForEach-Object { "-$_ '" + $values[$_].Replace("'", "''") + "'" })
    if ($UseExistingFrontend) { $pairs += '-UseExistingFrontend' }
    if ($PrepareOnly) { $pairs += '-PrepareOnly' }
    if ($PostgresPassword) {
        $protectedPassword = ConvertFrom-SecureString $PostgresPassword
        $pairs += "-PostgresPassword (ConvertTo-SecureString '$protectedPassword')"
    }
    # 交互安装窗口展示进度；失败时保留窗口，便于新电脑的用户查看原因。
    $command = "& '" + $PSCommandPath.Replace("'", "''") + "' " + ($pairs -join ' ') + '; $deployExitCode = $LASTEXITCODE; if ($deployExitCode -ne 0) { Read-Host ''Installation failed. Press Enter to close'' }; exit $deployExitCode'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $proc = Start-Process powershell.exe -Verb RunAs -WindowStyle Normal -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded" -Wait -PassThru
    if ($proc.ExitCode -ne 0) { Stop-WithError "管理员部署进程失败，退出码 $($proc.ExitCode)" }
    exit 0
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not [Environment]::Is64BitOperatingSystem) { Stop-WithError '需要 64 位 Windows' }
foreach ($path in @($InstallDir, $DataDir, $BackupDir)) {
    if (-not [IO.Path]::IsPathRooted($path)) { Stop-WithError '安装、数据、备份目录必须是绝对路径' }
}
$config = Join-Path $InstallDir 'config'
New-Item -ItemType Directory -Force $config | Out-Null
$bootstrapLog = Join-Path $InstallDir 'logs\bootstrap.log'
New-Item -ItemType Directory -Force (Split-Path $bootstrapLog -Parent) | Out-Null
Start-Transcript -Path $bootstrapLog -Append | Out-Null
$credentialsFile = Join-Path $config 'setup-credentials.json'
if (-not (Test-Path -LiteralPath $credentialsFile)) {
    # 先建空文件并收紧权限，再写密码，避免继承目录权限造成泄漏。
    Write-Utf8 $credentialsFile '{}'
    & icacls.exe $credentialsFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-WithError '无法保护安装凭据文件' }
    $credentials = @{ postgres_password=(New-Secret 32); admin_user='admin'; admin_password=(New-Secret 24) }
    Write-Utf8 $credentialsFile ($credentials | ConvertTo-Json)
}
$credentials = Get-Content -LiteralPath $credentialsFile -Raw | ConvertFrom-Json

function Install-WingetPackage([string]$Id, [string]$Override = '') {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { Stop-WithError '找不到 winget，请先安装或更新微软 App Installer' }
    $arguments = @('install', '--id', $Id, '--exact', '--source', 'winget', '--silent',
        '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    if ($Override) { $arguments += @('--override', $Override) }
    & winget.exe @arguments
    if ($LASTEXITCODE -ne 0) { Stop-WithError "安装 $Id 失败（$LASTEXITCODE），请检查上面的 winget 输出后重试" }
}

Write-Step '1/4 准备 Python 3.12'
$Python = Find-Python312
if (-not $Python) {
    Install-WingetPackage 'Python.Python.3.12'
    $Python = Find-Python312
}
if (-not $Python) { Stop-WithError 'Python 安装后仍未找到 3.12 解释器' }
Write-Ok "Python: $Python"

Write-Step '2/4 准备 PostgreSQL 17'
$Pg = Find-PostgreSQL
$pgPassword = $PostgresPassword
if (-not $Pg) {
    if (Get-NetTCPConnection -State Listen -LocalPort 5432 -ErrorAction SilentlyContinue) { Stop-WithError '5432 端口已被占用，请检查已有数据库，脚本不会覆盖它' }
    $options = Join-Path $config 'postgres-install-options.txt'
    try {
        Write-Utf8 $options ''
        & icacls.exe $options /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
        if ($LASTEXITCODE -ne 0) { Stop-WithError '无法保护数据库安装参数' }
        Write-Utf8 $options ("superpassword=" + $credentials.postgres_password + "`nserverport=5432`ndisable-components=pgAdmin,stackbuilder`n")
        # EDB 支持 optionfile：密码不进入安装程序命令行。
        $quotedOptions = '\"' + $options + '\"'
        Install-WingetPackage 'PostgreSQL.PostgreSQL.17' ("--mode unattended --unattendedmodeui none --optionfile " + $quotedOptions)
    } finally { Remove-Item -LiteralPath $options -Force -ErrorAction SilentlyContinue }
    $Pg = Find-PostgreSQL
    if (-not $Pg) { Stop-WithError 'PostgreSQL 安装后未找到，请检查安装日志' }
    $pgPassword = ConvertTo-SecureString $credentials.postgres_password -AsPlainText -Force
}
if ($Pg.Version -notin @(16, 17)) { Stop-WithError '目前仅支持 PostgreSQL 16/17' }
if ($Pg.Service) { Start-Service $Pg.Service }
Write-Ok "PostgreSQL: $($Pg.Bin)"
if ($PrepareOnly) { Write-Ok '环境准备完成'; exit 0 }

Write-Step '3/4 准备当前版本安装包'
if (Test-Path (Join-Path $PSScriptRoot 'app\backend\app\main.py')) {
    $package = $PSScriptRoot
} else {
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    if (-not (Test-Path (Join-Path $root 'backend\pyproject.toml'))) { Stop-WithError '缺少源码或完整发布包' }
    if (-not $UseExistingFrontend) {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
            Install-WingetPackage 'OpenJS.NodeJS.LTS'
            $env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
        }
        Push-Location (Join-Path $root 'frontend')
        try {
            & npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { Stop-WithError '前端依赖安装失败' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { Stop-WithError '前端构建失败' }
        } finally { Pop-Location }
    }
    $version = '0.1.0-local-' + (Get-Date -Format yyyyMMddHHmmss)
    $packageRoot = Join-Path $InstallDir 'packages'
    & $Python (Join-Path $root 'backend\scripts\release.py') --target windows --skip-build --version $version --output-dir $packageRoot
    if ($LASTEXITCODE -ne 0) { Stop-WithError '安装包构建失败' }
    $package = Join-Path $packageRoot "docbase-$version-windows"
}
Assert-InstallPackage $package

Write-Step '4/4 安装服务并验证'
if (-not $pgPassword -and -not (Test-Path (Join-Path $config 'docbase.env'))) {
    # 失败后重试沿用同一份生成凭据；不在隐藏的管理员进程里等待交互输入。
    $pgPassword = ConvertTo-SecureString $credentials.postgres_password -AsPlainText -Force
}
$adminPassword = ConvertTo-SecureString $credentials.admin_password -AsPlainText -Force
& (Join-Path $package 'install.ps1') -InstallDir $InstallDir -DataDir $DataDir -BackupDir $BackupDir `
    -Address $Address -PgPassword $pgPassword -AdminUser $credentials.admin_user -AdminPassword $adminPassword -EnvironmentReady
if ($LASTEXITCODE -ne 0) { Stop-WithError '安装失败，请检查上面的错误和安装目录日志' }
& (Join-Path $InstallDir 'venv\Scripts\python.exe') (Join-Path $InstallDir 'app\backend\scripts\verify_deployment.py') --install-dir $InstallDir --restore-drill
if ($LASTEXITCODE -ne 0) { Stop-WithError '部署后的 HTTPS、登录或备份恢复验证失败，请查看日志' }
Write-Ok "部署完成，初始账号和随机密码请以管理员身份读取 $credentialsFile；已有账号不会被重置"
exit 0
