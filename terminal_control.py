"""Close only the selected MT5 executable, using a normal window-close request."""
import json
import os
from pathlib import Path
import subprocess


def close_selected_terminal(terminal_path, record):
    target = str(Path(terminal_path).resolve())
    record('INFO', f'Checking for an existing terminal at {target}')
    # Pass the path as data, never as interpolated PowerShell code. Revalidate
    # the executable on the PID-bound Process object immediately before closing.
    script = """
$ErrorActionPreference = 'Stop'
$outcomes = @()
try {
    $targetPath = [IO.Path]::GetFullPath($env:MT5_BATCH_CLOSE_TARGET)
    $matches = @(Get-Process | Where-Object {
        $_.ProcessName -eq 'terminal64' -and $_.Path -and
        [IO.Path]::GetFullPath($_.Path) -ieq $targetPath
    })
    foreach ($candidate in $matches) {
        $terminalProcess = Get-Process -Id $candidate.Id -ErrorAction SilentlyContinue
        if ($null -eq $terminalProcess) { continue }
        if (-not $terminalProcess.Path -or
            [IO.Path]::GetFullPath($terminalProcess.Path) -ine $targetPath) {
            throw "Terminal PID identity changed; refusing to close it."
        }
        $terminalPid = $terminalProcess.Id
        if (-not $terminalProcess.CloseMainWindow()) {
            if ($terminalProcess.HasExited) { continue }
            throw "Terminal PID $terminalPid did not accept a normal close request. Close it manually."
        }
        if (-not $terminalProcess.WaitForExit(15000)) {
            throw "Terminal PID $terminalPid did not close within 15 seconds. Close it manually."
        }
        $outcomes += $terminalPid
    }
    ConvertTo-Json -InputObject $outcomes -Compress
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', script],
        env=dict(os.environ, MT5_BATCH_CLOSE_TARGET=target),
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    if result.returncode:
        raise RuntimeError('Could not close selected terminal: ' +
                           (result.stderr.strip() or 'process check failed'))
    closed = json.loads(result.stdout or '[]')
    for pid in closed:
        record('INFO', f'Existing selected terminal closed normally; PID={pid}')
    if not closed:
        record('INFO', 'No existing selected terminal needed closing.')
