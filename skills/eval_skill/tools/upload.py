"""上传本地文件到 eval_skill：prompt → 文件系统；dataset → Lumi。

设计动机
--------
评测人员的痛点：
1. 在 OpenWebUI 里用自然语言一句句调 prompt 太慢，不如直接把编辑器里写好的整段 prompt 上传；
2. 准备好的评测集（csv / jsonl）想直接拿来跑，不想手动逐条调 Langfuse 接口；
3. 整套领域评测包（如 RVEC：1 份 pack.yaml + 6 份 step prompt）需要一次性上传，逐文件 upload 容易漏。

本工具提供三个动作：
- `upload_prompt(file, kind, slug)`           → 单文件 → `eval_skill/prompts/uploads/<kind>/<slug>.md`
- `upload_prompt_pack(dir, pack_type, slug)`  → 整套包 → `eval_skill/prompts/uploads/packs/<pack_type>/<slug>/`
- `upload_dataset(file, target_name, ...)`    → 本地 csv/jsonl 转 v2 schema 推到 Lumi

CLI 入口见 [cli.py](../cli.py) 的 `upload-prompt` / `upload-prompt-pack` / `upload-dataset` 子命令。
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

from skill_commons import build_lumi_client


# 项目根（eval_skill/）—— prompt 上传的相对锚点
EVAL_SKILL_ROOT = Path(__file__).resolve().parent.parent
PROMPT_UPLOAD_BASE = EVAL_SKILL_ROOT / "prompts" / "uploads"
PROMPT_PACK_BASE = PROMPT_UPLOAD_BASE / "packs"

PROMPT_KINDS = ("system", "judge", "user_template")
SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


# ============================================================================
# Prompt 上传
# ============================================================================
@dataclass
class PromptUploadResult:
    saved_path: Path           # 绝对路径
    yaml_ref: str              # yaml 里直接填的引用字符串（相对 eval_skill/）
    kind: str
    slug: str
    overwritten: bool


def upload_prompt(
    source_file: Path | str,
    kind: str,
    slug: str,
    overwrite: bool = False,
    dry_run: bool = False,
) -> PromptUploadResult:
    """把本地文件保存到 prompts/uploads/<kind>/<slug>.md。

    Parameters
    ----------
    source_file : 本地文件路径（任何扩展名都可以，统一存为 .md）
    kind        : system | judge | user_template
    slug        : 文件名主体（仅允许字母数字下划线连字符），最终保存为 <slug>.md
    overwrite   : 同名文件是否允许覆盖
    dry_run     : True 时只解析、不落盘；用于上传前给用户预览

    Returns
    -------
    PromptUploadResult，含可直接填入 yaml 的 ref 字符串。
    dry_run=True 时 ``overwritten`` 表示目标是否已存在（不会真去覆盖）。
    """
    if kind not in PROMPT_KINDS:
        raise ValueError(f"kind 必须是 {PROMPT_KINDS} 之一，got {kind!r}")
    if not SLUG_RE.match(slug):
        raise ValueError(f"slug 仅允许 [a-zA-Z0-9_-]，got {slug!r}")

    src = Path(source_file).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在：{src}")

    target_dir = PROMPT_UPLOAD_BASE / kind
    target = target_dir / f"{slug}.md"
    overwritten = target.exists()
    if overwritten and not overwrite:
        raise FileExistsError(
            f"目标已存在：{target}\n如要覆盖，加 --overwrite。"
        )

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    rel = target.relative_to(EVAL_SKILL_ROOT)
    return PromptUploadResult(
        saved_path=target,
        yaml_ref=str(rel),
        kind=kind,
        slug=slug,
        overwritten=overwritten,
    )


# ============================================================================
# Prompt Pack 上传（一次传一整套相关 prompt + 配置）
# ============================================================================
@dataclass
class PromptPackUploadResult:
    saved_dir: Path                  # 绝对路径
    yaml_ref: str                    # yaml prompt_pack 字段的值（相对 eval_skill/）
    pack_type: str                   # rvec | ...
    slug: str
    mode: str                        # strict | merge | lite
    files_copied: List[str]          # 用户提供 → 直接复制（含路径相对名）
    files_filled_from_template: List[str]  # 从 fallback 模板兜底复制
    overwritten: bool
    pack_meta: Dict[str, Any]        # 解析出的 pack.yaml 摘要：domain/version/scoring_mode/signals_count 等


# 允许跟着 pack 一起带过去的"非必填"扩展名（README.md / NOTES.md 等）
_PACK_OPTIONAL_SUFFIXES = (".md", ".yaml", ".yml", ".txt")

# pack 模式
PACK_MODES = ("strict", "merge", "lite")


def _validate_rvec_pack_yaml(pack_yaml_path: Path) -> Dict[str, Any]:
    """RVEC pack.yaml 校验。返回解析出的 dict 供后续摘要用。

    校验规则（与 [_rvec_helpers.py](../metrics/_rvec_helpers.py) / [rvec.py](../metrics/rvec.py) 对齐）：
      - 顶层必须是 dict
      - scoring_mode 仅支持 'llm'（rule 在 metric 启动时会 raise NotImplementedError）
      - signals 必须是非空 list；每条至少含 tag_id / dim / name
      - highlights 可选；如果有，每条至少含 tag_id / dim / name
    """
    try:
        data = yaml.safe_load(pack_yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"pack.yaml 解析失败：{e}") from e

    if not isinstance(data, dict):
        raise ValueError("pack.yaml 顶层必须是 dict")

    mode = str(data.get("scoring_mode", "llm")).lower()
    if mode != "llm":
        raise ValueError(
            f"scoring_mode={mode!r} 暂不支持；当前 RVECJudge 仅实现 'llm' 模式"
        )

    signals = data.get("signals")
    if not isinstance(signals, list) or not signals:
        raise ValueError("pack.yaml.signals 必须是非空 list")
    for i, s in enumerate(signals):
        if not isinstance(s, dict):
            raise ValueError(f"signals[{i}] 不是 dict")
        for k in ("tag_id", "dim", "name"):
            if not s.get(k):
                raise ValueError(f"signals[{i}] 缺少必填字段 {k!r}")

    highlights = data.get("highlights") or []
    if not isinstance(highlights, list):
        raise ValueError("pack.yaml.highlights 必须是 list（可为空）")
    for i, h in enumerate(highlights):
        if not isinstance(h, dict):
            raise ValueError(f"highlights[{i}] 不是 dict")
        for k in ("tag_id", "dim", "name"):
            if not h.get(k):
                raise ValueError(f"highlights[{i}] 缺少必填字段 {k!r}")

    return data


# pack 类型注册表：required_files + yaml 校验函数 + yaml 文件名 + lite 模式兜底模板目录
_RVEC_REQUIRED_FILES = (
    "pack.yaml",
    "step1_understand.md",
    "step2_R.md",
    "step2_V.md",
    "step2_E.md",
    "step2_C.md",
    "step3_scoring.md",
)


@dataclass(frozen=True)
class _PackTypeSpec:
    required_files: Tuple[str, ...]
    yaml_file: str                              # 即 pack.yaml；用户在 lite 模式下唯一需要提供的文件
    yaml_validator: Callable[[Path], Dict[str, Any]]
    metric_hint: str                            # CLI 打印的 yaml 引用示范用 metric 名
    template_dir: Path                          # merge / lite 模式的兜底来源（需含全部 required_files）


PACK_TYPES: Dict[str, _PackTypeSpec] = {
    "rvec": _PackTypeSpec(
        required_files=_RVEC_REQUIRED_FILES,
        yaml_file="pack.yaml",
        yaml_validator=_validate_rvec_pack_yaml,
        metric_hint="rvec_judge",
        template_dir=EVAL_SKILL_ROOT / "prompts" / "judge" / "rvec_general",
    ),
}


def upload_prompt_pack(
    source: Path | str,
    pack_type: str,
    slug: str,
    overwrite: bool = False,
    mode: str = "strict",
    dry_run: bool = False,
) -> PromptPackUploadResult:
    """把一个 prompt pack 复制到 ``prompts/uploads/packs/<pack_type>/<slug>/``。

    ``source`` 既可以是目录（一份多文件包）也可以是单文件 ``pack.yaml``。
    ``mode`` 决定如何对待"用户没有提供的文件"：

    - ``strict``（默认）：要求 ``source`` 是目录且 7 个必填文件全部齐全；
      流程 prompt 也想完全自定义时使用。
    - ``merge``：``source`` 是目录，缺啥从 ``rvec_general/`` 兜底。常见用途：
      用户改了 pack.yaml + step3_scoring.md（评分阈值），其他保持模板。
    - ``lite``：``source`` 可以只是一个 pack.yaml 文件（或目录里只有它），
      6 个 step prompt 全部从 ``rvec_general/`` 复制。**最低门槛入口**：
      用户只要把"标签集 + caps"整成一份 yaml 就能跑起来。

    设计动机
    --------
    真实场景里，研究员手里通常只有一份"标签设计文档"，不是已经按 7 个文件
    拆好的包。强制让他们手工拆 6 份 step prompt 门槛过高；事实上 step prompt
    95% 是"流程骨架"（JSON 输出格式、占位符、校准原则），跟具体领域无关——
    领域差异都集中在 ``pack.yaml`` 的标签清单和 caps 上。
    所以 ``lite`` 模式只要求一份 pack.yaml，其他从 ``rvec_general`` 复用。
    """
    # ---- 参数校验 ----
    if pack_type not in PACK_TYPES:
        raise ValueError(
            f"pack_type 必须是 {tuple(PACK_TYPES)} 之一，got {pack_type!r}"
        )
    if mode not in PACK_MODES:
        raise ValueError(f"mode 必须是 {PACK_MODES} 之一，got {mode!r}")
    if not SLUG_RE.match(slug):
        raise ValueError(f"slug 仅允许 [a-zA-Z0-9_-]，got {slug!r}")

    spec = PACK_TYPES[pack_type]

    src = Path(source).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"source 不存在：{src}")

    # ---- 解析用户提供的文件 → user_files: {required_name: source_path} ----
    user_files: Dict[str, Path] = {}
    extra_files: List[Path] = []   # 可选附带：README.md 等

    if src.is_file():
        # 单文件入口：必须是 yaml（按后缀认；落盘时统一改名为 pack.yaml）
        # 用户从 init-pack 拿到的模板路径常是 ~/work/rvec_finance.yaml 这种有意义名字，
        # 强制要求文件名必须叫 pack.yaml 不合理。
        if mode == "strict":
            raise ValueError(
                "strict 模式必须传目录（含完整 7 文件）；"
                "如果只有 pack.yaml，请用 --mode lite"
            )
        if src.suffix.lower() not in (".yaml", ".yml"):
            raise ValueError(
                f"单文件入口必须是 .yaml/.yml（pack.yaml 角色）；got {src.name!r}"
            )
        user_files[spec.yaml_file] = src
    elif src.is_dir():
        for name in spec.required_files:
            p = src / name
            if p.is_file():
                user_files[name] = p
        # 同目录的 README.md 等
        for entry in sorted(src.iterdir()):
            if (
                entry.is_file()
                and entry.name not in spec.required_files
                and entry.suffix.lower() in _PACK_OPTIONAL_SUFFIXES
            ):
                extra_files.append(entry)
    else:
        raise ValueError(f"source 必须是文件或目录：{src}")

    # ---- 按 mode 决定"必须用户提供的文件" ----
    if mode == "strict":
        # 全部 required 必须由用户提供
        must_have = set(spec.required_files)
    elif mode == "merge":
        # 至少 pack.yaml 必须由用户提供（没标签清单等于没自定义）
        must_have = {spec.yaml_file}
    else:  # lite
        # 仅 pack.yaml
        must_have = {spec.yaml_file}

    missing = [f for f in must_have if f not in user_files]
    if missing:
        raise ValueError(
            f"{pack_type} pack 在 {mode!r} 模式下缺少必填文件：{missing}；"
            f"模式说明：strict=全 7 文件；merge=至少 pack.yaml；lite=仅 pack.yaml"
        )

    # ---- pack.yaml 内容校验 ----
    pack_meta = spec.yaml_validator(user_files[spec.yaml_file])

    # ---- 模板兜底（merge / lite）----
    template_files: Dict[str, Path] = {}
    if mode != "strict":
        for name in spec.required_files:
            if name in user_files:
                continue
            tpl = spec.template_dir / name
            if not tpl.is_file():
                # 模板自身有问题（不该发生）
                raise RuntimeError(
                    f"内置模板缺失 {tpl}；pack_type={pack_type} 安装可能损坏"
                )
            template_files[name] = tpl

    # ---- 目标目录准备 ----
    target_dir = PROMPT_PACK_BASE / pack_type / slug
    overwritten = target_dir.exists()
    if overwritten and not overwrite:
        raise FileExistsError(
            f"目标目录已存在：{target_dir}\n如要覆盖，加 --overwrite（会清空目录后重写）"
        )

    files_copied: List[str] = []
    files_filled: List[str] = []

    if not dry_run:
        if overwritten:
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # ---- 落盘 ----
        for name, p in user_files.items():
            shutil.copy2(p, target_dir / name)
            files_copied.append(name)
        for extra in extra_files:
            shutil.copy2(extra, target_dir / extra.name)
            files_copied.append(extra.name)

        for name, tpl in template_files.items():
            shutil.copy2(tpl, target_dir / name)
            files_filled.append(name)
    else:
        # dry-run 也要把"会落到哪几个文件"如实展示
        for name in user_files:
            files_copied.append(name)
        for extra in extra_files:
            files_copied.append(extra.name)
        for name in template_files:
            files_filled.append(name)

    yaml_ref = str(target_dir.relative_to(EVAL_SKILL_ROOT)).replace(os.sep, "/")

    sig_list = pack_meta.get("signals") or []
    hl_list = pack_meta.get("highlights") or []
    summary = {
        "domain": pack_meta.get("domain"),
        "version": pack_meta.get("version"),
        "scoring_mode": pack_meta.get("scoring_mode", "llm"),
        "signals_count": len(sig_list),
        "highlights_count": len(hl_list),
        "metric_hint": spec.metric_hint,
        # 下面三个是给 dry-run 展示用的；执行模式也带上不会出错
        "caps": pack_meta.get("caps") or {},
        "signals_preview": sig_list[:8],
        "highlights_preview": hl_list[:5],
    }
    return PromptPackUploadResult(
        saved_dir=target_dir,
        yaml_ref=yaml_ref,
        pack_type=pack_type,
        slug=slug,
        mode=mode,
        files_copied=sorted(files_copied),
        files_filled_from_template=sorted(files_filled),
        overwritten=overwritten,
        pack_meta=summary,
    )


# ============================================================================
# pack.yaml 脚手架：给用户一个带注释的模板，把"按规范拆"的门槛降到最低
# ============================================================================
_RVEC_PACK_YAML_SCAFFOLD = """\
# ============================================================================
# RVEC pack.yaml — 你的领域标签包
# ============================================================================
# 这是 upload-prompt-pack --type rvec 唯一**必须**由你提供的文件。
# 6 个 step prompt（step1/step2_R/V/E/C + step3_scoring）默认从 rvec_general
# 复用——它们是流程骨架（JSON 输出格式、占位符、校准原则），跟领域无关，
# 领域差异 95% 都集中在下面的 signals / highlights / caps 里。
#
# 填好之后：
#   python -m eval_skill.cli upload-prompt-pack \\
#       --file <这份 yaml> --type rvec --slug <你的包名> --mode lite
# ============================================================================

