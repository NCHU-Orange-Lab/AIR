# tests/ 規則

- **TEST-1【必須】** 測試 steps 時不用 mock，直接給輸入、比對輸出。
- **TEST-2【必須】** 不 mock 第三方工具；要替換的是自己定義的 Protocol（例如寫一個 `FakeDetector`）。
- **TEST-3【必須】** 用到真實模型、GPU、網路或大型檔案的測試，加上 `@pytest.mark.slow`（或專案原本的慢速測試標記）。
- **TEST-4【建議】** 測試檔的路徑對應原始檔：`<套件>/steps/orders.py` → `tests/steps/test_orders.py`。
- **TEST-5【必須】** 遷移期間，搬移程式之前先補上「行為保護測試」，鎖住現有的行為；搬移後測試必須照樣通過。不能為了讓測試通過而修改測試的預期結果。
