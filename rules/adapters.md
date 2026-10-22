# adapters/ 規則

這裡是第三方工具與外部服務的轉接層，也是唯一可以 import 它們的地方。新增 adapter 時使用 skill `air-wrap-tool`。

- **ADP-1【必須】** 一個第三方工具一個檔案，檔名用工具名稱（`yolo.py`、`s3.py`）；自己寫模型、資料管線或訓練迴圈時改用子套件 `adapters/<工具>/`，依角色切檔：`data.py`、`model.py`、`loss.py`、`metrics.py`、`trainer.py`、`predictor.py`，只建需要的。
- **ADP-2【必須】** 提供給 flows 使用的功能，必須實作 `contracts/ports.py` 中定義的 Protocol；只由 main 直接呼叫的功能（例如訓練）不需要。無論是哪一種，回傳值都用 `contracts/types.py` 的型別、基本型別或白名單內資料函式庫的型別（pandas、numpy，以及專案加進 `imports.extra` 的），其他第三方物件不能流出這一層。
- **ADP-3【必須】** 關閉副作用：自動存檔、自動下載、冗長的 log、修改全域設定。工具需要路徑時（載入權重、訓練輸出），由參數傳入，不寫死。
- **ADP-4【必須】** 只包裝實際用到的功能，不要把整個第三方 API 照搬一遍。
- **ADP-5【建議】** 工具內建的完整流程（例如 `model.train()`）整段包成一個函式，不拆開。
- **ADP-6【必須】** 內部只能 import contracts 與同層的 adapters（同層不能循環 import）；不能 import steps、flows、storage、main。〔hook 檢查〕
