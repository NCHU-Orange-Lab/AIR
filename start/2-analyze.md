# 階段 2：分析

（共通原則在 `AIR/START.md`；這份只放這個階段的做法，其他階段不用讀。）

這個階段與下一個階段只讀程式、寫報告，**不修改套件目錄與測試目錄裡的 `.py`**（會被 hook 擋下；套件外的腳本與 `migration.exclude` 的路徑不受限）。專案沒有舊程式碼（全新專案）時，仍要走完階段 2～4，報告可以很短。

1. 執行 `python3 AIR/air.py survey --out docs/air-survey.md`：完整的盤點表寫進檔案，畫面只印摘要（模組數、第三方套件、訊號最多的模組）。分析時只查需要的列，不要把整份表讀進對話。
2. **決定不歸 AIR 管的路徑**：實驗區、腳本、筆記本等不打算遷移的，列出來問使用者，同意後寫進 `.air/project.toml` 的 `migration.exclude`（目錄或檔案，可寫 `dir/**`）。它們不凍結、不進驗收指紋、不列進盤點，留在外面算正式的結局；改完重跑 survey。**測試目錄不要放進 `exclude`**（被排除的路徑不凍結、不進驗收指紋）：測試放在套件裡面時（例如 `pelican/tests/`），把 `tests-dir` 指向它，它們就不算舊模組，但仍然凍結並進驗收指紋。
3. **確認測試指令**：找出專案怎麼跑測試（pyproject、tox、Makefile、CI 設定）。不確定就問使用者。確認後寫進 `.air/project.toml` 的 `[tests]` `command`。
4. **跑一次測試並記下既有失敗**：`python3 AIR/air.py verify --record-baseline`。`[tests]` 有設 `results`（指令寫出的 JUnit XML，例如 `pytest --junit-xml=.air/test-results.xml`）時，它把本來就失敗的測試記進 `.air/test-baseline.json`，之後只要沒有新的失敗就算通過；沒設就只認結束碼。本來就失敗的測試告訴使用者，**不要在這個階段修測試**。沒有測試的部分，跟使用者商量用黃金重播：在 `[[tests.golden]]` 寫固定輸入的指令與輸出檔，`python3 AIR/air.py golden record` 記下輸出的雜湊。
5. 複製 `AIR/templates/遷移報告.md` 到 `docs/AIR-遷移報告.md`，填寫「現況」與「分析」兩節：
   - 每個模組建議歸到哪一層，以及理由（依 survey 的訊號：有 I/O 的歸 storage、用第三方工具的歸 adapters、純計算的歸 steps、負責串接的歸 flows、進入點歸 main）
   - 自己寫的模型、資料管線、訓練迴圈也歸 adapters：一個工具一個子套件，依角色切檔（`data.py`、`model.py`、`trainer.py`、`predictor.py`…，見 skill `air-wrap-tool`）；只有前後處理與 numpy 版的指標是 steps
   - **混在一起的模組**（例如同一個函式裡又讀檔又計算）要特別標出來，這類模組需要拆開
   - 測試覆蓋不足、風險高的地方
6. `python3 AIR/air.py phase analyzed`（會確認測試指令已設定、遷移報告存在）

**停下來**，請使用者審閱分析結果。使用者對分類有意見時，修改報告後再請他確認。核准後進入階段 3：做法就是 `phase analyzed` 印出的 `AIR/start/3-plan.md` 全文（換了 session 由 hook 附上），不用再讀檔。