domain: <your_domain>          # 领域标识，如 finance / medical / code_review
version: v0.1
description: <一句话描述用途>

# step3 评分模式：当前仅支持 'llm'
scoring_mode: llm

# 信号上限：保护打分稳定性，按需调整
caps:
  bad_total: {mut: 5, baseline: 4}    # 自家 mut 给更宽容的上限，竞品收紧
  good_total: 3
  per_dim: {R: 2, V: 2, E: 1, C: 3}

# ============================================================================
# signals: 扣分信号（必填，至少 1 条）
# ============================================================================
# 每条至少 4 个字段：tag_id / name / dim / levels
#   tag_id : 全局唯一短码，建议 R-XXX-1 / V-XXX-1 / E-XXX-1 这种前缀
#            前缀首字母决定主维度（R/V/E）
#   dim    : 子维度，如 R1/R2/V1/V2/E1/E2，自定义即可
#   levels : 该信号允许的严重度，从 [P0, P1, P2] 中选若干（P0 最严重）
#
# 示例（替换成你的领域标签）：
signals:
  # R 可信性
  - {tag_id: R-FACT-1, name: 关键事实错误, dim: R3, levels: [P0, P1]}
  - {tag_id: R-REA-1,  name: 推理跳步,     dim: R4, levels: [P1, P2]}

  # V 有用性
  - {tag_id: V-SOL-1,  name: 方案不可执行, dim: V2, levels: [P1, P2]}

  # E 体验
  - {tag_id: E-NAT-1,  name: AI 味重,      dim: E3, levels: [P1, P2]}

