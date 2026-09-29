"""Local Windows dashboard for MT5 batch testing."""
import os
import streamlit as st

st.set_page_config(page_title="MT5 Bulk Backtester", page_icon="📊", layout="wide")


def home():
    st.title("MT5 Bulk Backtester")
    st.caption("Batch backtesting and optimisation on your Windows PC")
    st.markdown("Run a folder of Expert Advisor settings through your installed MetaTrader 5 terminal. "
                "Choose a module below; both share your saved terminal and test settings.")
    st.subheader("Before your first run")
    st.markdown("""
1. Install and open MetaTrader 5, connect to your broker, and install your EA under **MQL5 → Experts**.
2. In MT5, use **File → Open Data Folder** to locate the data folder. The app needs its **Tester** subfolder and the matching **terminal64.exe** path.
3. Export your EA settings as `.set` files into a local folder. Check that the broker symbols and required history are available.
4. Close the selected MT5 terminal. Open either module and save **Active Config** (or **First-Run Setup**): terminal, dates, model, deposit, currency, leverage and broker suffix.
5. Select the EA, review the file preview, and start the batch. Run only one batch at a time across dashboard tabs and CLI sessions.
""")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Batch Backtester")
        st.markdown("""
Test each set of fixed inputs and save an HTML report.

1. Select your EA and the folder containing `.set` files.
2. Choose lot handling. Manual/balance overrides use EA-specific inputs (`Risk`, `StartLots`, `LotPerBalance_step`); use **as-is** for other EAs.
3. Enter a strategy label and choose symbol/timeframe defaults or detection. Review and edit the preview before starting.
4. Click **Start Batch** and follow progress. Reports and charts are saved in `reports` beside each source set.

The dashboard prepares working copies in `_batch_modified`, including EA comments and selected overrides. Original sets are preserved. Files starting with `Optimization` are excluded.
""")
        st.page_link(backtest_page, label="Open Batch Backtester", icon="▶️")
    with right, st.container(border=True):
        st.subheader("Batch Optimiser")
        st.markdown("""
Search parameter ranges for each set and export XML results.

1. Export sets with at least one optimisation input enabled (`||Y`) and valid start/step/stop ranges.
2. Select the EA, optimisation method and ranking criterion.
3. Choose the source folder, symbols and timeframes. Review the preview and time limit, then start.
4. Find XML workbooks in `OptimisationReport` beside each source set. **Stop after current optimization** lets the current export finish.

Sets and their optimisation ranges are copied unchanged. Runs use local agents, with forward testing disabled. Matching reports can be skipped using their input/settings fingerprint.
""")
        st.page_link(optimizer_page, label="Open Batch Optimiser", icon="⚙️")
    with st.expander("Reports, repeat runs and troubleshooting"):
        st.markdown("""
- Keep the app running while a batch is active. MT5 starts and shuts down for each set. Use a dedicated testing terminal.
- Backtester dashboard **Skip existing** matches report names; turn it off after changing dates or inputs. The CLI uses input/settings fingerprints.
- If an EA uses `ForceSymbol`, confirm it agrees with your chart symbol. UBS detection is optional; review detected values for other EAs.
- Missing reports: check the terminal/data-folder pairing, EA path, broker symbol, history and MT5 tester journal.
- Shared configuration is saved locally in `mt5_batch_config.json` and excluded from Git. EAs, broker accounts and sets are not bundled.
- For unattended runs, see `CLI_GUIDE.md`. Start with `python mt5_batch_cli.py --help` and validate jobs with `--dry-run`.
""")


def backtest():
    if os.name != "nt":
        st.error("Batch backtesting requires Windows and a local MetaTrader 5 installation.")
        return
    from view_batch_backtest import render
    render()


def optimize():
    if os.name != "nt":
        st.error("Batch optimisation requires Windows and a local MetaTrader 5 installation.")
        return
    from view_batch_optimizer import render
    render()


home_page = st.Page(home, title="Home", icon="🏠", default=True)
backtest_page = st.Page(backtest, title="Batch Backtester", icon="▶️", url_path="backtester")
optimizer_page = st.Page(optimize, title="Batch Optimiser", icon="⚙️", url_path="optimiser")
with st.sidebar:
    st.title("MT5 Bulk Backtester")
    st.caption("Local Windows edition")
st.navigation([home_page, backtest_page, optimizer_page]).run()
