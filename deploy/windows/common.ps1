# install.ps1、upgrade.ps1、uninstall.ps1 共用的函数（Windows PowerShell 5.1 可用）
# 本文件要存成带 BOM 的 UTF-8，否则中文 Windows 上的 PowerShell 5.1 会读成乱码

$ErrorActionPreference = 'Stop'
$Services = @('docbase-proxy', 'docbase-worker', 'docbase-api')   # 停的顺序；启动反过来
$ApiPort = 8000

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) { Write-Host "   $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Write-Warn2([string]$Text) { Write-Host "   注意：$Text" -ForegroundColor Yellow }

# 出错：抛出，由各脚本开头的 trap 统一显示（有回退说明的一起显示）后退出
$script:OnFail = $null
function Stop-WithError([string]$Text) { throw "DOCBASE: $Text" }

function Show-Failure($Err) {
    $msg = [string]$Err.Exception.Message
    Write-Host ''
    if ($msg.StartsWith('DOCBASE: ')) {
        Write-Host "失败：$($msg.Substring(9))" -ForegroundColor Red
    } else {
        Write-Host "失败：$msg" -ForegroundColor Red
        Write-Host $Err.InvocationInfo.PositionMessage -ForegroundColor DarkGray
    }
    if ($script:OnFail) { Write-Host $script:OnFail -ForegroundColor Yellow }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function ConvertFrom-ProtectedString([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Test-PathWithin([string]$Candidate, [string]$Parent) {
    $childPath = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $parentPath = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    return $childPath.Equals($parentPath, [StringComparison]::OrdinalIgnoreCase) -or
        $childPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Invoke-NativeCapture([string]$Executable, [string[]]$Arguments) {
    # PowerShell 5.1 会把 native stderr 转成 ErrorRecord；以退出码判断成败。
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Executable @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    return @{ Code=$code; Output=($output | Out-String).Trim() }
}

function Assert-InstallPackage([string]$PackageDir) {
    $required = @('VERSION', 'app\backend\app\main.py', 'app\frontend\dist\index.html',
        'wheelhouse', 'requirements.txt', 'bin\WinSW-x64.exe', 'bin\caddy.exe',
        'templates\docbase.env.template', 'templates\docbase-api.xml',
        'templates\docbase-worker.xml', 'templates\docbase-proxy.xml', 'templates\Caddyfile.template')
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $PackageDir $_)) })
    if ($missing.Count -gt 0) {
        Stop-WithError ('当前目录不是完整的 Windows 安装包。请从 release 下解压后的安装包根目录运行；' +
            '源码 deploy\windows 不能直接安装。缺少：' + ($missing -join ', '))
    }
}

# 写 UTF-8（不带 BOM）：.env、Caddyfile、xml 都这样写
function Write-Utf8([string]$Path, [string]$Text) {
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

# 按模板生成文件：{{名字}} 换成值
function Expand-Template([string]$Source, [string]$Target, [hashtable]$Values) {
    $text = [IO.File]::ReadAllText($Source, [Text.Encoding]::UTF8)
    foreach ($k in $Values.Keys) { $text = $text.Replace("{{$k}}", [string]$Values[$k]) }
    if ($text -match '\{\{[A-Z_]+\}\}') { Stop-WithError "模板 $Source 里还有没填的占位符：$($Matches[0])" }
    Write-Utf8 $Target $text
}

# 随机密码（只用字母数字，放进数据库连接串不用转义）
function New-Secret([int]$Length = 24) {
    $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'.ToCharArray()
    $bytes = New-Object byte[] $Length
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# 找 Python 3.12（安装包里的依赖是给 3.12 准备的）
function Find-Python312 {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $candidates += (& py -3.12 -c "import sys; print(sys.executable)" 2>$null) } catch {}
    }
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", 'C:\Program Files\Python312\python.exe' -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($c in $candidates) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -eq '3.12') { return $c }
    }
    return $null
}

# 找 PostgreSQL（16 或 17）：命令目录、版本、Windows 服务名
function Find-PostgreSQL {
    $base = Join-Path $env:ProgramFiles 'PostgreSQL'
    $found = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+$' -and (Test-Path (Join-Path $_.FullName 'bin\pg_dump.exe')) } |
        Sort-Object { [int]$_.Name } -Descending | Select-Object -First 1
    if (-not $found) { return $null }
    $svc = Get-Service "postgresql*$($found.Name)*" -ErrorAction SilentlyContinue | Select-Object -First 1
    return @{ Bin = (Join-Path $found.FullName 'bin'); Version = [int]$found.Name; Service = $(if ($svc) { $svc.Name } else { $null }) }
}

