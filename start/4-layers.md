# 階段 4：建立分層

（共通原則在 `AIR/START.md`；這份只放這個階段的做法，其他階段不用讀。）

1. `python3 AIR/air.py layers`：建立各層的目錄（各層的規則在 `AIR/rules/`，讀寫該層時會自動附上：Claude Code 經 `.claude/rules`，Codex 經 hook）。
   - **專案已經有同名的模組**（最常見是 `main.py`，套件目錄裡有 `<層>.py`）時，它會拒絕、什麼都不建：層目錄會遮蔽同名模組，原本的程式 import 不到。那個模組就是這一層（`main.py` 本來就是進入點）時，執行 `python3 AIR/air.py layers --adopt-module main`：用 `git mv` 把它搬成 `main/__init__.py`（import 路徑不變），並列出它 import 的舊模組。把那些項目**經使用者同意後**加進 `.air/project.toml` 的 `migration.allow-legacy`，原因欄要寫真正的原因。不是這一層的話，先把模組改名。
   - 專案用 setuptools 的**明確 `packages` 清單**（`pyproject.toml`、`setup.cfg`、`setup.py`）時，輸出會指出沒列到哪些層的子套件：補進清單，否則 wheel 不含它們（editable 安裝看不出來）。
2. `python3 AIR/air.py check`：此時各層只有 `__init__.py` 與 contracts 的樣板（採用了 `main.py` 就會有 AIR008，加完 allow-legacy 後應該通過）。
3. `python3 AIR/air.py verify`：必須通過。建立分層可能弄壞專案（`check` 看不出來，要跑測試才知道）。
4. `python3 AIR/air.py phase layered`（會確認層目錄都存在、檢查通過、驗收新鮮且通過），然後 commit：`AIR：建立分層`。

**停下來**，等使用者核准後開始第 1 批：讀 `AIR/start/5-migrate.md`。
