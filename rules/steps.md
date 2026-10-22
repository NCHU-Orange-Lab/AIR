# steps/ 規則

這裡放純函式：只接收資料、回傳資料。同樣的輸入永遠得到同樣的輸出。

- **STEP-1【必須】** 只能 import 白名單內的外部模組（標準庫的純計算模組、pandas、numpy，以及專案在 `.air/project.toml` 加入的模組），內部只能 import steps 與 `contracts.types`。〔hook 檢查〕
- **STEP-2【必須】** 不做 I/O：不讀寫檔案（包括 `df.to_csv()`、`path.write_text()` 這類方法）、不呼叫網路、不 `print`、不寫 log。不接收 Protocol（無法 import `contracts.ports`）。〔hook 部分檢查：沒有型別標註、直接當參數傳進來的工具物件抓不到〕
- **STEP-3【必須】** 不直接讀時間、亂數、環境變數，需要時由參數傳入。`df.sample()` 必須傳入 `random_state`，`default_rng()` 必須傳入 seed。〔hook 部分檢查：其他函式庫自己的取時間、取亂數方法抓不到〕
- **STEP-4【必須】** 參數與回傳值都要有型別標註；跨層傳遞的資料型別定義在 `contracts/types.py`，不回傳白名單外的第三方物件（白名單同 STEP-1）。
- **STEP-5【建議】** 不修改傳入的參數，回傳新物件（DataFrame 用 `.copy()`，或直接回傳新的）。
- **STEP-6【建議】** 用結果命名（`valid_orders`），不用動作流程命名（`process_data`）。

## 違反時怎麼修

- STEP-1、STEP-2：讀寫移到 storage，第三方呼叫移到 adapters，結果用參數傳進來。細節見 skill `air-wrap-tool`。
- STEP-1 擋到的是純計算函式庫（例如 scipy）：不要自行加進白名單，先問使用者。
- STEP-2 需要外部工具的結果：在 flow 裡呼叫注入的工具，再把結果（資料）傳給 step。
- STEP-3：把目前時間、seed 加成函式參數，由 main 產生；設定值由 storage 讀取後傳入。
