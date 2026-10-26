<#
.SYNOPSIS
Installs, updates or removes the SolidWorks MCP server. Start it through "Install SolidWorks MCP.cmd" (the top of
the release) or "Uninstall SolidWorks MCP.cmd" (in files\ and in the install folder; Windows' Installed apps entry
runs the install folder's copy).

.DESCRIPTION
Install (the default; run it again to update): get uv if missing; copy the server into <InstallDir>\app; install
Python 3.12 and the packages with uv (in <InstallDir>\python, apart from any Python you have); put the
solidworks-mcp command on your PATH; list it in Windows' Installed apps (per user); run doctor; register the server with Claude Desktop, Claude Code, Gemini
CLI and Antigravity (app and CLI); print
the config block for other AI apps; run the install check (doctor --check; SolidWorks is started for it after
one question when it is not running); print a summary and offer to restart Claude Desktop. Your settings and logs (in
%APPDATA%\solidworks-mcp) are never touched.

-Uninstall: remove the server's entries from Claude Desktop, Claude Code, Gemini CLI and Antigravity, its PATH
entry, its Installed apps entry and its program folder
(with its own Python). Settings and logs stay unless -Purge. uv stays (other programs may use it).

.PARAMETER Source
The server to install: a folder or a release zip. Default: the folder this script is in (files\ of an
unpacked release).

.PARAMETER InstallDir
Where the program goes. Default: %LOCALAPPDATA%\solidworks-mcp (with -Uninstall from an install folder: that
folder).

.PARAMETER NoGeminiCli
Do not register the server with Gemini CLI.

.PARAMETER NoAntigravityCli
Do not register the server with Antigravity (the app and the CLI share one file).

.PARAMETER NoPath
Do not put the solidworks-mcp command on your PATH.

.PARAMETER Check
When SolidWorks is not running, start it for the install check without asking.

.PARAMETER NoCheck
Do not run the install check (a short real build in SolidWorks).

.PARAMETER Force
Replace (or with -Uninstall remove) a 'solidworks' server entry in Claude Desktop, Claude Code, Gemini CLI or
Antigravity that points somewhere else.

.PARAMETER Unattended
Ask nothing (for the Windows installer, which runs this script without a window): SolidWorks is started for the
install check only with -Check, and Claude Desktop is restarted only with -RestartClaudeDesktop.

.PARAMETER RestartClaudeDesktop
With -Unattended: restart Claude Desktop at the end when it is running and its config changed.

.PARAMETER SummaryFile
Also write the summary to this file, one plain line each (UTF-8): "<app>: <outcome>", "Install check: <result>",
"Next: <step>", or "STOPPED: <why>" when the install stopped.

.PARAMETER CheckTimeoutS
How long the install check may take, in seconds (default 300): past it the check stops and the summary says that
SolidWorks may be showing a message to answer.

.PARAMETER Uninstall
Remove the server instead of installing it.

.PARAMETER Purge
With -Uninstall: also delete your settings and logs, including the copies the Microsoft Store Claude Desktop keeps.

.EXAMPLE
"Install SolidWorks MCP.cmd"                      (from the unpacked release)
"Install SolidWorks MCP.cmd" -Source C:\Downloads\solidworks-mcp-0.2.0.zip
"files\Uninstall SolidWorks MCP.cmd" -Purge
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$InstallDir,
    [switch]$Force,
    [switch]$NoClaudeDesktop,
    [switch]$NoClaudeCode,
    [switch]$NoGeminiCli,
    [switch]$NoAntigravityCli,
    [switch]$NoPath,
    [switch]$Check,
    [switch]$NoCheck,
    [switch]$Unattended,
    [switch]$RestartClaudeDesktop,
    [string]$SummaryFile,
    [int]$CheckTimeoutS = 300,
    [switch]$Uninstall,
    [switch]$Purge
)
$ErrorActionPreference = 'Stop'

# What the program folder holds: the server and what uv needs to install it.
$ShippedFiles = @('pyproject.toml', 'uv.lock', '.python-version', 'README.md', 'LICENSE', 'solidworks_mcp')
$ServerName = 'solidworks'

