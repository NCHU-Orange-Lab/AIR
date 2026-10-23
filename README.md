# AIR：讓 AI 遵守分層架構的規範套件

把這個 `AIR/` 資料夾放進 Python 專案的根目錄，再對 AI 說一句話，AI 就會照這套規範安裝、分析你的專案，並分階段把它重構成分層架構。之後的開發，AI 也會持續遵守這套規範。

- 需求：Python 3.11 以上、git。不需要安裝任何套件。Windows 另外需要 Git for Windows（hook 由 Git Bash 執行）；文件裡的 `python3` 在 Windows 可以換成 `python` 或 `py -3`，安裝程式與 hook 會自己找可用的 Python。
- 目前只支援 Python 專案。
- 版本：見 `VERSION`
- 套件自己的測試：`tests/`（只用標準庫，`python3 -m unittest discover -s tests -t .`）；GitHub 上每次 push 到 `main` 與 pull request 都會用 Python 3.11 到 3.14 在 ubuntu 與 Windows 各跑一遍，另外再把標準輸出入強制成 cp950 跑一遍（`.github/workflows/test.yml`）。

## 這套規範在做什麼

把程式分成六層，讓「流程」和「步驟」分開，I/O 和第三方工具都放在外層：

```
main/                          組裝：載入 → 執行 flow → 儲存
flows/ │ adapters/ │ storage/  串接步驟 │ 包裝第三方工具 │ 所有讀寫
steps/                         純邏輯（不碰 I/O、時間、亂數）
contracts/                     跨層的型別與介面
```

並且用程式強制執行：AI 每次編輯檔案、每次要結束工作（只看這個 session 動過的檔案）、每次 commit 時都會被檢查，違規時會收到規則編號和修法。AI 也不能用編輯工具修改 `AIR/`、hook 設定或 AIR 複製的 skill；`AIR/` 的每個檔案都列在 `MANIFEST.sha256`，用其他方式改了，結束前與 commit 前也會被抓到（AIR009）。程式檢查不到的規則（例如 flow 不寫計算、adapter 關閉副作用），每批 commit 前由 AI 照 skill `air-review` 審查：`review` 指令用 git 界定這次的變更、列出要對照的規則，審查結果要通過格式與範圍的驗證才會被記錄。

## 怎麼開始

1. 把 `AIR/` 資料夾複製到專案根目錄（git repo 的根目錄）。**不要帶著 `.git`**：從 AIR-dev 複製時，只複製裡面的 `AIR/` 資料夾。
2. 工作樹不用乾淨：安裝程式最後會列出它建立或修改的檔案，commit 時只加那些。
3. 對 AI 說：**「照 AIR/START.md 安裝」**

AI 會分 6 個階段進行，**每個階段結束都會停下來等你核准**：

| 階段 | AI 會做什麼 | 你要做什麼 |
|---|---|---|
| 1. 安裝 | 確認套件位置、執行 `AIR/install.sh` | 確認安裝結果，同意 commit |
| 2. 分析 | 盤點所有模組、確認測試指令、寫出每個模組該歸到哪一層 | 審閱 `docs/AIR-遷移報告.md` 的分析 |
| 3. 計畫 | 把遷移切成數個批次 | 核准計畫 |
| 4. 建立分層 | 建立空的各層目錄與規則 | 核准 |
| 5. 逐批遷移 | 每一批：先補行為保護測試 → 搬移 → `verify`（測試＋架構檢查）通過 → 語意審查（`air-review`）→ commit | 每批結束時核准下一批 |
| 6. 收尾 | 移除過渡用的轉接，`verify --scope final` 通過 | 確認完成 |

遷移期間，還沒搬進任何層的程式屬於「舊程式碼」，不受檢查，專案可以照常運作。不打算遷移的實驗區、腳本、筆記本，經你同意寫進 `.air/project.toml` 的 `migration.exclude`：它們不凍結、不進驗收指紋、不列進盤點，留在外面算正式的結局。分析、計畫階段的程式凍結只涵蓋套件目錄與測試目錄。

階段只能一次前進一格，而且有前置條件（例如進入 `done` 需要一份剛通過、程式碼沒再改過的 `verify --scope final` 紀錄）；AI 不能直接把階段設成 `done`。每次轉移都記在 `.air/history.jsonl`。

遷移要先擱置（例如先趕別的工作）時，執行 `python3 AIR/air.py pause --reason <原因>`：階段保留，分析／計畫階段的程式凍結解除，AI 在 session 開始會看到「暫停中」而不是該階段的待辦，架構檢查照常；`python3 AIR/air.py resume` 繼續。這兩個指令由你執行，AI 不會自己跑。

## 支援的 AI 工具

規則、檢查與 skill 不分工具；只有接點不同，安裝時全部裝好，同一個專案裡誰用哪個工具都可以：

| 工具 | hook 設定 | skills | 差異 |
|---|---|---|---|
| Claude Code（Grok Build 讀同一份設定） | `.claude/settings.json` | `.claude/skills/` | 五個 hook；各層規則放在 `.claude/rules/air-<層>.md`，讀寫該層時由 Claude Code 自己載入（不啟動 Python）；修改 `.air/project.toml` 會跳出確認 |
| Codex | `.codex/hooks.json` | `.agents/skills/` | 五個 hook；各層規則在第一次修改該層時由 hook 附上；不支援確認，改成提醒；**hook 要在 Codex 裡信任過才會執行** |

只用其中一種：`sh AIR/install.sh --host claude`（或 `--host codex`）。`python3 AIR/air.py doctor` 列出每個工具的接線狀態。

## 安裝後專案裡會多出什麼