# 等健康检查通过（后端起来、数据库和文件存储都正常）
function Wait-Health([string]$Url, [int]$Seconds = 90) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    $last = ''
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Uri $Url -TimeoutSec 5 -UseBasicParsing
            if ($r.ok) { return $r }
            $last = "db=$($r.db) storage=$($r.storage)"
        } catch { $last = $_.Exception.Message }
        Start-Sleep -Seconds 2
    }
    Stop-WithError "健康检查没通过（$Url）：$last。看日志：安装目录\logs"
}

# 在安装目录的 Python 里跑 backend\scripts 下的脚本（带上配置文件位置）
function Invoke-BackendScript([string]$InstallDir, [string[]]$Arguments) {
    $py = Join-Path $InstallDir 'venv\Scripts\python.exe'
    $env:DOCBASE_ENV_FILE = Join-Path $InstallDir 'config\docbase.env'
    $env:PYTHONIOENCODING = 'utf-8'
    Push-Location (Join-Path $InstallDir 'app\backend')
    try {
        & $py @Arguments
        if ($LASTEXITCODE -ne 0) { Stop-WithError "运行 $($Arguments -join ' ') 失败" }
    } finally { Pop-Location }
}

# 复制程序文件（app 目录）：先复制到临时目录，再整体换上
function Copy-App([string]$PackageDir, [string]$InstallDir) {
    $src = Join-Path $PackageDir 'app'
    $dst = Join-Path $InstallDir 'app'
    robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /NP /R:2 /W:2 | Out-Null
    if ($LASTEXITCODE -ge 8) { Stop-WithError "复制程序文件失败（robocopy 代码 $LASTEXITCODE）" }
    Copy-Item (Join-Path $PackageDir 'VERSION') (Join-Path $InstallDir 'app\VERSION') -Force
}

# 建虚拟环境、从安装包离线装依赖（不需要上网）
function Install-Dependencies([string]$PackageDir, [string]$InstallDir, [string]$Python) {
    $venv = Join-Path $InstallDir 'venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { Stop-WithError '建虚拟环境失败' }
    }
    $py = Join-Path $venv 'Scripts\python.exe'
    & $py -m pip install --no-index --find-links (Join-Path $PackageDir 'wheelhouse') -r (Join-Path $PackageDir 'requirements.txt') --quiet --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Stop-WithError '安装 Python 依赖失败' }
}

# 用 WinSW 注册（或更新）一个服务
function Register-DocbaseService([string]$PackageDir, [string]$InstallDir, [string]$Id, [hashtable]$Values) {
    $svcDir = Join-Path $InstallDir 'services'
    New-Item -ItemType Directory -Force $svcDir | Out-Null
    $exe = Join-Path $svcDir "$Id.exe"
    Copy-Item (Join-Path $PackageDir 'bin\WinSW-x64.exe') $exe -Force
    Expand-Template (Join-Path $PackageDir "templates\$Id.xml") (Join-Path $svcDir "$Id.xml") $Values
    if (Get-Service $Id -ErrorAction SilentlyContinue) {
        # WinSW 2.x 不能就地改配置：停掉、删掉、等 Windows 真正删完，再按新配置注册
        $null = Invoke-NativeCapture $exe @('stopwait')
        $removed = Invoke-NativeCapture $exe @('uninstall')
        if ($removed.Code -ne 0) { Stop-WithError "卸载旧服务 $Id 失败：$($removed.Output)" }
        for ($i = 0; $i -lt 30 -and (Get-Service $Id -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }
        if (Get-Service $Id -ErrorAction SilentlyContinue) {
            Stop-WithError "服务 $Id 删不掉（可能服务管理窗口还开着）：关掉 services.msc 后重新运行"
        }
    }
    $registered = Invoke-NativeCapture $exe @('install')
    if ($registered.Code -ne 0) { Stop-WithError "注册服务 $Id 失败：$($registered.Output)" }
}

function Stop-DocbaseServices {
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -Force -ErrorAction SilentlyContinue }
    }
}

function Start-DocbaseServices {
    foreach ($s in $Services[($Services.Count - 1)..0]) {
        if (Get-Service $s -ErrorAction SilentlyContinue) { Start-Service $s }
    }
}
