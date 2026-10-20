# flows/ 規則

這裡只描述「哪個步驟的輸出接到哪個步驟的輸入」，以及什麼時候呼叫注入的外部工具。

- **FLOW-1【必須】** 外部只能 import 白名單內的模組（同 STEP-1）；內部只能 import steps、flows（子流程）、contracts（含 ports）。不能 import storage、adapters、main。外部工具透過參數接收 `contracts/ports.py` 定義的 Protocol。〔hook 檢查〕
- **FLOW-2【必須】** 自己不做 I/O（規則同 STEP-2）；需要外部資料或工具時，呼叫注入的 Protocol。〔hook 部分檢查〕
- **FLOW-3【必須】** 不寫計算或資料轉換。一旦出現運算、篩選、迴圈裡的邏輯，就移到 steps；需要逐筆呼叫工具時，讓 Protocol 提供批次方法。
- **FLOW-4【建議】** 一個流程一個檔案，入口函式命名為 `run`。可以有條件分支，但分支裡只呼叫步驟、子流程或注入的工具。
- **FLOW-5【必須】** 可以呼叫其他 flow 當子流程，但不能循環（A 呼叫 B 時，B 就不能直接或間接呼叫 A）。〔hook 檢查〕

## 範例

```python
from <套件>.contracts.ports import RateProvider
from <套件>.steps.orders import build_report, convert_amounts, valid_orders


def run(orders: pd.DataFrame, fx: RateProvider, today: date) -> Report:
    valid = valid_orders(orders)              # step
    rates = fx.rates_for(today)               # 注入的 Protocol，實作在 adapters
    return build_report(convert_amounts(valid, rates))   # step：只收資料
```
