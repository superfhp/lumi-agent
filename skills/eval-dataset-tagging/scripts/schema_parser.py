"""配置解析器：支持 yaml / json / md 格式的打标配置解析"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


def parse_config(source: str) -> Dict[str, Any]:
    """解析配置，支持文件路径(yaml/json/md)或内联字符串"""
    path = Path(source)
    if path.exists() and path.is_file():
        return _parse_file(path)
    try:
        return json.loads(source)
    except json.JSONDecodeError:
        pass
    try:
        result = yaml.safe_load(source)
        if isinstance(result, dict):
            return result
    except yaml.YAMLError:
        pass
    raise ValueError(f"无法解析配置: {source[:200]}")


def _parse_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    elif suffix == ".json":
        return json.loads(text)
    elif suffix == ".md":
        return _extract_from_markdown(text)
    else:
        raise ValueError(f"不支持的配置文件格式: {suffix}")


def _extract_from_markdown(text: str) -> Dict[str, Any]:
    for lang in ("yaml", "yml", "json"):
        marker = f"```{lang}"
        if marker in text:
            block = text.split(marker)[1].split("```")[0]
            if lang == "json":
                return json.loads(block)
            return yaml.safe_load(block)
    raise ValueError("Markdown中未找到yaml/json代码块")


def build_scene_tree_text(scene_schema: List[Dict]) -> str:
    """将 scene_schema 树形结构转为 prompt 可读文本"""
    lines = []
    for dim in scene_schema:
        lines.append(f"\n## 维度: {dim['dimension']}")
        for node in dim.get("tree", []):
            _walk_tree(node, lines, depth=0)
    return "\n".join(lines)


def _walk_tree(node: Dict, lines: List[str], depth: int):
    for level_key in ("l1", "l2", "l3"):
        if level_key in node:
            indent = "  " * depth
            lines.append(f"{indent}- {level_key}: {node[level_key]}")
            break
    for child in node.get("children", []):
        _walk_tree(child, lines, depth + 1)


def build_error_schema_text(error_schema: List[Dict], severity_schema: Optional[Dict] = None) -> str:
    """将 error_schema 转为 prompt 可读文本"""
    lines = ["## 错误分类清单"]
    for item in error_schema:
        lines.append(f"- **{item['name']}**: {item.get('description', '')}")
    if severity_schema:
        lines.append("\n## 严重程度量表")
        for val in severity_schema.get("scale", []):
            label = severity_schema.get("labels", {}).get(val, severity_schema.get("labels", {}).get(str(val), ""))
            lines.append(f"- {val}: {label}")
    return "\n".join(lines)
