# 範例：自己寫模型、Dataset 與訓練迴圈（PyTorch）
> 範例中的 `<套件>` 是專案的套件名稱（`.air/project.toml` 的 package），`<套件目錄>` 是 package-dir。
> 跟 `detector.md` 的差別：這裡沒有現成的 `model.train()`，網路、資料管線、訓練迴圈都是專案自己寫的。
> 它們都依賴 torch，所以全部住在 adapters，用一個子套件依角色切檔（ADP-1）；同層可以互相 import，但不能循環（ADP-6）。

```
<套件目錄>/adapters/torch/        一個工具一個子套件
    __init__.py                   只匯出 train 與 TorchPredictor
    data.py                       Dataset、DataLoader、增強
    model.py                      nn.Module
    trainer.py                    訓練迴圈、checkpoint；由 main 直接呼叫（不需要 Protocol）
    predictor.py                  推論，實作 contracts/ports.py 的 Predictor
<套件目錄>/steps/metrics.py       numpy 版指標：純函式，測試不需要 GPU
<套件目錄>/flows/evaluate.py      呼叫注入的 Predictor，結果交給 steps
<套件目錄>/main/__main__.py       train()、evaluate() 各一個函式（MAIN-4）
```

## 1. 型別與介面

```python
# <套件目錄>/contracts/types.py
from dataclasses import dataclass


@dataclass(frozen=True)
class Prediction:
    index: int
    label: int
    score: float


@dataclass(frozen=True)
class TrainConfig:
    epochs: int
    batch_size: int
    learning_rate: float
    device: str          # "cuda" 或 "cpu"：設定值，由 main 從設定檔讀進來
```

```python
# <套件目錄>/contracts/ports.py
from typing import Protocol

import numpy as np

from <套件>.contracts.types import Prediction


class Predictor(Protocol):
    def predict_many(self, images: np.ndarray) -> list[Prediction]: ...   # 批次方法（CTR-4）
```

## 2. Adapter 子套件：<套件目錄>/adapters/torch/

```python
# data.py —— 只收記憶體中的陣列；讀檔是 storage 的事，由 main 讀好再傳進來
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset


class ArrayDataset(Dataset):
    def __init__(self, images: np.ndarray, labels: np.ndarray | None = None):
        self._x = torch.as_tensor(images, dtype=torch.float32)
        self._y = None if labels is None else torch.as_tensor(labels, dtype=torch.long)

    def __len__(self) -> int:
        return len(self._x)

    def __getitem__(self, i: int):
        return self._x[i] if self._y is None else (self._x[i], self._y[i])


def loader(images: np.ndarray, labels: np.ndarray | None, batch_size: int, shuffle: bool, seed: int) -> DataLoader:
    gen = torch.Generator().manual_seed(seed)          # 亂數由參數傳入的 seed 決定，不用全域狀態
    return DataLoader(ArrayDataset(images, labels), batch_size=batch_size, shuffle=shuffle, generator=gen)
```

```python
# model.py
import torch
from torch import nn


class Net(nn.Module):
    def __init__(self, n_classes: int):
        super().__init__()
        self.body = nn.Sequential(nn.Flatten(), nn.Linear(28 * 28, 128), nn.ReLU(), nn.Linear(128, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)
```

```python
# trainer.py —— 整段訓練包成一個函式，由 main 直接呼叫（ADP-5）；路徑、seed、裝置都由參數傳入（ADP-3）
import numpy as np
import torch
from torch import nn

from <套件>.contracts.types import TrainConfig
from .data import loader
from .model import Net


def train(images: np.ndarray, labels: np.ndarray, n_classes: int, cfg: TrainConfig, out_path: str, seed: int) -> str:
    torch.manual_seed(seed)
    model = Net(n_classes).to(cfg.device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)
    loss_fn = nn.CrossEntropyLoss()
    for _ in range(cfg.epochs):
        for x, y in loader(images, labels, cfg.batch_size, shuffle=True, seed=seed):
            opt.zero_grad()
            loss_fn(model(x.to(cfg.device)), y.to(cfg.device)).backward()
            opt.step()
    torch.save(model.state_dict(), out_path)      # 寫到由參數指定的路徑，不自動存檔到別處
    return out_path                               # 回傳基本型別，不回傳 torch 物件（ADP-2）
```

