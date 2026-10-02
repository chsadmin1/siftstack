# run_daily_audit.ps1 - wrapper invoked by Windows Task Scheduler at 7:00 AM.
#
# Verified 2026-08 against the installed `claude` CLI (v2.1.221) via
# `claude --help`: -p/--print, --allowedTools, --no-session-persistence, and
# --setting-sources are all real, current flags on this machine.
#
# Scoped to exactly the tools this task needs (Bash to run the python
# scripts, Read for transcripts, Write for the rows JSON, Skill for the
# coaching skills) rather than a blanket permission bypass
# (--dangerously-skip-permissions / --permission-mode bypassPermissions) -
# smaller blast radius for something running unattended with no one to
# notice if it goes off the rails.
#
# --setting-sources project,local (excludes "user"): the account's global
# ~/.claude/settings.json carries GSD-suite PreToolUse hooks on Write/Edit
# (gsd-workflow-guard.js etc.) that gate on an active GSD workflow context.
# This automation has nothing to do with GSD and isn't running inside one, so
# those hooks just stall an unattended run waiting for an approval nobody's
# there to give (confirmed live 2026-08-06 - the run blocked on writing
# daily_audit_rows.json and sat until it gave up). Excluding "user" skips
# that settings file entirely for this invocation. Deliberately NOT --bare:
# --bare would also force ANTHROPIC_API_KEY billing instead of the normal
# subscription auth and change how skills/CLAUDE.md resolve - more than this
# problem needs.

$ErrorActionPreference = "Stop"
Set-Location "c:\Users\djpkc\SiftStack"

$logDir = "output\call_coaching\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logFile = Join-Path $logDir "daily_audit_$stamp.log"

$promptPath = "src\call_coaching\DAILY_AUDIT_PROMPT.md"
$prompt = Get-Content $promptPath -Raw

& claude -p $prompt --allowedTools "Bash Read Write Skill" --no-session-persistence `
    --setting-sources "project,local" *>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8
$exitCode = $LASTEXITCODE

Add-Content $logFile "`nExit code: $exitCode" -Encoding utf8
if ($exitCode -ne 0) {
    Write-Output "Daily audit run FAILED (exit $exitCode) - see $logFile"
} else {
    Write-Output "Daily audit run completed - see $logFile"
}
exit $exitCode
