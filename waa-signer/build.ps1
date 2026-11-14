# Requires -Version 5.1
<#
.SYNOPSIS
    一键编译 WAA Signer (Windows x64 / Android arm64)
.DESCRIPTION
    自动检测环境、检测系统/进程代理、下载 SpiderMonkey 静态依赖，
    并通过 Go + CGO (Zig / MinGW-w64 GCC / Clang) 构建独立的 waa-signer.exe 或 Android arm64 静态产物。
#>
[CmdletBinding()]
param(
    [string]$Target = "windows-amd64",
    [string]$Proxy = "",
    [string]$LocalFile = "",
    [switch]$ForceDownload
)

$ErrorActionPreference = "Stop"

# 统一控制台编码为 UTF-8，彻底杜绝 CMD 与 Windows Terminal 乱码
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# 适配 Windows PowerShell 5.1 默认老旧 TLS 协议，强制启用 TLS 1.2 与 TLS 1.3
try {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    if ([System.Enum]::IsDefined([System.Net.SecurityProtocolType], "Tls13")) {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls13
    }
} catch {}

# 获取脚本所在根目录（使用可靠的 $PSScriptRoot，避免 Split-Path 对 System.Object 的类型转换报错）
$ScriptDir = ""
if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $ScriptDir = $PSScriptRoot
} else {
    $ScriptDir = (Get-Location).Path
}
Set-Location $ScriptDir

$SM_VERSION = "v152.0.4-aistudio2api.1"
switch ($Target) {
    "windows-amd64" {
        $ARCH = "windows-amd64"
        $TargetGOOS = "windows"
        $TargetGOARCH = "amd64"
        $ZigTarget = "x86_64-windows-gnu"
        $OutExe = "$ScriptDir\bin\waa-signer.exe"
        $NeedWinShim = $true
        $RequireZig = $false
        $GoExtLdFlagsBase = ""
    }
    "android-arm64" {
        $ARCH = "linux-arm64"
        $TargetGOOS = "linux"
        $TargetGOARCH = "arm64"
        $ZigTarget = "aarch64-linux-musl"
        $RepoRoot = Split-Path $ScriptDir -Parent
        $OutExe = Join-Path $RepoRoot "android\app\src\main\jniLibs\arm64-v8a\libwaa_signer.so"
        $NeedWinShim = $false
        $RequireZig = $true
        $GoExtLdFlagsBase = "-static"
    }
    default {
        Write-Host "错误: 不支持的目标平台: $Target (支持: windows-amd64, android-arm64)" -ForegroundColor Red
        exit 1
    }
}

$Tarball = "spidermonkey-$SM_VERSION-$ARCH.tar.gz"
$DepsDir = "$ScriptDir\.deps\spidermonkey\$ARCH"
$OutDir = Split-Path $OutExe -Parent

# 1. 检查 Go 环境
if (-not (Get-Command "go" -ErrorAction SilentlyContinue)) {
    Write-Host "错误: 未找到 Go 运行环境。" -ForegroundColor Red
    Write-Host "请先安装 Go 1.22+ 并添加至 PATH:" -ForegroundColor Yellow
    Write-Host "    winget install GoLang.Go" -ForegroundColor White
    Write-Host "或访问官方下载页: https://go.dev/dl/" -ForegroundColor White
    exit 1
}

$goVersionStr = (go version)
Write-Host ">>> 检测到 Go: $goVersionStr" -ForegroundColor Cyan

# 2. 检查 C/C++ 编译器 (Windows 静态构建必须使用内置 LLVM libc++ 的 Zig 或 Clang)
$CC = ""
$CXX = ""
$ExtLdFlags = ""
# 标记是否使用 Zig 工具链（决定是否启用 libjsrust.a 的 Zig 兼容后处理）
$UseZig = $false

