# Installs AgentLoop on Windows (item installer), or upgrades it when run again:
#
#     irm https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.ps1 | iex
#
# It installs uv (https://docs.astral.sh/uv/) if it is missing, which brings its own Python, and
# then the release's wheel as a uv tool, and starts `agentloop serve --open`. It checks for git and
# claude and says how to get them, but installs nothing else, and never asks for or reads a key.
#
# The release workflow writes the release's version over the placeholder below. Beside it:
#   AGENTLOOP_WHEEL     a wheel's path or address to install instead of the release's
#   AGENTLOOP_NO_SERVE  set: install, but do not start the page
#   UV_NO_MODIFY_PATH   set: change no PATH, neither uv's installer nor this script
#
# It runs on Windows PowerShell 5.1 and on PowerShell 7. Under `iex` an `exit` would close the
# person's own window, so a failure throws instead.

function Install-AgentLoop {
    $version = '@VERSION@'

    function Say([string] $text) { Write-Host "agentloop: $text" }
    function Has([string] $name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

    # uv's installer puts it in one of these, and this window has not read the new PATH.
    function Find-Uv {
        $found = Get-Command uv -ErrorAction SilentlyContinue
        if ($found) { return $found.Source }
        $candidates = @()
        if ($env:UV_INSTALL_DIR) {
            $candidates += Join-Path $env:UV_INSTALL_DIR 'bin\uv.exe'
            $candidates += Join-Path $env:UV_INSTALL_DIR 'uv.exe'
        }
        if ($env:XDG_BIN_HOME) { $candidates += Join-Path $env:XDG_BIN_HOME 'uv.exe' }
        $candidates += Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        $candidates += Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'
        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        return $null
    }

    $wheel = $env:AGENTLOOP_WHEEL
    if (-not $wheel) {
        if ($version.StartsWith('@')) {
            throw 'agentloop: this script names no release: fetch it from a release, or set AGENTLOOP_WHEEL to a wheel'
        }
        $wheel = "https://github.com/AxelStrem/AgentLoop/releases/download/v$version/agentloop-$version-py3-none-any.whl"
    }

    # What a loop needs beside AgentLoop, which this script does not install.
    if (-not (Has 'git')) {
        Say 'git is not on PATH: install it from https://git-scm.com/downloads, or with: winget install Git.Git'
    }
    if (-not (Has 'claude')) {
        Say 'claude (Claude Code) is not on PATH: see https://docs.anthropic.com/en/docs/claude-code/setup,'
        Say 'then log in to each account a loop will use.'
    }

    $uv = Find-Uv
    if (-not $uv) {
        Say 'installing uv, which brings its own Python'
        # In a PowerShell of its own, so that nothing it sets is left in this one.
        $shell = (Get-Process -Id $PID).Path
        & $shell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex"
        if ($LASTEXITCODE -ne 0) { throw "agentloop: uv's installer failed (exit $LASTEXITCODE)" }
        $uv = Find-Uv
        if (-not $uv) { throw 'agentloop: uv was installed, but it is not where its installer puts it: open a new window and run this again' }
    }

    $tools = (& $uv tool list 2>$null) -join "`n"
    if ($tools -match '(?m)^agentloop ') {
        Say 'AgentLoop is installed already: this upgrades it.'
        Say 'A loop that is running reads the installed files on every step. Stop the loops first, or run'
        Say 'them from a build that upgrades do not touch (agentloop build --to <path>; see docs/install.md).'
    }

    # --force replaces an earlier install, which is the upgrade: uv tool upgrade keeps a tool
    # installed from an address at the address it came from.
    Say "installing $wheel"
    & $uv tool install --force --python '>=3.11' $wheel
    if ($LASTEXITCODE -ne 0) { throw "agentloop: uv tool install failed (exit $LASTEXITCODE)" }

    $bin = (& $uv tool dir --bin | Select-Object -First 1).Trim()
    $agentloop = Join-Path $bin 'agentloop.exe'
    if (-not (Test-Path -LiteralPath $agentloop -PathType Leaf)) {
        throw "agentloop: uv installed AgentLoop, but $agentloop is not there"
    }

    if (-not $env:UV_NO_MODIFY_PATH) {
        & $uv tool update-shell *> $null
        Say "agentloop is in ${bin}: a new window finds it on PATH."
    } else {
        Say "agentloop is in ${bin}: add it to PATH to run it by name."
    }

    & $agentloop --version
    if ($LASTEXITCODE -ne 0) { throw "agentloop: agentloop --version failed (exit $LASTEXITCODE)" }

    if ($env:AGENTLOOP_NO_SERVE) { return }
    Say "starting the page: Ctrl+C stops it, and 'agentloop serve --open' starts it again."
    & $agentloop serve --open
}

Install-AgentLoop