```python
# predictor.py —— 實作 Predictor Protocol，提供給 flows 使用
import numpy as np
import torch

from <套件>.contracts.types import Prediction
from .data import loader
from .model import Net


class TorchPredictor:
    def __init__(self, weights_path: str, n_classes: int, device: str, batch_size: int = 256):
        self._model = Net(n_classes).to(device)
        self._model.load_state_dict(torch.load(weights_path, map_location=device))
        self._model.eval()
        self._device, self._batch = device, batch_size

    def predict_many(self, images: np.ndarray) -> list[Prediction]:
        out: list[Prediction] = []
        with torch.no_grad():
            for x in loader(images, None, self._batch, shuffle=False, seed=0):
                probs = torch.softmax(self._model(x.to(self._device)), dim=1).cpu()
                scores, labels = probs.max(dim=1)
                out += [Prediction(len(out) + i, int(l), float(s)) for i, (l, s) in enumerate(zip(labels, scores))]
        return out
```

```python
# __init__.py —— 對外只露出兩個名字；子套件內部的 import 都是單向的：trainer、predictor → data、model
from .predictor import TorchPredictor
from .trainer import train

__all__ = ["TorchPredictor", "train"]
```

## 3. 步驟與流程

```python
# <套件目錄>/steps/metrics.py —— numpy 版指標：純函式，測試不需要模型或 GPU
import numpy as np

from <套件>.contracts.types import Prediction


def accuracy(preds: list[Prediction], labels: np.ndarray) -> float:
    return float(np.mean([p.label == int(labels[p.index]) for p in preds])) if preds else 0.0

# <套件目錄>/flows/evaluate.py
import numpy as np

from <套件>.contracts.ports import Predictor
from <套件>.steps.metrics import accuracy


def run(images: np.ndarray, labels: np.ndarray, predictor: Predictor) -> float:
    return accuracy(predictor.predict_many(images), labels)
```

## 4. 組裝：<套件目錄>/main/__main__.py

```python
from <套件>.adapters.torch import TorchPredictor, train
from <套件>.contracts.types import TrainConfig
from <套件>.flows import evaluate
from <套件>.storage.catalog import load_catalog


def train_model(seed: int) -> None:                       # 入口依任務命名（MAIN-4）
    catalog = load_catalog("conf/catalog.yml")
    cfg = TrainConfig(**catalog.params("train"))
    images, labels = catalog.load("train_images"), catalog.load("train_labels")   # storage 讀成 ndarray
    train(images, labels, catalog.param("n_classes"), cfg, catalog.path("weights"), seed)


def evaluate_model() -> None:
    catalog = load_catalog("conf/catalog.yml")
    predictor = TorchPredictor(catalog.path("weights"), catalog.param("n_classes"), catalog.param("device"))
    catalog.save("accuracy", evaluate.run(catalog.load("test_images"), catalog.load("test_labels"), predictor))
```

## 5. 測試

```python
# tests/steps/test_metrics.py —— 直接給資料（TEST-1）
def test_accuracy():
    preds = [Prediction(0, 1, 0.9), Prediction(1, 0, 0.8)]
    assert accuracy(preds, np.array([1, 1])) == 0.5

# tests/flows/test_evaluate.py —— Fake 取代真實模型（TEST-2）
class FakePredictor:
    def predict_many(self, images):
        return [Prediction(i, 1, 0.9) for i in range(len(images))]

# tests/adapters/torch/test_trainer.py —— 真模型：慢速測試（TEST-3）
@pytest.mark.slow
def test_train_writes_weights(tmp_path):
    out = train(np.zeros((8, 28, 28), "float32"), np.zeros(8, "int64"), 10, TrainConfig(1, 4, 1e-3, "cpu"), str(tmp_path / "w.pt"), seed=0)
    assert Path(out).exists()
```

## 資料放不進記憶體時

Dataset 改成只拿路徑清單（字串），逐筆讀取交給一個 `contracts/ports.py` 的 Protocol（例如 `ImageSource.load(path) -> np.ndarray`），由 storage 實作、main 注入給 adapter；adapters 不能 import storage（ADP-6）。