# ============================================================================
# highlights: 加分亮点（可选，留空就 highlights: []）
# ============================================================================
# 每条至少 3 个字段：tag_id / name / dim
#   dim 必须是 C-R / C-V / C-E 之一（分别挂在 R/V/E 三个维度的亮点池）
highlights:
  - {tag_id: C-R-01, name: 边界清晰,   dim: C-R}
  - {tag_id: C-V-01, name: 方案有效,   dim: C-V}
  - {tag_id: C-E-01, name: 表达自然,   dim: C-E}
"""


def init_pack_template(
    pack_type: str,
    target: Path | str,
    overwrite: bool = False,
) -> Path:
    """生成一份带注释的 pack.yaml 模板写到 ``target``，让用户从有引导的起点开始填。

    用户的"RVEC 文档"通常是 markdown / word / 内部 wiki，没法直接喂给评测——
    pack.yaml 是机器要解析的，必须结构化。这条命令把"拆"的成本压到最低：
    只拆这一份 yaml，照着模板填标签即可，6 个 step prompt 不用动。

    Parameters
    ----------
    pack_type : 'rvec'（与 PACK_TYPES 对齐）
    target    : 输出文件路径（建议 .yaml 后缀）
    overwrite : 目标已存在时是否覆盖

    Returns
    -------
    Path : 写入的目标路径
    """
    if pack_type != "rvec":
        # 未来 PACK_TYPES 多了再分发模板
        raise ValueError(f"init-pack 当前仅支持 --type rvec，got {pack_type!r}")

    out = Path(target).expanduser().resolve()
    if out.exists() and not overwrite:
        raise FileExistsError(
            f"目标已存在：{out}\n如要覆盖，加 --overwrite。"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_RVEC_PACK_YAML_SCAFFOLD, encoding="utf-8")
    return out


# ============================================================================
# Dataset 上传
# ============================================================================
V2_SCHEMA_VALUES = {
    "single_choice", "array", "string", "number",
    "open_ended", "dialog", "report_pair",
}

# 字符串答案 → array 切分默认正则（与 migrate_legacy_dataset.py 对齐）
_DEFAULT_ARRAY_SPLITTER = re.compile(r"[,，;\s]+")


@dataclass
class DatasetUploadResult:
    target_name: str
    items_uploaded: int
    items_total: int
    created_dataset: bool
    sample_preview: List[Dict[str, Any]]  # 前 3 条转换结果


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"jsonl 第 {line_no} 行解析失败：{e}") from e


def _read_csv(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {k: (v if v != "" else None) for k, v in row.items()}


def _csv_row_to_v2(
    row: Dict[str, Any],
    schema: str,
    input_keys: List[str],
    expected_key: Optional[str],
    metadata_keys: List[str],
    auto_split_array: bool,
) -> Dict[str, Any]:
    """把一行 csv 字典装配成 v2 item。"""
    input_block: Dict[str, Any] = {}
    for k in input_keys:
        if k in row and row[k] is not None:
            input_block[k] = row[k]

    expected: Dict[str, Any] = {}
    if expected_key and row.get(expected_key) is not None:
        ans = row[expected_key]
        if schema == "array" and isinstance(ans, str) and auto_split_array:
            parts = [p.strip() for p in _DEFAULT_ARRAY_SPLITTER.split(ans) if p.strip()]
            if len(parts) > 1:
                ans = parts
        expected["answer"] = ans

    metadata: Dict[str, Any] = {"schema": schema}
    for k in metadata_keys:
        if k in row and row[k] is not None:
            metadata[k] = row[k]

    return {"input": input_block, "expected_output": expected, "metadata": metadata}


def _validate_v2(item: Dict[str, Any], idx: int) -> None:
    """对 jsonl 直传的 item 做最小 v2 校验。"""
    if not isinstance(item, dict):
        raise ValueError(f"item #{idx} 不是 dict")
    if "input" not in item:
        raise ValueError(f"item #{idx} 缺 'input' 字段")
    md = item.get("metadata") or {}
    if "schema" not in md:
        raise ValueError(f"item #{idx} metadata.schema 缺失（v2 必填）")
    if md["schema"] not in V2_SCHEMA_VALUES:
        raise ValueError(
            f"item #{idx} metadata.schema={md['schema']!r} 非法，"
            f"必须是 {V2_SCHEMA_VALUES}"
        )


def upload_dataset(
    source_file: Path | str,
    target_name: str,
    *,
    description: str = "",
    csv_schema: Optional[str] = None,
    csv_input_keys: Optional[List[str]] = None,
    csv_expected_key: Optional[str] = None,
    csv_metadata_keys: Optional[List[str]] = None,
    auto_split_array: bool = True,
    dry_run: bool = False,
    dry_run_limit: int = 3,
) -> DatasetUploadResult:
    """把本地 csv 或 jsonl 推送到 Lumi 作为 v2 dataset。

    支持两种输入格式
    ----------------
    - **.jsonl**：每行已经是 v2 dict（含 input / expected_output / metadata），直接校验后上传。
                  如果你已经在外部把数据搞成 v2 schema，这是最快捷的路径。
    - **.csv** ：每行是扁平字段，由 csv_* 参数定义字段映射；schema 必须显式指定。

    CSV 字段映射（csv_* 参数）
    ---------------------------
    - csv_schema       : v2 schema（single_choice/array/string/number/open_ended/dialog/report_pair）
    - csv_input_keys   : 这些列进 item.input
    - csv_expected_key : 这一列的值进 item.expected_output.answer
    - csv_metadata_keys: 这些列进 item.metadata
    - auto_split_array : schema=array 时，字符串答案按 [,，;\\s]+ 切分

    Parameters
    ----------
    source_file : 本地 .csv 或 .jsonl 文件
    target_name : Lumi 上的 dataset 名（不存在则创建）
    description : Lumi dataset 描述
    dry_run     : True 时不上传 Lumi，只打印前 N 条转换结果
    """
    src = Path(source_file).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在：{src}")

    suffix = src.suffix.lower()
    items: List[Dict[str, Any]] = []

    if suffix == ".jsonl":
        for idx, raw in enumerate(_read_jsonl(src)):
            _validate_v2(raw, idx)
            items.append(raw)
    elif suffix == ".csv":
        if not csv_schema:
            raise ValueError("csv 输入必须指定 csv_schema")
        if csv_schema not in V2_SCHEMA_VALUES:
            raise ValueError(f"csv_schema={csv_schema!r} 非法")
        if not csv_input_keys:
            raise ValueError("csv 输入必须指定 csv_input_keys（哪些列进 input）")
        for row in _read_csv(src):
            items.append(_csv_row_to_v2(
                row,
                schema=csv_schema,
                input_keys=csv_input_keys,
                expected_key=csv_expected_key,
                metadata_keys=csv_metadata_keys or [],
                auto_split_array=auto_split_array,
            ))
    else:
        raise ValueError(f"不支持的文件后缀 {suffix}，仅支持 .csv / .jsonl")

    preview = items[:dry_run_limit]

    if dry_run:
        return DatasetUploadResult(
            target_name=target_name,
            items_uploaded=0,
            items_total=len(items),
            created_dataset=False,
            sample_preview=preview,
        )

    client = build_lumi_client()
    created = False
    try:
        client.create_dataset(name=target_name, description=description)
        created = True
    except Exception:
        # 已存在则忽略；具体异常类型 sdk 不稳定，统一吞掉
        created = False

    n = 0
    for it in items:
        client.create_dataset_item(
            dataset_name=target_name,
            input=it.get("input", {}),
            expected_output=it.get("expected_output", {}),
            metadata=it.get("metadata", {}),
        )
        n += 1

    return DatasetUploadResult(
        target_name=target_name,
        items_uploaded=n,
        items_total=len(items),
        created_dataset=created,
        sample_preview=preview,
    )