# 2.1 探测系统中是否已有 Zig (优先检测 PATH，其次深度扫描 WinGet、Scoop、ProgramFiles 目录)
$zigCmd = Get-Command "zig" -ErrorAction SilentlyContinue
if (-not $zigCmd) {
    # 2.1.1 优先使用 .deps 内置的 Zig 0.13.0（已验证可链接 SpiderMonkey 预编译静态库）
    foreach ($pinnedZig in @(
        "$ScriptDir\.deps\zig\zig-windows-x86_64-0.13.0\zig.exe",
        "$ScriptDir\.deps\zig\zig-x86_64-windows-0.13.0\zig.exe"
    )) {
        if (Test-Path $pinnedZig) {
            $env:PATH = "$(Split-Path $pinnedZig -Parent);$env:PATH"
            $zigCmd = Get-Command "zig" -ErrorAction SilentlyContinue
            Write-Host ">>> 使用内置 Zig 0.13.0: $pinnedZig" -ForegroundColor Green
            break
        }
    }
}
if (-not $zigCmd) {
    $searchPaths = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages",
        "$env:USERPROFILE\scoop\shims",
        "$env:ProgramFiles\zig",
        "$ScriptDir\.deps\zig"
    )
    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            $foundZig = Get-ChildItem -Path $p -Filter "zig.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($foundZig) {
                $zigDir = $foundZig.DirectoryName
                $env:PATH = "$zigDir;$env:PATH"
                $zigCmd = Get-Command "zig" -ErrorAction SilentlyContinue
                Write-Host ">>> 从安装目录自动识别并加载 Zig: $($foundZig.FullName)" -ForegroundColor Green
                break
            }
        }
    }
}

if ($zigCmd) {
    Write-Host ">>> 检测到 Zig 编译器，启用高性能免配置 CGO 静态交叉编译 ($ZigTarget)" -ForegroundColor Green
    $UseZig = $true
    $CC = "zig cc -target $ZigTarget"
    $CXX = "zig c++ -target $ZigTarget"
    $ExtLdFlags = if ($GoExtLdFlagsBase) { "$GoExtLdFlagsBase -Wl,--strip-all" } else { "-Wl,--strip-all" }
} elseif (-not $RequireZig -and (Get-Command "clang" -ErrorAction SilentlyContinue)) {
    Write-Host ">>> 检测到 Clang 编译器" -ForegroundColor Green
    $CC = "clang"
    $CXX = "clang++"
    $ExtLdFlags = "-Wl,--strip-all"
} else {
    $hasGcc = Get-Command "gcc" -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Yellow
    if ($RequireZig) {
        Write-Host "【重要提示】目标 $Target 交叉编译必须使用内置 LLVM libc++ 的 Zig 编译器！" -ForegroundColor Red
    } elseif ($hasGcc) {
        Write-Host "【重要提示】检测到当前系统仅有 GNU GCC (MinGW-w64)，无法直接编译本服务！" -ForegroundColor Red
        Write-Host "原因: SpiderMonkey 官方核心引擎基于 LLVM libc++ 构建，" -ForegroundColor Yellow
        Write-Host "      GNU GCC 缺失 libc++ 与 libunwind 依赖库，会导致 cannot find -lc++ 报错。" -ForegroundColor Yellow
    } else {
        Write-Host "【重要提示】未检测到兼容的 C/C++ 编译器！" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "解决方案: 推荐一键安装免环境配置、仅约 40MB 的 Zig 编译器 (自带完整 LLVM libc++):" -ForegroundColor Green
    Write-Host "    winget install zig.zig" -ForegroundColor White
    Write-Host "==================================================================" -ForegroundColor Yellow
    Write-Host ""

    # 若检测到系统中存在 winget，尝试协助用户一键就绪
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        Write-Host ">>> 检测到系统已内置 winget，是否立即自动执行安装 Zig 编译器？(Y/n): " -NoNewline -ForegroundColor Cyan
        $choice = Read-Host
        if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim().ToUpper() -eq "Y") {
            Write-Host ">>> 正在通过 winget 安装 zig.zig..." -ForegroundColor Cyan
            & winget install -e --id zig.zig --accept-source-agreements --accept-package-agreements
            # 重新扫描探测刚刚安装的 Zig
            if (Test-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages") {
                $foundZig = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter "zig.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($foundZig) {
                    $zigDir = $foundZig.DirectoryName
                    $env:PATH = "$zigDir;$env:PATH"
                    Write-Host ">>> Zig 编译器已就绪: $($foundZig.FullName)" -ForegroundColor Green
                    $UseZig = $true
                    $CC = "zig cc -target $ZigTarget"
                    $CXX = "zig c++ -target $ZigTarget"
                    $ExtLdFlags = if ($GoExtLdFlagsBase) { "$GoExtLdFlagsBase -Wl,--strip-all" } else { "-Wl,--strip-all" }
                }
            }
        }
    }

    if (-not $CC) {
        Write-Host "提示: 请安装 Zig 后重新运行 .\build.bat 即可。" -ForegroundColor Yellow
        exit 1
    }
}

