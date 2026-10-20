---
name: air-wrap-tool
description: 需要使用第三方工具或外部服務（YOLO 等模型、OCR、雲端儲存、外部 API、requests 等），或檢查回報第三方工具出現在 steps、flows 時使用。
---

# 包裝第三方工具

原則：不修改第三方工具，也不讓整個專案遷就它，而是在 adapters 包一層轉接。

## 開始前

這個任務會改到多層，**改到哪一層就讀哪一層的 `CLAUDE.md`**：adapters（必讀）、contracts（定義介面時）、flows（注入時）、main（組裝時）。

## 步驟

1. **定義介面**：在 `contracts/ports.py` 定義 Protocol，在 `contracts/types.py` 定義回傳型別。只寫實際會用到的方法；flow 需要處理多筆資料時，提供批次方法（例如 `detect_many`），避免在 flow 裡寫迴圈。
2. **實作 adapter**：建立 `adapters/<工具名>.py`
   - 關閉副作用（自動存檔、自動下載、log）
   - 推論、轉換時傳入記憶體中的資料（陣列、DataFrame）
   - 工具本身需要路徑時（權重、訓練輸出），路徑由參數傳入，來源是設定檔
   - 把工具的回傳物件轉成第 1 步定義的型別
3. **不要改白名單**：純的層採用白名單，第三方工具本來就無法在那裡 import。不要把工具加進 `.air/project.toml` 的 imports.extra 來讓檢查放行。
4. **注入**：flow 透過參數接收 Protocol 並呼叫它，再把結果（資料）傳給 steps。steps 不接收 Protocol。由 main 建立 adapter，再傳給 flow。
5. **測試**：
   - flow 的單元測試用 Fake（實作同一個 Protocol），不要 mock 第三方工具本身
   - 另外寫一個使用真實工具的整合測試，標記為慢速測試

## 工具內建完整流程時

例如 `model.train()` 本身就是一整條流程：整段包成 adapter 裡的一個函式，由 main 直接呼叫，不需要 Protocol，也不要拆成 steps。前處理和後處理才寫成 steps。

## 不必包的情況

一次性的實驗腳本或 notebook 可以直接呼叫工具。要放進套件裡時才需要包。

## 完整範例

需要看具體程式碼時，才讀 `examples/detector.md`（以物件偵測模型為例）。
