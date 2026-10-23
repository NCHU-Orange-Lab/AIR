---
name: air-add-step
description: 在 steps 層新增或修改資料處理步驟，或把步驟串進 flows 時使用。
---

# 新增或修改步驟

## 開始前

改到哪一層，就照哪一層的規則（`AIR/rules/<層>.md`，讀寫該層時會自動附上）：一定會改 steps；串進流程時改 flows；需要新資料時改 storage；需要新的跨層型別時改 contracts。各層的位置在 `.air/project.toml` 的 package-dir 底下。

## 步驟

1. 確定輸入、輸出的型別。需要跨層傳遞的新型別，加到 `contracts/types.py`。
2. 參考 `templates/step.py` 寫函式。
3. 在測試目錄寫測試：直接給輸入、比對輸出，不用 mock。
4. 串進對應的 flow。需要新資料時，改設定檔與 storage，不要在 step 裡讀檔。
5. 執行專案的測試指令（`.air/project.toml` 的 test-command），再執行 `python3 AIR/air.py check`。

## 要不要獨立成一個函式

預設不為了變短而拆。需要判斷時，使用 skill `air-split-function`。

## 完成檢查

- [ ] 沒有 I/O、時間、亂數。檢查只能抓到一部分，例如沒有型別標註、直接傳進來的工具物件就抓不到，要自己對照 STEP-2、STEP-3
- [ ] step 只接收資料，沒有接收注入的工具（Protocol）
- [ ] 參數與回傳值都有型別標註
- [ ] 有對應的測試，而且通過
