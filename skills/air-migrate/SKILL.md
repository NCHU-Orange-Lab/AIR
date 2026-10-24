---
name: air-migrate
description: 安裝 AIR、分析現有專案、規劃或執行分層遷移時使用；使用者提到「照 AIR/START.md」、「安裝 AIR」、「遷移」、「重構成 AIR 架構」時也使用。
---

# AIR 安裝與遷移

共通原則與階段目錄在 `AIR/START.md`，每個階段的做法是 `AIR/start/` 底下的一份檔案；**一次只做一個階段**。

1. 執行 `python3 AIR/air.py status`，看目前在哪個遷移階段、最近一次驗收的狀態
2. 只讀它「現在讀」那一行指出的檔案（session 開始時已附上的就不用再讀；其他階段不用讀）
3. 做完該階段後**停下來**，向使用者報告，等使用者核准後才進入下一階段

- 每一批遷移與收尾的驗證用 `python3 AIR/air.py verify`（收尾用 `--scope final`），只跑 `check` 不算。既有失敗以 `.air/test-baseline.json` 為準（分析階段 `verify --record-baseline` 記的）；黃金重播的輸出變了就是行為變了，重錄要使用者決定。
- 每一批 commit 前照 skill `air-review` 做語意審查，補上程式檢查不到的規則；priority 0、1 的發現要處理或向使用者說明。
- `python3 AIR/air.py phase <階段>` 被拒絕時，照它列出的原因處理；不要用 `--force`，除非使用者明確要求。
- 建立分層時專案已有同名模組（例如 `main.py`）：`layers` 會拒絕；是這一層就用 `layers --adopt-module <層>`，它列出的 allow-legacy 要經使用者同意才寫進 `.air/project.toml`。
- `status` 顯示「遷移暫停中」就照一般開發，不要自己 `resume`；要擱置遷移也是由使用者執行 `pause --reason`。
- 不論在哪個階段，都不要在同一個 commit 裡同時「改變行為」和「搬移程式」。
