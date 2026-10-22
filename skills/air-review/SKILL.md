---
name: air-review
description: 每批遷移 commit 前，或使用者要求依 AIR 規則審查變更時使用；補上程式檢查不到的規則（flow 不寫計算、main 不寫業務邏輯、adapter 關閉副作用、不 mock 第三方工具……）。唯讀，不改檔。
---

# 依 AIR 規則審查變更

`check` 只涵蓋能用 AST 判斷的規則（import、I/O、時間、亂數）；其餘規則靠這個審查。審查是**唯讀**的：不改檔、不 commit、不 push。發現的問題交回給改程式的一方處理。可以的話交給沒參與實作、只看 diff 的子 agent 做（Claude Code 的 subagent；Codex 明確呼叫 `$air-review`），降低自己審自己的偏誤。

## 步驟

1. `python3 AIR/air.py check` 先通過。它負責的規則（有〔hook 檢查〕標記）這裡不重複報，交回時會被拒絕。
2. `python3 AIR/air.py review`：相對 HEAD、含未 commit 的修改；審整個分支加 `--base main`。它列出改了哪些層內的 `.py`、每個檔案改到的行、要對照的規則與出處（`AIR/rules/<層>.md:行`）。沒列出的檔案不審。
3. 讀差異（用它印出的 `git diff` 指令；新檔案整個讀），對每個檔案逐條對照列出的規則。規則帶著「只審程式抓不到的部分」時，就專看那個缺口。
4. 什麼該報：
   - **這次變更引入的**。既有問題不報；行號要落在改到的行附近，否則會被拒絕。
   - **具體可修**，指得出檔案與行。「可能影響別處」要指出具體位置才算。
   - **有把握**：信心 0–1，不到 0.5 的不報；拿不準就不報，沒有就交 `findings: []`，不要硬湊。
   - 只依列出的規則判斷，不加規則以外的偏好。
5. 交回結果（格式如下）。無效會被拒絕並列出原因，修正後再交；通過驗證才會記錄。
   ```sh
   python3 AIR/air.py review --result - <<'EOF'
   { ... }
   EOF
   ```
6. `priority` 0、1 的發現必須處理，或向使用者說明為什麼不處理。處理後內容變了，紀錄就過期（`status` 會顯示），要重新審查。審查結果不能推翻 `check` 或 `verify` 的失敗。

## 結果格式

```json
{
  "findings": [
    {
      "rule": "FLOW-3",
      "title": "把篩選邏輯移到 steps",
      "body": "run() 第 12–15 行在迴圈裡依金額過濾訂單，這是計算；移到 steps 的 valid_orders，flow 只呼叫它。",
      "confidence": 0.8,
      "priority": 1,
      "path": "src/shop/flows/report.py",
      "lines": {"start": 12, "end": 15}
    }
  ],
  "overall": "fail",
  "explanation": "一個 FLOW-3 的問題；其餘對照過的規則都符合。"
}
```

- `priority`：0 ＝違反【必須】而且讓層的保證失效（step 接收工具物件、白名單外的第三方物件流出 adapters、為了讓測試通過改了預期結果）；1 ＝其他違反【必須】；2 ＝違反【建議】；3 ＝提醒。
- `overall`：有 priority 0 或 1 的發現就是 `fail`，否則 `pass`。
- 每個發現引用一條規則，`path` 用 `review` 列出的寫法。規則的出處由程式補上，不用自己數行號。
- 機器可讀的格式在這個資料夾的 `schema.json`（非互動模式、CI 可以用它規定輸出）。