# 代理智能探测函数
function Get-DetectedProxy([string]$explicit) {
    if (-not [string]::IsNullOrWhiteSpace($explicit)) {
        return $explicit.Trim()
    }
    # 检查环境变量
    foreach ($var in @("AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")) {
        $val = [System.Environment]::GetEnvironmentVariable($var)
        if (-not [string]::IsNullOrWhiteSpace($val)) {
            return $val.Trim()
        }
    }
    # 检查 Windows 注册表系统代理 (Clash/v2rayN/Mihomo 系统代理模式)
    try {
        $reg = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
        if ($reg -and $reg.ProxyEnable -eq 1 -and -not [string]::IsNullOrWhiteSpace($reg.ProxyServer)) {
            $raw = [string]$reg.ProxyServer
            $raw = $raw.Trim()
            if ($raw -match "^https?://") {
                return $raw
            }
            if ($raw -match "https?=([^;]+)") {
                return "http://" + $matches[1]
            }
            return "http://" + $raw
        }
    } catch {}
    # 本地常见代理端口探测 (7890 Clash, 10808/10809 v2rayN)
    foreach ($port in @(7890, 10808, 10809)) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $async = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(60, $false) -and $tcp.Connected) {
                $tcp.Close()
                return "http://127.0.0.1:$port"
            }
            $tcp.Close()
        } catch {}
    }
    return ""
}

# 3. 准备依赖目录并下载 SpiderMonkey 静态依赖
$LibStatic = "$DepsDir\lib\libjs_static.a"
if ($ForceDownload -or -not (Test-Path $LibStatic)) {
    if (-not (Test-Path $DepsDir)) {
        New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null
    }

    $EffectiveProxy = Get-DetectedProxy $Proxy
    $TempTarDir = "$ScriptDir\.deps"
    if (-not (Test-Path $TempTarDir)) {
        New-Item -ItemType Directory -Path $TempTarDir -Force | Out-Null
    }
    $TempTar = "$TempTarDir\$Tarball"

    $downloadSuccess = $false

    # 3.1 优先检查显式指定的本地路径或本地下载目录中是否已有安装包 (避免重复下载与网络 504)
    $candidateFiles = @()
    if (-not [string]::IsNullOrWhiteSpace($LocalFile)) {
        $candidateFiles += $LocalFile
    }
    $candidateFiles += $TempTar
    $candidateFiles += "$DepsDir\$Tarball"
    $candidateFiles += "$ScriptDir\$Tarball"
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidateFiles += "$env:USERPROFILE\Downloads\$Tarball"
    }

    foreach ($c in $candidateFiles) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        if ((Test-Path $c) -and (Get-Item $c).Length -gt 10000000) {
            Write-Host ">>> 检测到本地已存在静态依赖包: $c" -ForegroundColor Green
            if ($c -ne $TempTar) {
                Copy-Item $c $TempTar -Force
            }
            $downloadSuccess = $true
            break
        }
    }

    # 3.2 若本地无有效安装包，通过官方源及多镜像源重试下载
    if (-not $downloadSuccess) {
        $officialUrl = "https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball"
        $urlsToTry = @(
            $officialUrl,
            "https://ghproxy.net/$officialUrl",
            "https://gh-proxy.com/$officialUrl",
            "https://mirror.ghproxy.com/$officialUrl"
        )

        if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
            Write-Host ">>> 启用代理: $EffectiveProxy" -ForegroundColor Gray
        }

        foreach ($url in $urlsToTry) {
            Write-Host ">>> 正在下载 SpiderMonkey 静态依赖库: $url" -ForegroundColor Cyan
            # 策略 A: 优先使用系统自带 curl.exe (-4 强制 IPv4 避免 Windows IPv6 超时，带 Chrome User-Agent 避免 504)
            if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
                Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
                $curlArgs = @(
                    "-4",
                    "-fL",
                    "--retry", "2",
                    "--connect-timeout", "10",
                    "--max-time", "180",
                    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "-H", "Accept: application/octet-stream,application/gzip,*/*",
                    "-o", $TempTar,
                    $url
                )
                if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
                    $curlArgs = @("-x", $EffectiveProxy) + $curlArgs
                }
                & curl.exe @curlArgs
                if ($LASTEXITCODE -eq 0 -and (Test-Path $TempTar) -and (Get-Item $TempTar).Length -gt 10000000) {
                    $downloadSuccess = $true
                    break
                }
            }

            # 策略 B: 使用 WebClient 备用下载
            try {
                Remove-Item $TempTar -Force -ErrorAction SilentlyContinue
                $wc = New-Object System.Net.WebClient
                $wc.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                $wc.Headers.Add("Accept", "application/octet-stream,application/gzip,*/*")
                if (-not [string]::IsNullOrWhiteSpace($EffectiveProxy)) {
                    $wc.Proxy = New-Object System.Net.WebProxy($EffectiveProxy)
                }
                $wc.DownloadFile($url, $TempTar)
                if ((Test-Path $TempTar) -and (Get-Item $TempTar).Length -gt 10000000) {
                    $downloadSuccess = $true
                    break
                }
            } catch {}
        }
    }

    if (-not $downloadSuccess -or -not (Test-Path $TempTar) -or (Get-Item $TempTar).Length -le 10000000) {
        Write-Host ""
        Write-Host "==================================================================" -ForegroundColor Yellow
        Write-Host "错误: 终端自动化下载 SpiderMonkey 依赖包失败 (GitHub 节点返回 504 或超时)。" -ForegroundColor Red
        Write-Host ""
        Write-Host "【极简手动解决方式】:" -ForegroundColor Yellow
        Write-Host "1. 直接在浏览器中点击下方链接下载依赖压缩包:" -ForegroundColor White
        Write-Host "   https://github.com/Mag1cFall/firefox/releases/download/$SM_VERSION/$Tarball" -ForegroundColor Cyan
        Write-Host "2. 下载完成后，直接保留在浏览器的「下载」文件夹中，" -ForegroundColor White
        Write-Host "   或者复制到本目录下的 .deps\ 文件夹中。" -ForegroundColor White
        Write-Host "3. 重新运行 build.bat，脚本将自动检测该文件并完成一秒编译！" -ForegroundColor Green
        Write-Host "==================================================================" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }

    Write-Host ">>> 正在解压 SpiderMonkey 到: $DepsDir" -ForegroundColor Cyan
    if (Get-Command "tar.exe" -ErrorAction SilentlyContinue) {
        & tar.exe -xzf $TempTar -C $DepsDir
    } else {
        tar -xzf $TempTar -C $DepsDir
    }
    Remove-Item $TempTar -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $LibStatic)) {
        Write-Host "错误: 解压后未找到关键静态库: $LibStatic" -ForegroundColor Red
        exit 1
    }
    Write-Host ">>> SpiderMonkey 静态库解压就绪!" -ForegroundColor Green
}

