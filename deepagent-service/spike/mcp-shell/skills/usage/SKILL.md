---
name: sales-mock-usage
description: sales-mock connector 的工具說明——列區域、列訂單、瑕疵統計、庫存水位、出貨統計。
---

# sales-mock 使用說明

THROWAWAY spike fixture connector,五個工具:

- `list_regions()` -- 無參數,回傳 `[{region, display_name}, ...]` 4 筆區域。
- `list_orders(regions, days)` -- `regions` 是 `list_regions()` 回傳的 `region` id 組成的陣列
  (省略或空即不篩區域);`days` 是回溯天數(預設 30)。回傳訂單明細列陣列,每列
  `{order_id, order_date, region, product, quantity, unit_price, amount, status}`。
- `defect_summary(days)` -- `days` 回溯天數(預設 30)。回傳 5 筆
  `{defect_type, count, rate}`。
- `inventory_levels(warehouse)` -- `warehouse` 可選,合法值 `wh-north`/`wh-south`/`wh-central`,
  省略即全部;不合法的值會回錯誤。回傳信封物件 `{status, errorCode, data: [...]}`,列在 `data`
  裡,每列 `{warehouse, product, on_hand, reorder_point}`。
- `shipment_summary(days)` -- `days` 回溯天數(預設 30)。回傳兩層信封
  `{result: {data: [...], total, days}}`,列在 `result.data` 裡,每列
  `{ship_date, carrier, shipments, on_time_rate}`。

`list_regions` 的 `region` 值就是 `list_orders.regions` 要傳入的值——先呼叫
`list_regions` 取得合法值清單,再用來過濾 `list_orders`。
