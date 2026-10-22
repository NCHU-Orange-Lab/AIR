# contracts/ 規則

最底層：跨層傳遞的資料型別（`types.py`）與介面（`ports.py`）。所有層都依賴它，它不依賴任何層。

- **CTR-1【必須】** 只能 import 白名單內的外部模組與 contracts 自己，不能 import 其他層。〔hook 檢查〕
- **CTR-2【必須】** `types.py` 只放資料型別（dataclass、enum、型別別名），不放處理邏輯。
- **CTR-3【必須】** `ports.py` 只放 Protocol。steps 不能 import 它，只有 flows、adapters、storage、main 可以。〔hook 檢查〕
- **CTR-4【必須】** Protocol 的參數與回傳值只用 `types.py` 的型別、基本型別或白名單內資料函式庫的型別（pandas、numpy，以及專案在 `.air/project.toml` 的 `imports.extra` 加入的）；需要處理多筆資料時，提供批次方法（例如 `detect_many`）。
- **CTR-5【建議】** Protocol 依使用方（flows）的需求設計，不照第三方工具的 API 設計；只寫實際會用到的方法。
