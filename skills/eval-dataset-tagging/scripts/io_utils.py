"""多格式数据读写：jsonl / csv / xlsx / Lumi Dataset"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)


# ── 读取 ──

def read_data(file_path: str) -> List[Dict[str, Any]]:
    """自动识别格式并读取数据"""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _read_jsonl(path)
    elif suffix == ".csv":
        return _read_csv(path)
    elif suffix in (".xlsx", ".xls"):
        return _read_excel(path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}，仅支持 jsonl/csv/xlsx")


def read_from_lumi(dataset_names: List[str], lumi_client, max_total: int = 2000) -> List[Dict[str, Any]]:
    """从 Lumi 平台拉取 Dataset Items"""
    all_records = []
    for ds_name in dataset_names:
        items = lumi_client.fetch_dataset_items(ds_name, max_total=max_total)
        records = lumi_client.dataset_items_to_records(items)
        logger.info(f"📚 Dataset [{ds_name}]: {len(records)} 条")
        all_records.extend(records)
    return all_records


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    # 尝试多种编码
    for enc in ["utf-8", "utf-8-sig", "gbk", "gb18030"]:
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc).fillna("")
            # 跳过描述行（第二行是字段说明而非数据）
            if len(df) > 0 and _is_description_row(df.iloc[0]):
                df = df.iloc[1:].reset_index(drop=True)
            return df.to_dict(orient="records")
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法读取CSV: {path}")


def _is_description_row(row) -> bool:
    """检测是否为描述行（通常以'样本所属池'、'标准化'等开头）"""
    vals = [str(v) for v in row.values if str(v).strip()]
    if not vals:
        return False
    first = vals[0]
    return any(kw in first for kw in ["样本所属池", "标准化", "【打标】", "回答唯一ID", "题目唯一ID"])


def _read_excel(path: Path) -> List[Dict[str, Any]]:
    df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    # 跳过描述行（和 CSV 保持一致）
    if len(df) > 0 and _is_description_row(df.iloc[0]):
        df = df.iloc[1:].reset_index(drop=True)
    return df.to_dict(orient="records")


# ── 写入 ──

def write_data(records: List[Dict[str, Any]], output_path: str, fmt: str = "jsonl"):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        _write_jsonl(records, path)
    elif fmt == "csv":
        _write_csv(records, path)
    elif fmt in ("xlsx", "xls"):
        _write_excel(records, path)
    else:
        raise ValueError(f"不支持的输出格式: {fmt}")


def flatten_for_tabular(record: Dict[str, Any]) -> Dict[str, Any]:
    """展平嵌套打标结果为 csv/xlsx 友好格式"""
    flat = {}
    for k, v in record.items():
        if k == "scene_labels" and isinstance(v, dict):
            for dim_name, dim_val in v.items():
                if isinstance(dim_val, dict):
                    for level, label in dim_val.items():
                        flat[f"scene_{dim_name}_{level}"] = label
                else:
                    flat[f"scene_{dim_name}"] = dim_val
        elif k == "error_labels" and isinstance(v, list):
            flat["error_attribution"] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, (dict, list)):
            flat[k] = json.dumps(v, ensure_ascii=False)
        else:
            flat[k] = v
    return flat


def _write_jsonl(records: List[Dict], path: Path):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_csv(records: List[Dict], path: Path):
    pd.DataFrame([flatten_for_tabular(r) for r in records]).to_csv(path, index=False, encoding="utf-8-sig")


def _write_excel(records: List[Dict], path: Path):
    # 确保后缀为 .xlsx
    if path.suffix.lower() not in (".xlsx", ".xls"):
        path = path.with_suffix(".xlsx")
    pd.DataFrame([flatten_for_tabular(r) for r in records]).to_excel(path, index=False, engine="openpyxl")
