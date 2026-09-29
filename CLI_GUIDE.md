# MT5 batch CLI — agent handoff

Use `mt5_batch_cli.py` for unattended runs. It supports `backtest` and `optimize`,
does not import Streamlit, and never prompts for input. Python's standard library
is sufficient. Live runs require Windows and an installed, configured MT5 terminal.

Start in your MT5BulkBacktester checkout. Use the app's Python environment:

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py --help
```

## Agent workflow

1. Confirm the requested source folder or per-file job list, EA, symbol, dates,
   timeframe and model. Use the intended MT5 installation and its matching data folder.
2. Run the command with `--dry-run`. Inspect the JSON plan and report destinations.
   Dry runs read files but do not launch MT5, check whether it is open, or write files.
3. Close the selected terminal before a live batch. Do not run the dashboard's
   backtester/optimizer simultaneously. Concurrent CLI batches for the same terminal
   are blocked by an operating-system lock, released even if the CLI crashes.
4. Repeat the command without `--dry-run`. Read JSON progress from stdout, then check
   the exit code and final `summary` event. Never interpret a report's existence alone
   as a successful new run.
5. The summary records each completed, skipped or failed set, report paths, and any
   uncompleted jobs. Fix failed settings before retrying with `--skip-existing`.

This launches strategy tester jobs only. Source set files, EA comments, lot inputs,
optimization ranges and saved app configuration are never edited. MT5 receives an
unchanged temporary copy of each set. In `backtest` mode optimization is forced off,
so the current input values are tested, even if the source set has enabled ranges.

## Backtests: OHLC versus real ticks

Replace `C:\YourSets` with the selected folder. These examples use the EA and account
settings from `mt5_batch_config.json`; specify `--ea` if that saved EA is not intended.

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py backtest --folder "C:\YourSets" --symbol GoldDukascopy --period D1 --from-date 2024.08.01 --to-date 2026.08.31 --model ohlc --skip-existing --dry-run
```

After reviewing the plan:

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py backtest --folder "C:\YourSets" --symbol GoldDukascopy --period D1 --from-date 2024.08.01 --to-date 2026.08.31 --model ohlc --skip-existing
.\.venv\Scripts\python.exe mt5_batch_cli.py backtest --folder "C:\YourSets" --symbol GoldDukascopy --period D1 --from-date 2024.08.01 --to-date 2026.08.31 --model realticks --skip-existing
```

Each set's `reports` subfolder receives its HTML report and companion PNG charts.
Filenames include `OHLC`, `REALTICKS`, `EVERYTICK` or `OPENPRICES`, plus a settings/input
fingerprint. Changing the model, dates or inputs produces a separate report. These
commands produce paired reports; they do not automatically analyze their differences.

## Optimization with XML export

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py optimize --folder "C:\YourOptimizationSets" --symbol GoldDukascopy --period D1 --model ohlc --method complete --criterion 1 --skip-existing --dry-run
```

Remove `--dry-run` after checking the plan. XML files go in `OptimisationReport`
beside each source set, using the same naming and resume logic as the dashboard.
At least one input must have optimization enabled (`||Y`). `--method complete`
selects the complete grid; `--method genetic` selects the genetic algorithm.
The optimization method must be explicitly provided unless the config already has
`optimization` set to `1` or `2`.

| Criterion code | MT5 name |
|---|---|
| 0 | Balance max |
| 1 | Profit Factor max |
| 2 | Expected Payoff max |
| 3 | Drawdown min |
| 4 | Recovery Factor max |
| 5 | Sharpe Ratio max |
| 6 | Custom max — EA's OnTester value |
| 7 | Complex Criterion max |

Absent `--criterion`, the config's `optimization_criterion` is used, or `0` if absent.
Both modes use local agents, no MQL5 Cloud agents, no forward testing, and normal
execution mode. MT5 shuts down after each set and is launched again for the next.

## Settings and file selection

- `--config "C:\path\settings.json"`: alternate config. The existing app config is
  the default; CLI overrides are applied for this run without saving them back.
