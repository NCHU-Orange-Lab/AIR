# 範例：包裝物件偵測模型（YOLO）
> 範例中的 `<套件>` 是專案的套件名稱（`.air/project.toml` 的 package），`<套件目錄>` 是 package-dir。

## 1. 型別與介面

```python
# <套件目錄>/contracts/types.py —— 資料型別，所有層都能用
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    image_index: int
    label: str
    score: float
    box: tuple[float, float, float, float]
```

```python
# <套件目錄>/contracts/ports.py —— Protocol，steps 不能 import
from typing import Protocol

import numpy as np

from <套件>.contracts.types import Detection


class Detector(Protocol):
    # 批次方法：flow 呼叫一次就好，不必在 flow 裡寫迴圈（FLOW-3）
    def detect_many(self, images: list[np.ndarray]) -> list[Detection]: ...
```

## 2. Adapter：<套件目錄>/adapters/yolo.py

工具自帶完整的訓練與推論流程，一個檔案就夠（ADP-1）。自己寫模型、Dataset、訓練迴圈時改用子套件，見 `trainer.md`。

```python
import numpy as np
from ultralytics import YOLO

from <套件>.contracts.types import Detection


class YoloDetector:
    """實作 Detector Protocol，提供給 flows 使用。"""

    def __init__(self, weights_path: str):          # 路徑由 main 從設定檔取得後傳入
        self._model = YOLO(weights_path)

    def detect_many(self, images: list[np.ndarray]) -> list[Detection]:
        results = self._model.predict(images, save=False, verbose=False)   # 傳記憶體資料
        return [
            Detection(i, r.names[int(c)], float(s), tuple(b))
            for i, r in enumerate(results)
            for b, s, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())
        ]


def train(base_weights: str, data_yaml: str, epochs: int, out_dir: str) -> str:
    """工具內建的完整訓練流程，整段包成一個函式，由 main/ 直接呼叫（不需要 Protocol）。
    所有路徑都由參數傳入；回傳最佳權重的路徑（基本型別，不回傳 YOLO 物件）。"""
    model = YOLO(base_weights)
    model.train(data=data_yaml, epochs=epochs, project=out_dir, verbose=False)
    return str(model.trainer.best)
```

## 3. 步驟與流程

```python
# <套件目錄>/steps/detections.py —— 只收資料，不收 Detector（也 import 不到 ports）
from collections import Counter

from <套件>.contracts.types import Detection


def keep_confident(dets: list[Detection], threshold: float) -> list[Detection]:
    return [d for d in dets if d.score >= threshold]

def count_by_label(dets: list[Detection]) -> dict[str, int]:
    return dict(Counter(d.label for d in dets))

# <套件目錄>/flows/inspect.py —— 呼叫注入的工具，把結果交給 steps
import numpy as np

from <套件>.contracts.ports import Detector
from <套件>.steps.detections import count_by_label, keep_confident


def run(images: list[np.ndarray], detector: Detector, threshold: float) -> dict[str, int]:
    detections = detector.detect_many(images)
    return count_by_label(keep_confident(detections, threshold))
```

## 4. 組裝：<套件目錄>/main/__main__.py

```python
catalog = load_catalog("conf/catalog.yml")
detector = YoloDetector(catalog.path("yolo_weights"))
result = inspect.run(catalog.load("images"), detector, catalog.param("threshold"))
catalog.save("label_counts", result)
```

## 5. 測試

```python
# tests/steps/test_detections.py —— steps 不用 Fake，直接給資料
def test_keep_confident():
    dets = [Detection(0, "person", 0.9, (0, 0, 1, 1)), Detection(0, "dog", 0.2, (0, 0, 1, 1))]
    assert [d.label for d in keep_confident(dets, 0.5)] == ["person"]

# tests/flows/test_inspect.py —— flow 用 Fake 取代真實模型
class FakeDetector:
    def detect_many(self, images):
        return [Detection(i, "person", 0.9, (0, 0, 1, 1)) for i in range(len(images))]
```
