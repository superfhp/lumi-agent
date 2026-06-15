"""ExperimentConfig & 子配置（PromptStrategy / PreprocessSpec / MetricSpec ...）。

YAML → dataclass，做基础校验。host 注册表与脱敏 profile 由 skill_commons 提供。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------
@dataclass
class ModelSpec:
    host_profile: str
    model: str
    temperature: float = 0.1
    run_prefix: str = ""
    extra_body: Dict[str, Any] = field(default_factory=dict)
    max_tokens: Optional[int] = None
    # 由 evaluator 在区分 mut/baseline 时填；yaml 不直接暴露
    is_baseline: bool = False

    def trace_label(self) -> str:
        return self.run_prefix or self.model


# ----------------------------------------------------------------------------
# Judge
# ----------------------------------------------------------------------------
@dataclass
class JudgeSpec:
    host_profile: str
    model: str
    temperature: float = 0.0


# ----------------------------------------------------------------------------
# Dataset / Sampling
# ----------------------------------------------------------------------------
@dataclass
class DatasetSpec:
    name: str                       # Langfuse dataset 名


@dataclass
class SamplingSpec:
    mode: str = "full"              # full | n | ratio
    n: Optional[int] = None
    ratio: Optional[float] = None
    seed: int = 42


# ----------------------------------------------------------------------------
# Prompt strategy
# ----------------------------------------------------------------------------
@dataclass
class PromptStrategy:
    system_prompt: Optional[str] = None
    system_prompt_ref: Optional[str] = None
    user_template: Optional[str] = None
    user_template_ref: Optional[str] = None
    per_turn_prefix: str = ""

    def resolved_system_prompt(self, project_root: Path) -> Optional[str]:
        if self.system_prompt:
            return self.system_prompt
        if self.system_prompt_ref:
            return (project_root / self.system_prompt_ref).read_text(encoding="utf-8")
        return None

    def resolved_user_template(self, project_root: Path) -> Optional[str]:
        if self.user_template:
            return self.user_template
        if self.user_template_ref:
            return (project_root / self.user_template_ref).read_text(encoding="utf-8")
        return None


# ----------------------------------------------------------------------------
# Preprocess (PDF / ground_truth)
# ----------------------------------------------------------------------------
@dataclass
class PdfPreprocess:
    mode: str = "smart"             # smart | full
    keywords: List[str] = field(default_factory=list)
    window: List[int] = field(default_factory=lambda: [-1, 3])
    max_chars: int = 28000
    max_pages: int = 50
    redaction_profile: Optional[str] = None


@dataclass
class GroundTruthPreprocess:
    mode: str = "full"              # full | smart
    max_pages: int = 50
    max_chars: int = 60000


@dataclass
class PreprocessSpec:
    pdf: PdfPreprocess = field(default_factory=PdfPreprocess)
    ground_truth: GroundTruthPreprocess = field(default_factory=GroundTruthPreprocess)


# ----------------------------------------------------------------------------
# Metric
# ----------------------------------------------------------------------------
@dataclass
class MetricSpec:
    name: str                                    # 指标注册名
    alias: Optional[str] = None                  # CSV 列前缀；不填用 name

    # 通用
    weight: float = 1.0

    # 客观指标参数
    case_sensitive: bool = False
    normalizer: str = "lower_strip"              # identity | lower_strip | chinese_punct
    splitter: str = r"[,，;；\n、]"               # array 切分正则
    tolerance: float = 0.001                     # numeric_match
    relative: bool = False                       # numeric_match: |d|/|gt| 还是 |d|
    extractor: str = "cn_final_answer"           # accuracy: cn_final_answer | en_the_answer | last_letter | regex
    extractor_regex: Optional[str] = None        # accuracy: extractor=regex 时使用

    # 主观指标参数
    prompt_ref: Optional[str] = None             # 覆盖默认 judge system prompt 文件
    prompt_inline: Optional[str] = None          # 内联 judge system prompt
    user_prompt_ref: Optional[str] = None        # judge user-prompt 模板文件（变量同 prompt_builder）
    user_prompt_inline: Optional[str] = None     # 内联 judge user-prompt 模板
    judge_override: Optional[JudgeSpec] = None   # 单指标指定 judge

    # 复合主观指标
    sub_weights: Dict[str, float] = field(default_factory=dict)

    # 任意额外参数（指标内部自取）
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def column_name(self) -> str:
        return self.alias or self.name

    @classmethod
    def from_obj(cls, obj: Any) -> "MetricSpec":
        if isinstance(obj, str):
            return cls(name=obj)
        if not isinstance(obj, dict):
            raise TypeError(f"metric must be str or dict, got {type(obj)}")
        d = dict(obj)
        if "name" not in d:
            raise ValueError(f"metric missing 'name': {obj}")
        if "judge_override" in d and d["judge_override"]:
            d["judge_override"] = JudgeSpec(**d["judge_override"])
        # 把未知字段塞到 extra
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        extra = {k: v for k, v in d.items() if k not in known}
        for k in list(d.keys()):
            if k not in known:
                d.pop(k)
        d.setdefault("extra", {}).update(extra)
        return cls(**d)


# ----------------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------------
@dataclass
class ExecutionSpec:
    rounds: int = 1
    concurrency: int = 5
    reporter: List[str] = field(default_factory=lambda: ["csv", "lumi"])
    resume: bool = False


# ----------------------------------------------------------------------------
# ExperimentConfig
# ----------------------------------------------------------------------------
@dataclass
class ExperimentConfig:
    experiment_name: str
    tags: List[str]
    description: str
    dataset: DatasetSpec
    sampling: SamplingSpec
    prompt_strategy: PromptStrategy
    preprocess: PreprocessSpec
    model_under_test: ModelSpec
    baselines: List[ModelSpec]
    judge: Optional[JudgeSpec]
    metrics: List[MetricSpec]
    execution: ExecutionSpec
    config_path: Path

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        """单一 dataset；如果 yaml 写了多个 dataset 名会报错。"""
        results = cls.from_yaml_expanded(path)
        if len(results) != 1:
            raise ValueError(
                f"{path}: 该 yaml 展开了 {len(results)} 个 dataset，请用 from_yaml_expanded()"
            )
        return results[0]

    @classmethod
    def from_yaml_expanded(cls, path: str | Path) -> List["ExperimentConfig"]:
        """支持 dataset.name 是字符串或字符串列表。

        当为列表时，每个 dataset 名展开成一份独立的 ExperimentConfig，
        experiment_name 自动后缀 ``__<dataset>``。
        """
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))

        ds_block = dict(data.get("dataset") or {})
        names = ds_block.pop("name", None)
        if isinstance(names, str):
            name_list = [names]
        elif isinstance(names, list):
            name_list = [str(x) for x in names]
        else:
            raise ValueError(f"{p}: dataset.name 必须是 str 或 str 列表")

        out: List[ExperimentConfig] = []
        for name in name_list:
            suffix = f"__{name}" if len(name_list) > 1 else ""
            out.append(cls._build(data, p, DatasetSpec(name=name, **ds_block), suffix))
        return out

    @classmethod
    def _build(cls, data: Dict[str, Any], path: Path,
               dataset: "DatasetSpec", name_suffix: str) -> "ExperimentConfig":
        def _model(d: Dict[str, Any]) -> ModelSpec:
            d = dict(d)
            # is_baseline 由 evaluator 自动设置；yaml 误写一律忽略
            d.pop("is_baseline", None)
            return ModelSpec(**d)

        cfg = cls(
            experiment_name=data["experiment_name"] + name_suffix,
            tags=list(data.get("tags", [])),
            description=data.get("description", ""),
            dataset=dataset,
            sampling=SamplingSpec(**data.get("sampling", {})),
            prompt_strategy=PromptStrategy(**data.get("prompt_strategy", {})),
            preprocess=_parse_preprocess(data.get("preprocess", {})),
            model_under_test=_model(data["model_under_test"]),
            baselines=[_model(x) for x in data.get("baselines", [])],
            judge=JudgeSpec(**data["judge"]) if data.get("judge") else None,
            metrics=[MetricSpec.from_obj(m) for m in data.get("metrics", ["accuracy"])],
            execution=ExecutionSpec(**data.get("execution", {})),
            config_path=path,
        )
        cfg.model_under_test.is_baseline = False
        for b in cfg.baselines:
            b.is_baseline = True
        return cfg

    def all_models(self) -> List[ModelSpec]:
        return [self.model_under_test, *self.baselines]


def _parse_preprocess(d: Dict[str, Any]) -> PreprocessSpec:
    return PreprocessSpec(
        pdf=PdfPreprocess(**(d.get("pdf") or {})),
        ground_truth=GroundTruthPreprocess(**(d.get("ground_truth") or {})),
    )
