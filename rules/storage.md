# storage/ 規則

這裡負責所有讀寫：檔案、資料庫、設定檔、環境變數。（規則前綴是 IO。）

- **IO-1【必須】** 路徑不寫死，一律從設定檔取得（例如 `conf/catalog.yml`，或專案原本的設定機制）。
- **IO-2【必須】** 不含業務邏輯，只做讀取、寫入與格式轉換（例如 bytes → ndarray、CSV → DataFrame）。
- **IO-3【建議】** 新增資料來源時，先在設定檔加上條目，再寫讀取函式。
- **IO-4【必須】** 內部只能 import contracts 與同層的 storage（同層不能循環 import）；不能 import steps、flows、adapters、main。〔hook 檢查〕
- **IO-5【建議】** 資料目錄依階段分開（`raw`、`interim`、`processed`；`raw` 只讀不改寫），模型權重、log、報表放在套件外的輸出目錄（例如 `models/`、`logs/`、`reports/`）；這些路徑都寫在設定檔，目錄本身不進版本控制。
