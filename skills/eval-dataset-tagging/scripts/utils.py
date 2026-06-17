"""工具函数"""

import datetime
from typing import Dict, Any, List


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_badcase_filter(records: List[Dict[str, Any]], filter_expr: str) -> tuple:
    """根据过滤表达式筛选badcase

    返回 (badcases, skipped) 两个列表
    支持简单的 field == value / field < value 等表达式
    """
    if not filter_expr or not filter_expr.strip():
        return records, []

    badcases = []
    skipped = []
    for r in records:
        if _eval_filter(r, filter_expr):
            badcases.append(r)
        else:
            skipped.append(r)
    return badcases, skipped


def _eval_filter(record: Dict, expr: str) -> bool:
    """安全地评估简单过滤表达式"""
    expr = expr.strip()
    for op in ("==", "!=", "<=", ">=", "<", ">"):
        if op in expr:
            parts = expr.split(op, 1)
            if len(parts) == 2:
                field = parts[0].strip()
                value = parts[1].strip().strip('"').strip("'")
                actual = str(record.get(field, ""))
                try:
                    actual_num = float(actual)
                    value_num = float(value)
                    if op == "==":
                        return actual_num == value_num
                    elif op == "!=":
                        return actual_num != value_num
                    elif op == "<=":
                        return actual_num <= value_num
                    elif op == ">=":
                        return actual_num >= value_num
                    elif op == "<":
                        return actual_num < value_num
                    elif op == ">":
                        return actual_num > value_num
                except (ValueError, TypeError):
                    pass
                if op == "==":
                    return actual == value
                elif op == "!=":
                    return actual != value
    return False