| 位置 | 內容 | 進版本控制 |
|---|---|---|
| `AIR/` | 這個套件 | 是 |
| `AGENTS.md` | 標記區塊：`AIR/AGENTS.md` 的全文（常駐規則，1,500 字內） | 是 |
| `CLAUDE.md` | 標記區塊：匯入常駐規則（AIR 建立的匯入 `AGENTS.md`；你原有的匯入 `AIR/AGENTS.md`） | 是 |
| `.claude/settings.json` | Claude Code 的五個 hook（session 開始、編輯前、編輯後、執行 Bash 前、結束前） | 是 |
| `.claude/rules/air-<層>.md` | 各層規則（`AIR/rules/<層>.md` 的複本，前面加 `paths` 指向該層目錄）；Claude Code 讀寫該層時自動載入 | 是 |
| `.claude/skills/air-*/` | 六個 skill（Claude Code） | 是 |
| `.codex/hooks.json`、`.agents/skills/air-*/` | Codex 的五個 hook 與六個 skill | 是 |
| `.air/project.toml` | 這個專案的設定：套件位置、測試指令（`[tests]`）、遷移階段、不歸 AIR 管的路徑（`migration.exclude`） | 是 |
| `.air/history.jsonl` | 每次階段轉移、暫停與繼續的紀錄（時間、commit、方式、原因） | 是 |
| `.air/paused.json` | 遷移暫停中的紀錄（時間、階段、原因）；`resume` 時移除 | 是 |
| `.air/python`、`.air/hosts` | 這台電腦的 Python 路徑、裝了哪些工具的接點 | 否 |
| `.air/sessions/` | 每個 session 的紀錄：已附過哪一層的規則（Codex）、編輯工具動過的檔案、開始時各層檔案的快照（結束前的檢查用） | 否 |
| `.air/hook-errors.log` | hook 自己壞掉的紀錄（找不到 Python、payload 壞掉）；`status` 與 `doctor` 會顯示最近 24 小時的次數 | 否 |
| `.air/metrics.jsonl` | 成本的代理指標：verify 幾次、審查範圍印了多少字、擋下幾次；`status` 顯示累計 | 否 |
| `.air/branches/<分支>/` | 這個分支的本機紀錄：`verify.json`（最近一次驗收，綁定當時的程式碼）、`review.json`（最近一次語意審查，每個檔案一筆）、`last-check.json`（結束前的檢查有違規時的完整清單）；切分支不會互相覆蓋 | 否 |
| `docs/AIR-遷移報告.md` | AI 寫的分析、計畫與進度 | 是 |
| commit 前檢查 | 有用 pre-commit 就加進 `.pre-commit-config.yaml`；沒有就裝 `.git/hooks/pre-commit` | `.git/hooks` 不進版本控制 |

安裝程式不會覆蓋不是它建立的東西（原有的 skill、git hook、CLAUDE.md），遇到時會列出「略過」。

**換一台電腦或重新 clone 之後**，要再執行一次 `sh AIR/install.sh`，產生 `.air/python`、`.air/hosts` 和 git hook。沒有 `.air/python` 時（例如新的 git worktree），hook 會自己找可用的 Python；找不到就寫進 `.air/hook-errors.log` 並放行，`doctor` 會指出。

## 更新與移除

- **更新**：用新版的 `AIR/` 整包取代舊的，再執行 `sh AIR/install.sh`。`.air/project.toml` 和遷移報告都會保留；2.2 以前放在各層目錄的 `CLAUDE.md` 會被移除；2.6.1 以前 Claude Code 讀檔前的 hook 會被移除、改成產生 `.claude/rules/air-<層>.md`；2.7 起測試指令改放 `[tests]`，舊的 `project.test-command` 仍可用（`doctor` 會提醒搬過去）。Codex 的 hook 設定變動後要重新信任。
- **移除**：`sh AIR/uninstall.sh`。會移除所有標記區塊、hook、skill 與 `.claude/rules/air-*.md`；`.air/` 與遷移報告會保留，加上 `--purge` 才會刪除。已經搬進各層的程式碼不會被動到。

## 常用指令

一條指令一列（不放在同一個可執行的區塊裡：有些工具會把整個區塊一次執行）。

| 指令 | 做什麼 |
|---|---|
| `python3 AIR/air.py status` | 本機時間、套件、遷移階段、分支、檢查範圍、違規數、hook 錯誤、成本指標、最近驗收與審查 |
| `python3 AIR/air.py doctor` | 診斷接線：每個工具的 hook 與 skill（真的跑一次）、指引、套件完整性、commit 前檢查 |
| `python3 AIR/air.py check` | 快速檢查所有層；同一種違規只印一次修法（加 `--json` 得到逐條的機器可讀結果） |
| `python3 AIR/air.py verify` | 正式驗收：測試指令＋架構檢查，結果記在 `.air/branches/<分支>/verify.json`（會跑整套測試） |
| `python3 AIR/air.py review --bundle` | 語意審查的範圍：每層要對照的規則（只列一次）、這次改到的檔案，加上每個待審檔案的差異（一次拿齊）；審過沒變的檔案沿用，只有改過的要重審（`--result` 交回結果） |
| `python3 AIR/air.py survey --out docs/air-survey.md` | 盤點現有程式碼：完整表寫進檔案，畫面只印摘要 |
| `python3 AIR/air.py pause --reason <原因>` | 暫停遷移（階段保留、分析／計畫階段的 `.py` 凍結解除、session 開始改附「暫停中」）；`resume` 繼續 |

## 更多說明

- 規則、檢查、設定：[docs/規範說明.md](docs/規範說明.md)
- 為什麼這樣設計：[docs/設計說明.md](docs/設計說明.md)
- 給 AI 的安裝與遷移指引：[START.md](START.md)
