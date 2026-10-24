# 階段 3：計畫

（共通原則在 `AIR/START.md`；這份只放這個階段的做法，其他階段不用讀。）

1. 在遷移報告的「計畫」一節，把遷移切成數個批次。建議的順序：
   1. contracts：先定義跨層的資料型別
   2. storage、adapters：把 I/O 和第三方呼叫包起來
   3. steps：把純計算抽出來
   4. flows：把串接邏輯整理成流程
   5. main：進入點改成只做組裝
2. 每個批次寫清楚：要搬或拆的模組、要補的行為保護測試（或哪些由黃金重播涵蓋）、預計需要的暫時允許（allow-legacy）、驗收方式。
3. 每批控制在使用者能一次審閱完的大小，一批一個 commit。
4. `python3 AIR/air.py phase planned`

**停下來**，等使用者核准計畫。核准後進入階段 4：做法就是 `phase planned` 印出的 `AIR/start/4-layers.md` 全文（換了 session 由 hook 附上），不用再讀檔。
