# MT5 Bulk Backtester

This repository contains only the local Windows batch backtester, optimiser and shared CLI.
Read CLI_GUIDE.md before unattended terminal jobs. Always inspect a --dry-run plan first.
Coordinate terminal use: no simultaneous dashboard/CLI batches. Do not launch real MT5 jobs as tests.
Use .venv/Scripts/python.exe. Run python -m unittest discover -v for regression checks.
Keep machine configuration, sets, reports and EA binaries out of Git.
