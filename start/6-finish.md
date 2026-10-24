# 階段 6：收尾

（共通原則在 `AIR/START.md`；這份只放這個階段的做法，其他階段不用讀。）

1. 移除階段 5 留下的轉接，把呼叫端改成直接 import 新的層。
2. 清空 `migration.allow-legacy`。
3. 檢查剩下的舊程式碼：層外只能剩空的 `__init__.py`（套件標記）。有自己程式的模組與轉接檔都要搬進層或移除；刻意留在外面的（不打算遷移的腳本、給外掛用的舊 import 路徑）要經使用者同意加進 `migration.exclude`，原因寫進報告。`status` 與 `verify --scope final` 會把層外的檔案分成「有自己的程式」「轉接檔」「套件標記」三類列出。
4. `python3 AIR/air.py verify --scope final` 必須通過：跑完整套件（有設 `[tests.final]` 就用它）、黃金重播與架構檢查，還會確認 `allow-legacy` 已清空，而且層外沒有有自己程式的模組或轉接檔（`legacy-remaining` 是必要項，不通過就不能 `phase done`；在 `exclude` 裡的不算）。再照 skill `air-review` 把整個遷移的變更審一次（`python3 AIR/air.py review --bundle --base <遷移開始前的 commit 或分支>`）。
5. 在遷移報告寫下總結，然後 `python3 AIR/air.py phase done`（需要第 4 步的驗收紀錄，而且之後沒有再改程式或設定；改了就重跑第 4 步），commit：`AIR 遷移完成`。

**停下來**，向使用者報告遷移完成。
