# AIR 規範（常駐）

這個專案採用 AIR 規範。這份文件只放地圖和最重要的 3 條規則；各層的詳細規則在 `AIR/rules/<層>.md`，**需要時才讀**（第一次讀寫某一層的檔案時，hook 會自動附上那一層的規則）。

## 架構

依賴方向由上往下；同一列的層互不依賴。各層都是套件底下的子套件，套件位置寫在 `.air/project.toml`（package、package-dir）。

```
main/                          組裝：載入 → 執行 flow → 儲存
flows/ │ adapters/ │ storage/  串接步驟 │ 包裝第三方工具 │ 所有讀寫
steps/                         純邏輯
contracts/                     跨層的型別（types.py）與介面（ports.py）
```

| 層 | 一句話 |
|---|---|
| contracts | 最底層：資料型別與 Protocol，不依賴任何層 |
| steps | 純函式：不碰 I/O、時間、亂數；除了 pandas、numpy 等資料函式庫，不用第三方套件 |
| flows | 只串接步驟與注入的工具，不寫計算 |
| adapters | 唯一可以使用第三方工具的地方；一個工具一個檔案或一個子套件；內部只能用 contracts 與同層（不能循環） |
| storage | 所有讀寫；內部只能用 contracts 與同層（不能循環） |
| main | 組裝與注入 |

**還沒搬進任何層的程式是「舊程式碼」**：不受分層檢查，遷移期間可以修改，但**新的程式一律寫進對應的層**。

## 依位置讀規則

改到哪一層，就照那一層的規則：`AIR/rules/contracts.md`、`steps.md`、`flows.md`、`adapters.md`、`storage.md`、`main.md`，測試是 `tests.md`；沒改到的層不用讀。有 hook 的工具（Claude Code、Codex）會在你第一次讀或改某一層的檔案時自動附上，同一個 session 不再重複；沒有 hook 的工具請自行讀取。

## 永遠適用（只有這三條）

1. 程式會在編輯後、結束前、commit 時被檢查。違規時**修改已經寫入檔案**，必須修正。訊息中有規則編號，依前綴讀 `AIR/rules/` 對應的規則：`CTR`→contracts、`STEP`→steps、`FLOW`→flows、`ADP`→adapters、`IO`→storage、`MAIN`→main、`TEST`→tests。檢查只涵蓋部分規則，**沒報錯不代表合規**；其餘規則在每批 commit 前照 skill `air-review` 審查。
2. 函式長度、複雜度的提醒（AIR101、AIR102）只是參考，不要為了消除提醒而拆函式。
3. 新程式一律寫進對應的層。不確定該放哪一層、或規則之間互相衝突時，停下來問使用者，不要自行新增層、放寬白名單或修改 `AIR/`。

## 遷移中

`.air/project.toml` 的 `migration.phase` 不是 `done` 時，專案正在遷移。做遷移相關的工作時，照 `AIR/START.md` 進行（有 skill 的工具：`air-migrate`；有 hook 的工具在 session 開始時會附上當下階段的那一節，不用整份讀），一次只做一個階段，做完就停下來等使用者核准。

## 指令

- 檢查：`python3 AIR/air.py check`（只檢查層內的程式；「沒有檢查任何檔案」不等於通過）
- 驗收：`python3 AIR/air.py verify`（測試指令＋架構檢查，結果綁定這一版程式碼；每批遷移結束、收尾前必須通過）
- 語意審查：`python3 AIR/air.py review`（列出這次變更要對照哪些沒有程式檢查的規則；做法見 skill `air-review`）
- 狀態：`python3 AIR/air.py status`；接線：`python3 AIR/air.py doctor`
- 不要用 `--no-verify`、`-n`、`SKIP=` 或修改 `core.hooksPath` 來略過 commit 前的檢查；這些寫法也會被擋下

## skills 與 hooks（Claude Code、Codex）

| 你要做的事 | skill |
|---|---|
| 安裝、分析、規劃或執行遷移 | `air-migrate` |
| 新增或修改步驟、把步驟串進流程 | `air-add-step` |
| 使用第三方工具或外部服務 | `air-wrap-tool` |
| 收到函式長度或複雜度的提醒 | `air-split-function` |
| 每批 commit 前依 AIR 規則審查變更（補程式檢查不到的規則） | `air-review` |
| 想放寬或修改規範 | `air-maintain-rules` |

hooks：session 開始與對話壓縮後附上目前狀態；第一次讀寫某一層的檔案時附上那一層的規則；編輯前擋下對 `AIR/`、hook 設定、AIR 複製的 skill 的修改，以及分析／計畫階段對 `.py` 的修改（改 `.air/project.toml` 會請使用者確認；Codex 沒有確認機制，改成提醒）；編輯 `.py` 後檢查該檔案；執行 Bash 前擋下略過檢查的指令；要結束前檢查所有層與套件完整性，有違規就要求繼續修正（只提醒一次）。
