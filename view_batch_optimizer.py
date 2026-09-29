"""Batch Optimizer page for MT5 Bulk Backtester."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re

import streamlit as st

from mt5_batch_backtest import DEFAULTS, get_ubs_symbol, get_ubs_period, detect_timeframe
from view_batch_backtest import load_config, render_config_form
from mt5_batch_optimizer import (BatchRun, METHODS, CRITERIA, PERIODS, discover_sets,
                                 optimized_inputs, report_path, validate_job)


@st.fragment(run_every='2s')
def render_progress():
    run = st.session_state.get('bo_run')
    if run is None:
        return
    while True:
        try:
            event = run.events.get_nowait()
        except queue.Empty:
            break
        if event['status'] == 'complete':
            st.session_state.bo_complete = True
            st.session_state.bo_stopped = event['stopped']
        elif event['status'] == 'error':
            st.session_state.bo_error = event['message']
        else:
            st.session_state.bo_results[event['idx']] = event
    results = list(st.session_state.bo_results.values())
    completed = sum(r['status'] in ('done', 'skipped', 'failed') for r in results)
    total = st.session_state.bo_total
    st.progress(completed / max(total, 1), text=f'{completed} / {total} sets processed')
    if st.session_state.get('bo_error'):
        st.error(st.session_state.bo_error)
    if results:
        st.dataframe([{'Set file': r['file'], 'Status': r['status'],
                       'Message': r['message'], 'XML report': r.get('report', '')}
                      for r in results], hide_index=True, use_container_width=True)
    if st.session_state.bo_complete:
        failures = sum(r['status'] == 'failed' for r in results)
        saved = sum(r['status'] == 'done' for r in results)
        skipped = sum(r['status'] == 'skipped' for r in results)
        label = 'Batch stopped' if st.session_state.bo_stopped else 'Batch finished'
        st.info(f'{label}: {saved} saved, {skipped} skipped, {failures} failed.')
    elif st.button('Stop after current optimization', key='bo_stop'):
        run.stop.set()
        st.info('The current optimization will finish and export before the batch stops.')


def render():
    st.title('Batch Optimizer')
    st.caption('Run MT5 optimizations from .set files. XML reports are saved in an '
               'OptimisationReport subfolder beside each source set.')
    run = st.session_state.get('bo_run')
    if run is not None and run.thread.is_alive():
        render_progress()
        st.button('Refresh batch controls', key='bo_refresh')
        return

    cfg = dict(DEFAULTS)
    cfg.update(load_config() or {})
    with st.expander('Active Config — shared terminal and test settings', expanded=not cfg.get('tester_folder')):
        saved, cfg = render_config_form(cfg)
        if saved:
            st.success('Config saved.')

    cfg = dict(cfg)
    experts_dir = Path(cfg.get('tester_folder') or '.').parent / 'MQL5' / 'Experts'
    eas = sorted(str(p.relative_to(experts_dir)) for p in experts_dir.rglob('*.ex5')) if experts_dir.is_dir() else []
    current = cfg.get('ea_name', '')
    if current and current not in eas:
        eas.insert(0, current)
    if eas:
        cfg['ea_name'] = st.selectbox('Expert Advisor', eas,
            index=eas.index(current) if current in eas else 0, key='bo_ea')
    else:
        cfg['ea_name'] = st.text_input('Expert Advisor (relative to MQL5/Experts)', value=current, key='bo_ea_text')
    col1, col2 = st.columns(2)
    cfg['optimization'] = col1.selectbox('Optimization method', list(METHODS),
                                       format_func=METHODS.get, key='bo_method')
    cfg['optimization_criterion'] = col2.selectbox('Optimization criterion', list(CRITERIA),
                                                 format_func=CRITERIA.get, key='bo_criterion')
    st.caption('Input values, start/step/stop ranges and enabled parameters come directly '
               'from each set file. Source files are kept unchanged. Runs use local agents; forward testing is off.')
    folder = st.text_input('Folder containing optimization .set files', key='bo_folder').strip().strip('"')
    col1, col2 = st.columns(2)
    recursive = col1.toggle('Include subfolders', value=True, key='bo_recursive')
    skip = col2.toggle('Skip matching existing XML reports', value=True, key='bo_skip',
        help='The report name includes a fingerprint of the set contents and run settings.')
    timeout = st.number_input('Time limit per set (hours)', min_value=1, max_value=720, value=24, key='bo_timeout')
    mode = st.radio('Symbol and timeframe', ['Read UBS inputs / filename', 'Use defaults for all files'],
                    horizontal=True, key='bo_detection')
    col1, col2 = st.columns(2)
    symbol_default = col1.text_input('Default symbol (including broker suffix)', key='bo_symbol')
    period_default = col2.selectbox('Default timeframe', PERIODS, index=PERIODS.index('H1'), key='bo_period')
    st.caption('Review the chart symbol and timeframe below. EA inputs such as ForceSymbol '
               'still take precedence inside the EA and are preserved from the set file.')
    if folder and not Path(folder).is_dir():
        st.error('Folder not found.')
    paths = discover_sets(folder, recursive) if folder and Path(folder).is_dir() else []
    if paths:
        rows = []
        errors = []
        for path in paths:
            try:
                inputs = optimized_inputs(path)
                symbol = symbol_default.strip()
                period = period_default
                if mode == 'Read UBS inputs / filename':
                    symbol = get_ubs_symbol(path) or symbol
                    if not symbol:
                        # Only infer conventional FX names, not words like Optimization.
                        match = re.search(r'(?i)(?<![a-z])((?:AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD|XAU|XAG){2})(?![a-z])', Path(path).stem)
                        symbol = match[1].upper() + cfg.get('suffix', '') if match else ''
                    period = get_ubs_period(path) or detect_timeframe(Path(path).name) or period
                rows.append({'Set file': os.path.relpath(path, folder), 'Symbol': symbol,
                             'Period': period, 'Enabled inputs': len(inputs)})
                if not inputs:
                    errors.append(f'{os.path.relpath(path, folder)}: no enabled optimization inputs (||Y).')
            except Exception as exc:
                errors.append(f'{path}: {exc}')
        st.write(f'Found {len(paths)} set files.')
        for error in errors:
            st.error(error)
        if not errors:
            editor_key = 'bo_preview_' + hashlib.sha256(json.dumps([paths, rows]).encode()).hexdigest()[:12]
            edited = st.data_editor(rows, hide_index=True, use_container_width=True,
                disabled=['Set file', 'Enabled inputs'], key=editor_key,
                column_config={'Period': st.column_config.SelectboxColumn(options=PERIODS, required=True),
                               'Symbol': st.column_config.TextColumn(required=True)})
            jobs = [{'path': path, 'symbol': str(row['Symbol'] or '').strip(), 'period': row['Period']}
                    for path, row in zip(paths, edited)]
            st.caption('Close the selected MT5 terminal before starting. Each optimization opens it '
                       'and closes it when finished, as in Batch Backtest.')
            if st.button('Start batch optimization', type='primary', key='bo_start',
                         disabled=bool(st.session_state.get('bb_running'))):
                try:
                    if not cfg['ea_name'].strip():
                        raise ValueError('Select an Expert Advisor.')
                    start = datetime.strptime(cfg['from_date'], '%Y.%m.%d')
                    end = datetime.strptime(cfg['to_date'], '%Y.%m.%d')
                    if end <= start:
                        raise ValueError('To date must be after From date.')
                    if float(cfg['deposit']) <= 0 or int(cfg['leverage']) <= 0:
                        raise ValueError('Deposit and leverage must be positive.')
                    for job in jobs:
                        validate_job(job, cfg)
                        report_path(job, cfg)
                    run = BatchRun()
                    st.session_state.update(bo_run=run, bo_results={}, bo_total=len(jobs),
                                            bo_complete=False, bo_stopped=False, bo_error='')
                    run.start(jobs, cfg, skip, timeout)
                    st.rerun()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
    elif folder:
        st.info('No .set files found.')
    render_progress()
