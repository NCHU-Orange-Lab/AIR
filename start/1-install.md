# 階段 1：安裝

（共通原則在 `AIR/START.md`；這份只放這個階段的做法，其他階段不用讀。）

1. **前置檢查**
   - 這是 Python 專案嗎？不是的話，停下來告訴使用者 AIR 目前只支援 Python。
   - `AIR/` 是否直接放在 git repo 的根目錄？
   - 工作樹不用乾淨：安裝程式最後會列出它建立或修改的路徑，commit 時只加那些。
2. **確認套件**：找出專案的主要套件（例如 `src/shop/` 或 `shop/`）。只有一個候選時直接用；有多個或找不到時，列出候選並問使用者。
3. **執行安裝**：`sh AIR/install.sh`（需要指定時：`sh AIR/install.sh --package shop --package-dir src/shop`；只用一種工具時加 `--host claude` 或 `--host codex`）
4. 向使用者報告安裝程式列出的接點，以及任何「略過」或「無法自動加入」的項目；用 Codex 時提醒使用者要在 Codex 裡信任 AIR 的 hook。`python3 AIR/air.py doctor` 可以確認接線。
5. 請使用者確認後 commit：`安裝 AIR 規範`（只 `git add` 安裝程式列出的路徑，不要 `git add -A`）。

**停下來**，等使用者核准後進入階段 2：讀 `AIR/start/2-analyze.md`。
