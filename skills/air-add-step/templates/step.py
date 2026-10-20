"""範本：步驟是純函式，只接收資料、回傳資料。"""

import pandas as pd


def valid_orders(orders: pd.DataFrame, min_amount: float = 0) -> pd.DataFrame:
    """金額大於 min_amount 的訂單。"""
    return orders.loc[orders["amount"] > min_amount].copy()


def order_total(valid: pd.DataFrame) -> float:
    return float(valid["amount"].sum())


# 對應測試：tests/steps/test_orders.py
#
# def test_valid_orders_drops_non_positive():
#     orders = pd.DataFrame({"amount": [10, 0, -5]})
#     assert valid_orders(orders)["amount"].tolist() == [10]
