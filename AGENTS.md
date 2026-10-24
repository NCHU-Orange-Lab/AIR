# AIR 規範（常駐）

這個專案採用 AIR 規範。這份文件只放地圖和 3 條常駐規則；各層的規則在 `AIR/rules/<層>.md`，**需要時才讀**。

## 架構

依賴方向由上往下：main → flows│adapters│storage → steps → contracts；同一列的層互不依賴。各層是套件（`.air/project.toml` 的 package-dir）底下的子套件。

| 層 | 一句話 |
|---|---|
| contracts | 最底層：跨層的型別（types.py）與 Protocol（ports.py） |
| steps | 純函式：不碰 I/O、時間、亂數；除了 pandas、numpy 等資料函式庫，不用第三方套件 |
| flows | 只串接步驟與注入的工具，不寫計算 |
| adapters | 唯一可以用第三方工具的地方；一個工具一個檔案或一個子套件；只能 import contracts 與同層（不能循環） |
| storage | 所有讀寫；只能 import contracts 與同層（不能循環） |
| main | 組裝與注入：載入 → 執行 flow → 儲存 |

**還沒搬進任何層的程式是「舊程式碼」**：不受分層檢查，遷移期間可以修改，但**新的程式一律寫進對應的層**。

## 依位置讀規則

改到哪一層就照 `AIR/rules/<層>.md`（測試是 `tests.md`），沒改到的層不用讀。Claude Code 讀寫該層時會自動載入（`.claude/rules`），Codex 第一次修改時由 hook 附上；其他工具請自行讀取。

## 永遠適用（只有這三條）

1. 程式會在編輯後、結束前、commit 時被檢查。違規時**修改已經寫入檔案**，必須修正。訊息中有規則編號，依前綴讀 `AIR/rules/` 對應的規則：`CTR`→contracts、`STEP`→steps、`FLOW`→flows、`ADP`→adapters、`IO`→storage、`MAIN`→main、`TEST`→tests。檢查只涵蓋部分規則，**沒報錯不代表合規**；其餘規則在每批 commit 前照 skill `air-review` 審查。
2. 函式長度、複雜度的提醒（AIR101、AIR102）只是參考，不要為了消除提醒而拆函式。
3. 新程式一律寫進對應的層。不確定該放哪一層、或規則之間互相衝突時，停下來問使用者，不要自行新增層、放寬白名單或修改 `AIR/`。

## 遷移中

`.air/project.toml` 的 `migration.phase` 不是 `done` 時正在遷移：只讀 `status` 指出的那份 `AIR/start/` 階段檔（skill `air-migrate`），一次只做一個階段，做完停下來等使用者核准。遷移暫停中（`status` 會顯示）就照一般開發，不要自己 `resume`。

## 指令

`python3 AIR/air.py check`（快速檢查層內）、`verify`（驗收：測試＋架構檢查，每批結束必須通過）、`review`（語意審查的範圍，做法見 skill `air-review`）、`status`、`doctor`。不要用 `--no-verify`、`-n`、`SKIP=` 或改 `core.hooksPath` 略過 commit 前的檢查。