# 3.5 Zig 静态库兼容后处理（幂等，每次构建都会校验）
# Zig 0.13.0 链接 SpiderMonkey 预编译静态库时存在两类冲突:
#   1) libjsrust.a 内的 compiler_builtins cgu.150 重复提供 ___chkstk_ms，与 Zig 的 compiler-rt 冲突;
#   2) Rust 侧引用的 fstat64i32 在 Zig 的 MinGW CRT 中缺失。
# 此处自动移除冲突成员并注入 fstat64i32 shim；重复运行不会产生重复成员。
if ($UseZig -and (Test-Path $LibStatic)) {
    $LibRust = "$DepsDir\lib\libjsrust.a"
    $LibRustOrig = "$LibRust.orig"
    if (-not (Test-Path $LibRust)) {
        Write-Host "错误: 未找到 SpiderMonkey Rust 静态库: $LibRust" -ForegroundColor Red
        exit 1
    }

    $ZigExe = ""
    $resolvedZig = Get-Command "zig" -ErrorAction SilentlyContinue
    if ($resolvedZig) {
        $ZigExe = $resolvedZig.Source
    }
    if ([string]::IsNullOrWhiteSpace($ZigExe) -or -not (Test-Path $ZigExe)) {
        Write-Host "错误: 未找到 Zig 可执行文件，无法执行静态库兼容后处理。" -ForegroundColor Red
        exit 1
    }

    if (-not (Test-Path $LibRustOrig)) {
        Copy-Item $LibRust $LibRustOrig -Force
        Write-Host ">>> 已备份原始 libjsrust.a -> $LibRustOrig" -ForegroundColor Gray
    }

    $rustMembers = @(& $ZigExe ar t $LibRust)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "错误: 读取 libjsrust.a 成员列表失败。" -ForegroundColor Red
        exit 1
    }
    $conflictMembers = @($rustMembers | Where-Object { $_ -match '^compiler_builtins-.*cgu\.150\.rcgu\.o$' })
    if ($conflictMembers.Count -gt 0) {
        foreach ($member in $conflictMembers) {
            Write-Host ">>> 移除与 Zig compiler-rt 冲突的成员: $member" -ForegroundColor Yellow
            & $ZigExe ar d $LibRust $member
            if ($LASTEXITCODE -ne 0) {
                Write-Host "错误: 移除冲突成员失败: $member" -ForegroundColor Red
                exit 1
            }
        }
    } else {
        Write-Host ">>> libjsrust.a 未发现重复 ___chkstk_ms 成员，跳过移除。" -ForegroundColor Gray
    }

    $ShimSrc = "$ScriptDir\tools\fstat64i32_shim.c"
    $ShimObj = "$ScriptDir\.deps\fstat_shim.o"
    if ($NeedWinShim) {
        if (-not (Test-Path $ShimSrc)) {
            Write-Host "错误: 缺少 fstat64i32 shim 源码: $ShimSrc" -ForegroundColor Red
            exit 1
        }
        Write-Host ">>> 编译并注入 fstat64i32 CRT shim..." -ForegroundColor Cyan
        & $ZigExe cc -target $ZigTarget -O2 -c $ShimSrc -o $ShimObj
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $ShimObj)) {
            Write-Host "错误: 编译 fstat64i32 shim 失败。" -ForegroundColor Red
            exit 1
        }
        $rustMembers = @(& $ZigExe ar t $LibRust)
        if ($rustMembers -contains "fstat_shim.o") {
            & $ZigExe ar d $LibRust "fstat_shim.o"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "错误: 移除旧 fstat_shim.o 成员失败。" -ForegroundColor Red
                exit 1
            }
        }
        & $ZigExe ar r $LibRust $ShimObj
        if ($LASTEXITCODE -ne 0) {
            Write-Host "错误: 注入 fstat_shim.o 失败。" -ForegroundColor Red
            exit 1
        }
        $rustMembers = @(& $ZigExe ar t $LibRust)
        if (-not ($rustMembers -contains "fstat_shim.o")) {
            Write-Host "错误: 注入后未在 libjsrust.a 中找到 fstat_shim.o。" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host ">>> libjsrust.a Zig 兼容后处理完成。" -ForegroundColor Green
}

