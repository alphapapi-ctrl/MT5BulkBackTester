# MT5 Bulk Backtester

A local Windows Streamlit app containing the **Batch Backtester** and **Batch Optimiser** extracted from MT5 Tools. The home page explains setup and each workflow; each module has its own sidebar page.

## Requirements

- Windows 10 or 11 and Python 3.11+ (with pip).
- An installed, configured MetaTrader 5 terminal and your Expert Advisor.
- Local `.set` files, available broker symbols and historical data.
- Internet access for initial Python dependency installation and any MT5 history downloads.

## Install and launch

```powershell
git clone https://github.com/alphapapi-ctrl/MT5BulkBackTester.git
cd MT5BulkBackTester
.\setup.bat
.\start.bat
```

You can also download and extract the repository ZIP, double-click `setup.bat` once, then double-click `start.bat`. The app opens in your browser at **http://127.0.0.1:8501** and runs on your PC. Keep the launcher running during batches. No hosted service is required.

For manual setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## First run

Open MT5, install the EA, connect to the broker and locate **File > Open Data Folder**. Close that terminal before batching. On a module page, save the matching executable and `Tester` folder paths, date range, tick model and account settings. Leave the suffix empty unless your broker requires one. Select your EA relative to `MQL5\Experts`.

Use a dedicated tester installation. Run one batch at a time across all app tabs, processes and CLI sessions. MT5 is launched and shut down for each set.

## Batch Backtester

Select your settings folder, lot handling, strategy label, symbol and timeframe. Review the editable preview, then start the batch. HTML reports and charts are written to `reports` beside each set. Original sets are preserved; the dashboard prepares working copies in `_batch_modified` and updates EA comments and selected lot/symbol inputs. Lot overrides target `Risk`, `StartLots` and `LotPerBalance_step`; for EAs with different inputs, configure lots in the source set and use as-is mode.

Dashboard scanning excludes filenames starting with `Optimization`. Its skip-existing option matches report names, so disable it after changing dates or inputs. The unattended CLI includes all set names and uses report fingerprints instead.

## Batch Optimiser

Use sets with at least one enabled optimisation input (`||Y`). Choose complete or genetic optimisation and a criterion, then review symbols, timeframes and the per-set timeout. Inputs and optimisation ranges are preserved. XML workbooks go to `OptimisationReport` beside each source set. Matching input/settings fingerprints support resume. Stop-after-current allows the active export to finish. Runs use local agents and no forward testing.

For other EAs, use explicit symbols/timeframes or filename detection; UBS input detection is optional. Check any EA-internal `ForceSymbol` before starting.

## CLI and tests

See [CLI_GUIDE.md](CLI_GUIDE.md) for dry runs, JSON jobs, progress logs and exit codes. The CLI needs only the Python standard library; the dashboard needs the packages in `requirements.txt`.

```powershell
.\.venv\Scripts\python.exe mt5_batch_cli.py --help
.\.venv\Scripts\python.exe -m unittest discover -v
```

Automated tests use simulated MT5 jobs and never launch a real terminal.

## Local data and troubleshooting

`mt5_batch_config.json` is created on setup and ignored by Git, along with reports, logs, sets and EA binaries. No personal terminal configuration is distributed. If no report appears, check terminal/data-folder pairing, EA path, broker symbol, available history and the tester journal. Keep enough disk space for reports and MT5 history.

The application is derived from the batch modules in [MT5 Tools](https://github.com/alphapapi-ctrl/mt5-tools). Portfolio, trade-analysis and live-account modules are not included.