function Write-Step([string]$text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok([string]$text) { Write-Host "    $text" -ForegroundColor Green }
function Write-Note([string]$text) { Write-Host "    $text" -ForegroundColor Yellow }
function Stop-Install([string]$text) {
    Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red
    Write-Summary @("STOPPED: $text")
    exit 1
}

# -SummaryFile: the summary in plain lines, for the Windows installer's last page
function Write-Summary([string[]]$lines) {
    if (-not $SummaryFile) { return }
    try { Set-Content -Path $SummaryFile -Value $lines -Encoding UTF8 }
    catch { Write-Note "could not write the summary file ($($_.Exception.Message))" }
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @((Join-Path $env:USERPROFILE '.local\bin\uv.exe'), (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-UnderFolder([string]$path, [string]$folder) {
    if (-not $path) { return $false }
    $root = [IO.Path]::GetFullPath($folder).TrimEnd('\') + '\'
    return [IO.Path]::GetFullPath($path).StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
}

# A running server holds its program files open: updating or removing them would fail half-way.
function Assert-NotRunning([string]$installDir) {
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { Test-UnderFolder $_.Path $installDir } catch { $false } }
    if ($running) {
        $names = ($running | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -join ', '
        Stop-Install ("The installed server is running ($names). Quit Claude Desktop (tray icon > Quit) and close " +
                      "Claude Code sessions, then run this again.")
    }
}

# The user PATH as stored: unexpanded (%USERPROFILE%\... stays so) and with its registry type (Windows keeps it as
# REG_EXPAND_SZ). [Environment]::SetEnvironmentVariable would write it back expanded, as REG_SZ.
function Get-UserPath([string]$var = 'Path') {
    $key = Get-Item 'HKCU:\Environment'
    return [string]$key.GetValue($var, '', 'DoNotExpandEnvironmentNames')
}

function Set-UserPath([string]$value, [string]$var = 'Path') {
    $key = Get-Item 'HKCU:\Environment'
    $kind = if ($key.GetValueNames() -contains $var) { $key.GetValueKind($var) } else { 'ExpandString' }
    if ($value -match '%') { $kind = 'ExpandString' }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name $var -Value $value -Type $kind
    # tell running programs (Explorer, new terminals) that the environment changed
    [Environment]::SetEnvironmentVariable('SWMCP_ENV_REFRESH', $null, 'User')
}

# Entries are compared expanded; everything else (order, separators, other entries) is kept as it is, so adding and
# then removing an entry gives back exactly the value there was.
function Test-SameDir([string]$entry, [string]$dir) {
    return [Environment]::ExpandEnvironmentVariables($entry).TrimEnd('\') -ieq $dir.TrimEnd('\')
}

function Add-UserPath([string]$dir, [string]$var = 'Path') {
    $raw = Get-UserPath $var
    if (@($raw -split ';' | Where-Object { $_ -and (Test-SameDir $_ $dir) }).Count) { return $false }
    $new = if (-not $raw) { $dir } elseif ($raw.EndsWith(';')) { "$raw$dir;" } else { "$raw;$dir" }
    Set-UserPath $new $var
    return $true
}

function Remove-UserPath([string]$dir, [string]$var = 'Path') {
    $raw = Get-UserPath $var
    $parts = @($raw -split ';')
    $kept = @($parts | Where-Object { -not ($_ -and (Test-SameDir $_ $dir)) })
    if ($kept.Count -eq $parts.Count) { return $false }
    Set-UserPath ($kept -join ';') $var
    return $true
}

# Claude Code's user-scope entry: (command, or $null when there is none). Asked from a neutral folder, so a
# project's own .mcp.json does not answer instead.
# Windows PowerShell 5.1 turns a native program's stderr into an error record, fatal under 'Stop': claude is run
# through cmd with its streams merged there.
function Invoke-Claude([string]$arguments) {
    $ErrorActionPreference = 'Continue'
    $out = (cmd /c "claude $arguments 2>&1" | Out-String)
    return @{ Text = $out; Code = $LASTEXITCODE }
}

function Get-ClaudeCodeEntry {
    Push-Location ([IO.Path]::GetTempPath())
    try { $out = (Invoke-Claude "mcp get $ServerName").Text } finally { Pop-Location }
    if ($out -notmatch 'Scope:\s*User') { return $null }
    if ($out -match '(?m)^\s*Command:\s*(.+?)\s*$') { return $Matches[1] }
    return ''
}

# Returns the outcome for the summary.
function Register-ClaudeCode([string]$Exe, [string]$InstallDir, [switch]$Force) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Write-Note "Claude Code not installed: skipped"; return 'not installed' }
    $existing = Get-ClaudeCodeEntry
    if ($null -ne $existing) {
        if (-not (Test-UnderFolder $existing $InstallDir) -and -not $Force) {
            Write-Note "kept: Claude Code's '$ServerName' server runs '$existing', not this install (-Force replaces it)"
            return "kept another '$ServerName' server (-Force replaces it)"
        }
        if ($existing -ieq $Exe) { Write-Ok "unchanged"; return 'registered' }
        Invoke-Claude "mcp remove $ServerName -s user" | Out-Null
    }
    $added = Invoke-Claude "mcp add --scope user $ServerName -- `"$Exe`""
    if ($added.Code -eq 0) { Write-Ok "added for every folder (user scope)"; return 'registered' }
    Write-Note "claude mcp add failed (exit $($added.Code)): $($added.Text.Trim()); add it by hand: claude mcp add --scope user $ServerName -- `"$Exe`""
    return 'add it by hand (see above)'
}

function Unregister-ClaudeCode([string]$InstallDir, [switch]$Force) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Write-Note "Claude Code not found: skipped"; return }
    $existing = Get-ClaudeCodeEntry
    if ($null -eq $existing) { Write-Ok "nothing to remove"; return }
    if (-not (Test-UnderFolder $existing $InstallDir) -and -not $Force) {
        Write-Note "kept: Claude Code's '$ServerName' server is not this install (-Force removes it anyway)"
        return
    }
    $removed = Invoke-Claude "mcp remove $ServerName -s user"
    if ($removed.Code -eq 0) { Write-Ok "removed" } else { Write-Note "claude mcp remove failed: $($removed.Text.Trim())" }
}

# Delete a folder tree, also where uv's Python puts files past Windows' 260-character path limit (a deep
# -InstallDir or a long user name): Remove-Item in Windows PowerShell 5.1 fails there, the \\?\ form does not.
function Remove-Tree([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop } catch {
        try { [IO.Directory]::Delete('\\?\' + [IO.Path]::GetFullPath($path), $true) } catch { }
    }
    return -not (Test-Path -LiteralPath $path)
}

# SolidWorks' program, as COM starts it (the class the server connects to), or $null when it is not installed.
function Find-SolidWorks {
    try {
        $clsid = (Get-ItemProperty 'Registry::HKEY_CLASSES_ROOT\SldWorks.Application\CLSID' -ErrorAction Stop).'(default)'
        $server = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$clsid\LocalServer32" -ErrorAction Stop).'(default)'
        if ($server -match '^\s*"?([^"]+?\.exe)') { if (Test-Path $Matches[1]) { return $Matches[1] } }
    } catch { }
    return $null
}

# Wait until a starting SolidWorks takes COM calls (the install check attaches, it never starts it). A licence or
# sign-in dialog holds it back: then the user is told, and the wait ends at the time limit.
function Wait-SolidWorks([int]$seconds = 300) {
    $until = (Get-Date).AddSeconds($seconds)
    $noteAt = (Get-Date).AddSeconds(60)
    $told = $false
    while ((Get-Date) -lt $until) {
        try {
            $sw = [Runtime.InteropServices.Marshal]::GetActiveObject('SldWorks.Application')
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sw)
            Start-Sleep -Seconds 5                        # its add-ins are still loading
            return $true
        } catch { }
        if (-not (Get-Process SLDWORKS -ErrorAction SilentlyContinue)) { return $false }
        if (-not $told -and (Get-Date) -gt $noteAt) {
            Write-Note "still starting: if SolidWorks shows a dialog (licence, sign-in, welcome), answer it"
            $told = $true
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

# An app's version from Windows' Installed apps (or the Store), '' when unknown.
function Get-AppVersion([string]$like, [string]$appx = '') {
    if ($appx) { $p = Get-AppxPackage $appx -ErrorAction SilentlyContinue | Select-Object -First 1; if ($p) { return [string]$p.Version } }
    foreach ($root in @('HKCU:\Software', 'HKLM:\Software', 'HKLM:\Software\WOW6432Node')) {
        foreach ($k in (Get-ChildItem "$root\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue)) {
            $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
            if ($p.DisplayName -like $like -and $p.DisplayVersion) { return [string]$p.DisplayVersion }
        }
    }
    return ''
}

# What one registration step said, in one word for the summary.
function Get-Outcome([string[]]$lines) {
    $text = $lines -join "`n"
    if ($text -match 'WARNING') { return 'registered, but may be too old: update it' }
    if ($text -match ':\s*(added|updated|unchanged)\b') { return 'registered' }
    if ($text -match 'not installed') { return 'not installed' }
    if ($text -match ':\s*kept') { return "kept another '$ServerName' server (-Force replaces it)" }
    if ($text -match ':\s*skipped') { return 'add it by hand (see above)' }
    return 'see above'
}

# Claude Desktop's own processes (not Claude Code's claude.exe).
function Get-ClaudeDesktop {
    return @(Get-Process claude -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like '*\WindowsApps\Claude_*\Claude.exe' -or $_.Path -like '*\AnthropicClaude\*' })
}

function Restart-ClaudeDesktop {
    $store = Get-AppxPackage Claude -ErrorAction SilentlyContinue | Select-Object -First 1
    Get-ClaudeDesktop | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if ($store) { Start-Process "shell:AppsFolder\$($store.PackageFamilyName)!Claude" }
    else { Start-Process (Join-Path $env:LOCALAPPDATA 'AnthropicClaude\claude.exe') }
}

# Windows' Installed apps entry (per user, no admin): Settings > Apps > Installed apps > SolidWorks MCP > Uninstall
# runs the uninstaller in the install folder. The usual folder's entry is 'SolidWorksMCP'; an install elsewhere
# gets its own entry, named by its folder, so it never replaces the usual one.
$UninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'

function Get-AppsEntry([string]$installDir) {
    $dir = [IO.Path]::GetFullPath($installDir).TrimEnd('\')
    $usual = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'solidworks-mcp')).TrimEnd('\')
    if ($dir -ieq $usual) { return @{ Key = Join-Path $UninstallRoot 'SolidWorksMCP'; Name = 'SolidWorks MCP' } }
    $sha = [Security.Cryptography.SHA256]::Create()
    $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($dir.ToLowerInvariant()))[0..3] | ForEach-Object { $_.ToString('x2') })
    return @{ Key = Join-Path $UninstallRoot "SolidWorksMCP-$hash"; Name = "SolidWorks MCP ($dir)" }
}

function Register-AppsEntry([string]$installDir) {
    $installDir = [IO.Path]::GetFullPath($installDir).TrimEnd('\')
    $entry = Get-AppsEntry $installDir
    $source = Get-Content (Join-Path $installDir 'app\solidworks_mcp\version.py') -Raw -ErrorAction SilentlyContinue
    $version = if ($source -match '__version__\s*=\s*"([^"]+)"') { $Matches[1] } else { '' }
    $kb = [int]((Get-ChildItem $installDir -Recurse -File -Force -ErrorAction SilentlyContinue |
                 Measure-Object Length -Sum).Sum / 1KB)
    $cmd = Join-Path $installDir 'Uninstall SolidWorks MCP.cmd'
    New-Item -Path $entry.Key -Force | Out-Null
    $values = @{ DisplayName = $entry.Name; DisplayVersion = $version; Publisher = 'SolidWorks MCP'
                 InstallLocation = $installDir; UninstallString = "`"$env:ComSpec`" /c `"`"$cmd`"`""
                 InstallDate = (Get-Date -Format 'yyyyMMdd') }
    foreach ($k in $values.Keys) { New-ItemProperty -Path $entry.Key -Name $k -Value $values[$k] -PropertyType String -Force | Out-Null }
    foreach ($k in @('NoModify', 'NoRepair')) { New-ItemProperty -Path $entry.Key -Name $k -Value 1 -PropertyType DWord -Force | Out-Null }
    New-ItemProperty -Path $entry.Key -Name EstimatedSize -Value $kb -PropertyType DWord -Force | Out-Null
    return $entry.Name
}

# Only the entry of this install folder: another install's entry is never touched.
function Unregister-AppsEntry([string]$installDir) {
    $entry = Get-AppsEntry $installDir
    if (-not (Test-Path $entry.Key)) { return $false }
    $location = (Get-ItemProperty -Path $entry.Key -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
    if ($location -and $location.TrimEnd('\') -ine ([IO.Path]::GetFullPath($installDir).TrimEnd('\'))) { return $false }
    Remove-Item -Path $entry.Key -Recurse -Force
    return $true
}

# Never delete or replace a folder that is not this program's install: a mistyped -InstallDir must do no harm.
function Test-OurInstall([string]$installDir) {
    $marker = Join-Path $installDir 'app\pyproject.toml'
    return (Test-Path $marker) -and ((Get-Content $marker -Raw) -match '(?m)^name\s*=\s*"solidworks-mcp"')
}

function Assert-OurInstallOrNew([string]$installDir) {
    $app = Join-Path $installDir 'app'
    $hasContent = (Test-Path $app) -and @(Get-ChildItem $app -Force | Where-Object Name -ne '.venv').Count -gt 0
    if ($hasContent -and -not (Test-OurInstall $installDir)) {
        Stop-Install "$app holds something else than this server; choose another -InstallDir."
    }
}

function Invoke-Install {
    Write-Step "SolidWorks MCP installer"
    $app = Join-Path $InstallDir 'app'

    # 1. The source: a folder or a zip holding pyproject.toml and solidworks_mcp\
    if (-not $Source) { $Source = $PSScriptRoot }
    $Source = [IO.Path]::GetFullPath($Source)
    $unpacked = $null
    if ((Test-Path $Source -PathType Leaf) -and $Source -match '\.zip$') {
        $unpacked = Join-Path ([IO.Path]::GetTempPath()) ("swmcp-src-" + [guid]::NewGuid().ToString('N'))
        Expand-Archive -Path $Source -DestinationPath $unpacked
        $Source = $unpacked
    }
    $top = Get-ChildItem $Source -Recurse -Filter pyproject.toml -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $top -or -not (Test-Path (Join-Path $top.DirectoryName 'solidworks_mcp'))) {
        Stop-Install "$Source does not hold the SolidWorks MCP server (no pyproject.toml next to solidworks_mcp)."
    }
    $Source = $top.DirectoryName

    Assert-NotRunning $InstallDir
    Assert-OurInstallOrNew $InstallDir

    # 2. uv
    $uv = Find-Uv
    if (-not $uv) {
        Write-Step "Installing uv (the Python installer, from astral.sh)"
        powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $uv = Find-Uv
        if (-not $uv) { Stop-Install "uv could not be installed. Install it by hand (https://docs.astral.sh/uv/) and run this again." }
    }
    Write-Ok "uv: $uv"

    # 3. The program: replaced on every run (the environment in app\.venv is reused)
    Write-Step "Copying the server to $app"
    New-Item -ItemType Directory -Force $app | Out-Null
    Get-ChildItem $app -Force | Where-Object Name -ne '.venv' | Remove-Item -Recurse -Force
    foreach ($item in $ShippedFiles) {
        $from = Join-Path $Source $item
        # a release keeps README.md and LICENSE at its top, one level above files\
        if (-not (Test-Path $from)) { $from = Join-Path (Split-Path $Source -Parent) $item }
        if (Test-Path $from) { Copy-Item $from -Destination $app -Recurse -Force }
    }
    Get-ChildItem $app -Recurse -Force -Include '__pycache__', '*.log', '*.log.*', '.env' | Remove-Item -Recurse -Force
    # the uninstaller lives next to the program, so it works without the download
    foreach ($f in @('setup.ps1', 'Uninstall SolidWorks MCP.cmd')) {
        $from = Join-Path $PSScriptRoot $f
        if (-not (Test-Path $from)) { $from = Join-Path (Split-Path $PSScriptRoot -Parent) $f }
        if ((Test-Path $from) -and -not (Test-UnderFolder $from $InstallDir)) { Copy-Item $from $InstallDir -Force }
    }
    if ($unpacked) { Remove-Item -Recurse -Force $unpacked }

    # 4. Python and the packages, from uv.lock
    Write-Step "Installing Python 3.12 and the packages (first time: a minute or two)"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallDir 'python'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    & $uv sync --frozen --no-dev --directory $app
    if ($LASTEXITCODE -ne 0) { Stop-Install "uv could not install the packages (exit $LASTEXITCODE). See the messages above." }
    $exe = Join-Path $app '.venv\Scripts\solidworks-mcp.exe'
    $python = Join-Path $app '.venv\Scripts\python.exe'
    if (-not (Test-Path $exe)) { Stop-Install "The install did not produce $exe." }

    # 5. The solidworks-mcp command (for doctor) on the user's PATH
    $bin = Join-Path $InstallDir 'bin'
    New-Item -ItemType Directory -Force $bin | Out-Null
    Set-Content -Path (Join-Path $bin 'solidworks-mcp.cmd') -Encoding ASCII -Value "@`"%~dp0..\app\.venv\Scripts\solidworks-mcp.exe`" %*"
    if ($NoPath) { Write-Note "not added to PATH (-NoPath): run doctor as $bin\solidworks-mcp.cmd doctor" }
    elseif (Add-UserPath $bin) { Write-Ok "added $bin to your PATH (new terminals see it)" }

    # 5b. Windows' Installed apps entry, so it can be removed the usual Windows way
    Write-Step "Windows Installed apps"
    try { Write-Ok "listed as '$(Register-AppsEntry $InstallDir)' (Settings > Apps > Installed apps)" }
    catch { Write-Note "could not add the Installed apps entry ($($_.Exception.Message)); to remove it later: $InstallDir\Uninstall SolidWorks MCP.cmd" }

    # 6. doctor
    Write-Step "Checking the setup (doctor)"
    & $exe doctor
    Write-Host ""

    # 7-8. The AI apps. Each step's outcome goes into the summary at the end.
    $summary = [ordered]@{}
    $forceArg = @(); if ($Force) { $forceArg = @('--force') }
    $desktopChanged = $false
    if (-not $NoClaudeDesktop) {
        Write-Step "Claude Desktop"
        $lines = @(& $python -m solidworks_mcp.apps register --install-dir $InstallDir @forceArg)
        $lines | ForEach-Object { Write-Host "    $_" }
        $desktopChanged = [bool]($lines -match ':\s*(added|updated)\b')
        $installed = (Get-AppxPackage Claude -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $env:LOCALAPPDATA 'AnthropicClaude'))
        $summary['Claude Desktop'] = if ($installed) { Get-Outcome $lines } else { 'not installed (its config is ready for when it is)' }
    }
    # Claude Code: user scope, every folder
    if (-not $NoClaudeCode) {
        Write-Step "Claude Code"
        $summary['Claude Code'] = Register-ClaudeCode -Exe $exe -InstallDir $InstallDir -Force:$Force
    }
    # Gemini CLI: its user settings.json, made if it has never run
    if (-not $NoGeminiCli) {
        Write-Step "Gemini CLI"
        $lines = @(& $python -m solidworks_mcp.apps register --app gemini --install-dir $InstallDir @forceArg)
        $lines | ForEach-Object { Write-Host "    $_" }
        $summary['Gemini CLI'] = Get-Outcome $lines
    }
    # Antigravity, app and CLI: their shared mcp_config.json, made if neither has been opened yet
    if (-not $NoAntigravityCli) {
        Write-Step "Antigravity (app and CLI)"
        $lines = @(& $python -m solidworks_mcp.apps register --app antigravity --install-dir $InstallDir @forceArg)
        $lines | ForEach-Object { Write-Host "    $_" }
        $summary['Antigravity'] = Get-Outcome $lines
    }

    # 9. Other apps
    Write-Step "Other AI apps that run local MCP servers: add this to their MCP config"
    & $python -m solidworks_mcp.apps snippet --install-dir $InstallDir

    # 10. The install check: a short real build in SolidWorks, run without asking. SolidWorks is started for it
    # after one question when it is not running (it stays open afterwards).
    Write-Step "Install check"
    $checkResult = 'not run'
    $swUp = [bool](Get-Process SLDWORKS -ErrorAction SilentlyContinue)
    if ($NoCheck) { $checkResult = 'not run (-NoCheck)' }
    elseif (-not $swUp) {
        $sldworks = Find-SolidWorks
        if (-not $sldworks) { $checkResult = 'not run (SolidWorks is not installed on this computer)' }
        else {
            $start = $Check
            if (-not $Check -and -not $Unattended) {
                $start = (Read-Host "    Start SolidWorks now for the install check? (Y/n)") -notmatch '^[nN]'
            }
            if ($start) {
                Write-Ok "starting SolidWorks (a minute or two)"
                Start-Process $sldworks
                $swUp = Wait-SolidWorks 300
                if (-not $swUp) { $checkResult = 'not run (SolidWorks did not finish starting in 5 minutes)' }
            } else { $checkResult = 'not run (you chose not to start SolidWorks)' }
        }
    }
    if ($swUp -and -not $NoCheck) {
        # Its own process with a time limit: a SolidWorks dialog would otherwise hold a hidden install for ever.
        # The verdict is doctor's "INSTALL CHECK" line; its exit code also counts warnings (an untested version).
        $out = Join-Path ([IO.Path]::GetTempPath()) ("swmcp-check-" + [guid]::NewGuid().ToString('N') + ".txt")
        $proc = Start-Process -FilePath $exe -ArgumentList 'doctor', '--check' -NoNewWindow -PassThru `
                              -RedirectStandardOutput $out -RedirectStandardError "$out.err"
        if (-not $proc.WaitForExit($CheckTimeoutS * 1000)) {
            try { $proc.Kill() } catch { }
            $checkResult = "not finished in $([int]($CheckTimeoutS / 60)) minutes: SolidWorks may be showing a " +
                           "message. Answer it in SolidWorks, then run 'solidworks-mcp doctor --check'"
        }
        $text = if (Test-Path $out) { Get-Content $out -Raw } else { '' }
        if ($text) { Write-Host $text }
        Remove-Item $out, "$out.err" -ErrorAction SilentlyContinue
        if ($text -match 'INSTALL CHECK: PASS') {
            $checkResult = if ($text -match 'doctor found (\d+) problem') { "PASS ($($Matches[1]) note(s) above)" } else { 'PASS' }
        } elseif ($checkResult -eq 'not run') {
            $checkResult = 'FAIL (see above; run solidworks-mcp doctor and send the report)'
        }
    }

    # The summary
    Write-Step "Summary"
    $versions = @{ 'Claude Desktop' = (Get-AppVersion 'Claude' 'Claude'); 'Claude Code' = (Get-AppVersion 'Claude Code*');
                   'Antigravity' = (Get-AppVersion '*Antigravity*'); 'Gemini CLI' = '' }
    foreach ($app in $summary.Keys) {
        $v = $versions[$app]; $label = if ($v -and $summary[$app] -like 'registered*') { "$app $v" } else { $app }
        if ($summary[$app] -like 'registered*') { Write-Ok "$($label): $($summary[$app])" } else { Write-Note "$($label): $($summary[$app])" }
    }
    if ($checkResult -like 'PASS*') { Write-Ok "Install check: $checkResult" } else { Write-Note "Install check: $checkResult" }

    $next = @()
    if ($summary['Claude Desktop'] -like 'registered*') {
        $restarted = $false
        if ($desktopChanged -and (Get-ClaudeDesktop)) {
            if ($Unattended) { $answer = if ($RestartClaudeDesktop) { 'y' } else { 'n' } }
            else {
                $answer = Read-Host ("    Restart Claude Desktop now so it loads SolidWorks MCP? This closes all its windows, " +
                                     "including Claude Code sessions running in it (y/N)")
            }
            if ($answer -match '^[yY]') { Restart-ClaudeDesktop; $restarted = $true; Write-Ok "Claude Desktop restarted" }
        }
        if (-not $restarted -and $desktopChanged) { $next += "Claude Desktop: quit it fully (tray icon > Quit) and open it again" }
    }
    if ($summary['Claude Code'] -like 'registered*') { $next += "Claude Code: start a new session" }
    if ($summary['Gemini CLI'] -like 'registered*') { $next += "Gemini CLI: start a new session" }
    if ($summary['Antigravity'] -like 'registered*') { $next += "Antigravity: Settings > Customizations > Installed MCP Servers > Refresh (or open it, if it is new)" }
    Write-Host ""
    foreach ($n in $next) { Write-Host "Next: $n" }
    Write-Host "Then ask your AI app: 'Connect to SolidWorks and create a new part.'"
    Write-Host "Problems? Run: solidworks-mcp doctor   (in a new terminal) and send the report."
    Write-Host "To remove it: Settings > Apps > Installed apps > SolidWorks MCP > Uninstall (or $InstallDir\Uninstall SolidWorks MCP.cmd)"
    Write-Summary (@($summary.Keys | ForEach-Object { "$($_): $($summary[$_])" }) + @("Install check: $checkResult") +
                   @($next | ForEach-Object { "Next: $_" }))
}

function Invoke-Uninstall {
    Write-Step "SolidWorks MCP uninstaller"
    $isOurs = Test-OurInstall $InstallDir
    if ((Test-Path $InstallDir) -and -not $isOurs) {
        Stop-Install "$InstallDir is not a SolidWorks MCP install (no app\pyproject.toml of solidworks-mcp); nothing removed."
    }
    Assert-NotRunning $InstallDir

    Write-Step "Claude Desktop"
    $python = Join-Path $InstallDir 'app\.venv\Scripts\python.exe'
    if (Test-Path $python) {
        $forceArg = @(); if ($Force) { $forceArg = @('--force') }
        & $python -m solidworks_mcp.apps unregister --install-dir $InstallDir @forceArg
    } else {
        Write-Note "the program's Python is missing: remove the '$ServerName' entry from claude_desktop_config.json by hand if there is one"
    }

    Write-Step "Claude Code"
    Unregister-ClaudeCode -InstallDir $InstallDir -Force:$Force

    Write-Step "Gemini CLI and Antigravity"
    if (Test-Path $python) {
        $forceArg = @(); if ($Force) { $forceArg = @('--force') }
        & $python -m solidworks_mcp.apps unregister --app gemini --install-dir $InstallDir @forceArg
        & $python -m solidworks_mcp.apps unregister --app antigravity --install-dir $InstallDir @forceArg
    } else {
        Write-Note "the program's Python is missing: remove the '$ServerName' entry from .gemini\settings.json and .gemini\config\mcp_config.json by hand if there is one"
    }

    Write-Step "PATH"
    if (Remove-UserPath (Join-Path $InstallDir 'bin')) { Write-Ok "removed $InstallDir\bin" } else { Write-Ok "nothing to remove" }

    Write-Step "Windows Installed apps"
    if (Unregister-AppsEntry $InstallDir) { Write-Ok "entry removed" } else { Write-Ok "nothing to remove" }

    Write-Step "Program folder"
    if ($isOurs) {
        if (Remove-Tree $InstallDir) { Write-Ok "removed $InstallDir" }
        else { Write-Note "could not remove all of $InstallDir (a file in use?): delete the folder by hand" }
    } else {
        Write-Ok "nothing at $InstallDir"
    }

    $homes = @(Join-Path $env:APPDATA 'solidworks-mcp') + @($env:SWMCP_HOME | Where-Object { $_ }) +
             @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages') -Directory -ErrorAction SilentlyContinue |
               ForEach-Object { Join-Path $_.FullName 'LocalCache\Roaming\solidworks-mcp' })
    $homes = @($homes | Where-Object { Test-Path $_ })
    Write-Step "Settings and logs"
    if ($Purge) {
        foreach ($h in $homes) {
            if (Remove-Tree $h) { Write-Ok "removed $h" } else { Write-Note "could not remove all of $h" }
        }
        if (-not $homes) { Write-Ok "none" }
    } else {
        foreach ($h in $homes) { Write-Ok "kept $h (-Purge removes it)" }
        if (-not $homes) { Write-Ok "none" }
    }
    Write-Step "Done. Restart Claude Desktop (tray icon > Quit) so it forgets the server."
}

if ($env:OS -ne 'Windows_NT') { Stop-Install "This server drives SolidWorks and runs only on Windows." }
if (-not $InstallDir) {
    # run from an install folder (its uninstaller): that folder; else the usual place
    if ($Uninstall -and (Test-OurInstall $PSScriptRoot)) { $InstallDir = $PSScriptRoot }
    else { $InstallDir = Join-Path $env:LOCALAPPDATA 'solidworks-mcp' }
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if ($Uninstall) { Invoke-Uninstall } else { Invoke-Install }