- `--terminal`, `--tester-folder`, `--ea`: override the executable, Tester folder,
  and EA path relative to `MQL5\Experts` (for example `Market\My EA.ex5`). The CLI
  checks that the executable and EA exist before launching any jobs.
- `--from-date`, `--to-date`: dates in `YYYY.MM.DD` format.
- `--deposit`, `--currency`, `--leverage`: tester account settings, not EA lot-size overrides.
- `--model`: `ohlc` (1), `everytick` (0, generated ticks), `realticks` (4), or
  `openprices` (2). Numeric model codes are also accepted.
- `--symbol`, `--period`: explicit chart settings; D1/Daily, W1/Weekly and MN1/Monthly
  aliases work. A nonblank EA `ForceSymbol` still takes precedence inside the EA;
  inspect it before using a different chart symbol. The CLI does not modify that input.
- By default, `--detect ubs` reads missing symbol/timeframe fields from UBS inputs,
  with filename fallback for timeframe only. Unresolved values are errors.
- `--detect filename --symbol-chars 6` uses leading filename characters plus the
  configured suffix for missing symbols. Use it only with consistent naming.
- `--detect none` requires explicit chart settings. Explicit `--symbol` and
  `--period` always take precedence over detection.
- Recursive scanning is on; use `--no-recursive` for the root folder only.
  `reports`, `OptimisationReport`, `_batch_modified` and hidden directories are ignored.
  All `.set` names are included, including names beginning with Optimization.
- `--include "Selected*.set"`: restrict scanned filenames with a case-insensitive glob.
- `--skip-existing`: skip valid reports with the same fingerprint. Older backtester
  reports without the fingerprint do not qualify. This does not detect broker-history
  revisions or a replaced EA binary under the same name; omit the flag to retest those.
- `--timeout-hours 48`: maximum runtime per set. Default 24. A timed-out process
  launched by this runner is terminated and the set is marked failed.

All sets are validated before the first launch. Relative config paths are resolved
from the current working directory. `--folder` paths may contain spaces.

## Different settings per file

Instead of `--folder`, supply `--jobs` with a JSON array:

```json
[
  {"path": "sets/control.set", "symbol": "GoldDukascopy", "period": "D1"},
  {"path": "sets/trailing.set", "symbol": "GoldDukascopy", "period": "D1"}
]
```

Set paths are relative to the job-list file, or can be absolute. Each entry needs
`path`, `symbol` and `period`. Duplicate source files are rejected. Global `--symbol`
and `--period` override job entries if supplied; folder-scanning options do not apply.
The entire job list shares one EA and the same tester/account settings.

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py backtest --jobs "C:\RunPlans\jobs.json" --model realticks --dry-run
```

## Logs, stopping and exit codes

Every stdout line is one JSON event, suitable for agent parsing. Live runs also save
`events.jsonl` and `summary.json` under `batch_runs\<unique run id>`. Use
`--output-dir "C:\RunLogs\my-new-run"` to select a **new** folder; existing folders
are rejected to preserve previous evidence. The report files remain beside the sets.

Use Ctrl+C or `--stop-file "C:\RunLogs\stop.flag"` to stop after the current set
finishes and exports. Creating that file requests a stop; delete it before resuming.
An existing stop file prevents any job launch. The CLI remains active until the current
set finishes or times out. Do not forcibly kill the CLI to request a graceful stop.

| Exit code | Meaning |
|---|---|
| 0 | Dry run valid, or all selected jobs saved/skipped successfully |
| 1 | One or more jobs failed, or jobs remained unexpectedly uncompleted |
| 2 | Invalid arguments/settings, preflight error, terminal busy, or log/output error |
| 130 | Graceful stop requested; inspect summary for completed and pending sets |

Argument syntax errors are printed by argparse to stderr with exit code 2. Other
validation errors are JSON events. No live MT5 jobs are launched by the automated tests:

```powershell
.\.venv\Scripts\python.exe -m unittest test_batch_optimizer test_mt5_batch_cli test_batch_optimizer_view -v
```

The older `mt5_batch_backtest.py` interactive CLI and the dashboard remain available.
Use this new CLI for non-interactive agent work.