# 4. 创建产物目录
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}

# 5. 执行 Go 编译
Write-Host ">>> 开始编译 waa-signer (Target=$Target CGO_ENABLED=1 GOOS=$TargetGOOS GOARCH=$TargetGOARCH)..." -ForegroundColor Cyan

$env:CGO_ENABLED = "1"
$env:GOOS = $TargetGOOS
$env:GOARCH = $TargetGOARCH
$env:CC = $CC
$env:CXX = $CXX
$IncludePath = "$DepsDir\include" -replace '\\', '/'
$LibPath = "$DepsDir\lib" -replace '\\', '/'
if ($Target -eq "android-arm64") {
    $env:CGO_CPPFLAGS = "-I$IncludePath"
    Remove-Item Env:CGO_CXXFLAGS -ErrorAction SilentlyContinue
} else {
    $env:CGO_CXXFLAGS = "-I$IncludePath"
    Remove-Item Env:CGO_CPPFLAGS -ErrorAction SilentlyContinue
}
$env:CGO_LDFLAGS = "-L$LibPath"

$ldflags = "-s -w"
if ($Target -eq "android-arm64") {
    $ldflags = "-s -w -linkmode external -extldflags '$ExtLdFlags'"
} elseif (-not [string]::IsNullOrWhiteSpace($ExtLdFlags)) {
    $ldflags += " -extldflags `"$ExtLdFlags`""
}

go build -trimpath -tags "netgo,osusergo" -ldflags $ldflags -o $OutExe ./cmd/waa-signer
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: 编译 waa-signer 失败！" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "==================================================================" -ForegroundColor Green
Write-Host ">>> 编译成功！产物位置: $OutExe" -ForegroundColor Green
if ($Target -eq "windows-amd64") {
    Write-Host ">>> 验证与运行方式:" -ForegroundColor Cyan
    Write-Host "    $OutExe -listen 127.0.0.1:8081" -ForegroundColor White
} else {
    $sizeBytes = (Get-Item $OutExe).Length
    Write-Host ">>> 产物大小: $sizeBytes 字节" -ForegroundColor Cyan
}
Write-Host "==================================================================" -ForegroundColor Green
