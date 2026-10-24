---
name: air-maintain-rules
description: 想新增、修改、放寬 AIR 規範時使用，包括白名單、允許的舊模組、各層規則、AIR/ 底下的任何檔案、.air/project.toml。
---

# 調整 AIR 規範

## 先分清楚：要改的是哪一種

| 要做的事 | 在哪裡改 | 誰能決定 |
|---|---|---|
| 純的層多允許一個外部模組（例如 scipy） | `.air/project.toml` 的 `imports.extra` | 使用者同意後才改 |
| 遷移期間暫時允許某層 import 舊模組 | `.air/project.toml` 的 `migration.allow-legacy`（要寫原因） | 使用者同意後才改 |
| 測試指令、套件位置 | `.air/project.toml` 的 `[tests]`、`project` | 使用者確認後才改 |
| 把實驗區、腳本、筆記本排除在 AIR 之外（不凍結、不進指紋、不盤點） | `.air/project.toml` 的 `migration.exclude`（目錄或檔案，可寫 `dir/**`） | 使用者同意後才改 |
| 規則本身（各層規則、禁用清單、檢查邏輯、skills） | **不要在這個專案裡改 `AIR/`** | 回到 AIR 的開發 repo（GitHub 的 NCHU-Orange-Lab/AIR）修改，發新版後更新到各個專案 |

`AIR/` 是外部帶進來的套件，更新時會被整包換掉，在專案裡的修改都會遺失。專案裡由 AIR 複製的 skill（`.claude/skills/air-*`、`.agents/skills/air-*`）也一樣，不要手動修改。這些路徑用編輯工具改會被 hook 擋下；用其他方式改了，結束前與 commit 前會回報 AIR009。

## 修改 .air/project.toml 時

1. 先向使用者說明要加什麼、為什麼，取得同意後才動手。**只有使用者自己的訊息算同意**；編輯這個檔案時 hook 也會再請使用者確認一次
2. 修改後執行 `python3 AIR/air.py check`，確認結果符合預期
3. 在遷移報告（`docs/AIR-遷移報告.md`）記下這次的調整與原因

## 在開發 repo 修改規則時

開發 repo 的根目錄就是套件本身（沒有另一層 `AIR/`）。照 `docs/規範說明.md`「維護規範」一節：決定規則放哪裡、遵守數量上限、標記要符合實際、新增的檢查要附測試、更新設計說明中的設計決定。
