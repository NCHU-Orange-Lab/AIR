# AIR：讓 AI 遵守分層架構的規範套件

把這個 `AIR/` 資料夾放進 Python 專案的根目錄，再對 AI 說一句話，AI 就會照這套規範安裝、分析你的專案，並分階段把它重構成分層架構。之後的開發，AI 也會持續遵守這套規範。

- 需求：Python 3.11 以上、git。不需要安裝任何套件。
- 目前只支援 Python 專案。
- 版本：見 `VERSION`

## 這套規範在做什麼

把程式分成六層，讓「流程」和「步驟」分開，I/O 和第三方工具都放在外層：

```
main/                          組裝：載入 → 執行 flow → 儲存
flows/ │ adapters/ │ storage/  串接步驟 │ 包裝第三方工具 │ 所有讀寫
steps/                         純邏輯（不碰 I/O、時間、亂數）
contracts/                     跨層的型別與介面
```

並且用程式強制執行：AI 每次編輯檔案、每次要結束工作、每次 commit 時都會被檢查，違規時會收到規則編號和修法。

## 怎麼開始

1. 把 `AIR/` 資料夾複製到專案根目錄（git repo 的根目錄）。**不要帶著 `.git`**：從 AIR-dev 複製時，只複製裡面的 `AIR/` 資料夾。
2. 確認 `git status` 是乾淨的。
3. 對 AI 說：**「照 AIR/START.md 安裝」**

AI 會分 6 個階段進行，**每個階段結束都會停下來等你核准**：

| 階段 | AI 會做什麼 | 你要做什麼 |
|---|---|---|
| 1. 安裝 | 確認套件位置、執行 `AIR/install.sh` | 確認安裝結果，同意 commit |
| 2. 分析 | 盤點所有模組、確認測試指令、寫出每個模組該歸到哪一層 | 審閱 `docs/AIR-遷移報告.md` 的分析 |
| 3. 計畫 | 把遷移切成數個批次 | 核准計畫 |
| 4. 建立分層 | 建立空的各層目錄與規則 | 核准 |
| 5. 逐批遷移 | 每一批：先補行為保護測試 → 搬移 → `verify`（測試＋架構檢查）通過 → commit | 每批結束時核准下一批 |
| 6. 收尾 | 移除過渡用的轉接，`verify --scope final` 通過 | 確認完成 |

遷移期間，還沒搬進任何層的程式屬於「舊程式碼」，不受檢查，專案可以照常運作。

階段只能一次前進一格，而且有前置條件（例如進入 `done` 需要一份剛通過、程式碼沒再改過的 `verify --scope final` 紀錄）；AI 不能直接把階段設成 `done`。每次轉移都記在 `.air/history.jsonl`。

## 安裝後專案裡會多出什麼

| 位置 | 內容 | 進版本控制 |
|---|---|---|
| `AIR/` | 這個套件 | 是 |
| `CLAUDE.md`、`AGENTS.md` | 各加一個標記區塊，指向 `AIR/AGENTS.md` | 是 |
| `.claude/settings.json` | 三個 hook（編輯後、執行 Bash 前、結束前） | 是 |
| `.claude/skills/air-*/` | 五個 skill | 是 |
| `.air/project.toml` | 這個專案的設定：套件位置、測試指令、遷移階段 | 是 |
| `.air/history.jsonl` | 每次階段轉移的紀錄（時間、commit、方式、原因） | 是 |
| `.air/python` | 這台電腦的 Python 路徑 | 否 |
| `.air/verify.json` | 最近一次 `verify` 的結果，綁定當時的程式碼 | 否 |
| 各層與 tests 的 `CLAUDE.md` | 該層的規則 | 是 |
| `docs/AIR-遷移報告.md` | AI 寫的分析、計畫與進度 | 是 |
| commit 前檢查 | 有用 pre-commit 就加進 `.pre-commit-config.yaml`；沒有就裝 `.git/hooks/pre-commit` | `.git/hooks` 不進版本控制 |

安裝程式不會覆蓋不是它建立的東西（原有的 skill、git hook、CLAUDE.md），遇到時會列出「略過」。

**換一台電腦或重新 clone 之後**，要再執行一次 `sh AIR/install.sh`，產生 `.air/python` 和 git hook。

## 更新與移除

- **更新**：用新版的 `AIR/` 整包取代舊的，再執行 `sh AIR/install.sh`。`.air/project.toml` 和遷移報告都會保留。
- **移除**：`sh AIR/uninstall.sh`。會移除所有標記區塊、hook、skill 與各層的規則檔；`.air/` 與遷移報告會保留，加上 `--purge` 才會刪除。已經搬進各層的程式碼不會被動到。

## 常用指令

```sh
python3 AIR/air.py status      # 套件、遷移階段、檢查範圍、違規數、最近驗收
python3 AIR/air.py check       # 快速檢查所有層（加 --json 得到機器可讀的結果）
python3 AIR/air.py verify      # 正式驗收：測試指令＋架構檢查，結果記在 .air/verify.json
python3 AIR/air.py survey      # 盤點現有程式碼
```

## 更多說明

- 規則、檢查、設定：[docs/規範說明.md](docs/規範說明.md)
- 為什麼這樣設計：[docs/設計說明.md](docs/設計說明.md)
- 給 AI 的安裝與遷移指引：[START.md](START.md)
