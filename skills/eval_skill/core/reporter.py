"""Reporter：CSV 明细 + summary.json + 可选 Langfuse trace。

CSV 列由 alias_or_name 决定；同 name 多变体通过 alias 区分。
断点续传：load 已有 CSV 的 (sample_id, model, run_prefix, round)。
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..metrics.base import MetricResult
from .config import ExperimentConfig, ModelSpec
from .runner import RunOutput
from .sample import Sample

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "outputs"


class Reporter:
    def __init__(self, cfg: ExperimentConfig):
        self.cfg = cfg
        self.exp_dir = OUTPUT_ROOT / cfg.experiment_name
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        self.rows: List[Dict[str, Any]] = []
        self._lumi = self._init_lumi() if "lumi" in cfg.execution.reporter else None

    # ---------------- lumi ----------------
    def _init_lumi(self):
        try:
            from skill_commons import build_lumi_client
            return build_lumi_client()
        except Exception as e:
            print(f"[reporter] lumi 初始化失败，降级为只写 csv: {e}")
            return None

    # ---------------- resume ----------------
    def load_completed_keys(self) -> Set[Tuple[str, str, str, int]]:
        csv_path = self.exp_dir / "samples.csv"
        keys: Set[Tuple[str, str, str, int]] = set()
        if not csv_path.exists():
            return keys
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    keys.add((
                        row.get("sample_id", ""),
                        row.get("model", ""),
                        row.get("run_prefix", ""),
                        int(row.get("round") or 0),
                    ))
        except Exception as e:
            print(f"[reporter] 读取已有 csv 失败: {e}")
        return keys

    # ---------------- record ----------------
    def record(self, sample: Sample, model: ModelSpec, round_idx: int,
               output: RunOutput, metrics: List[MetricResult]) -> None:
        row: Dict[str, Any] = {
            "experiment_name": self.cfg.experiment_name,
            "tags": ";".join(self.cfg.tags),
            "dataset": self.cfg.dataset.name,
            "round": round_idx,
            "model": model.model,
            "run_prefix": model.trace_label(),
            "host_profile": model.host_profile,
            "sample_id": sample.sample_id,
            "schema": sample.schema,
            "turn_kind": sample.turn_kind,
            "scoring_mode": sample.scoring_mode,
            "ground_truth": json.dumps(
                {"kind": sample.ground_truth.kind, "answer": sample.ground_truth.answer},
                ensure_ascii=False,
            ),
            "prediction": output.final_text,
            "reasoning": output.final_reasoning,
            "error": output.error or "",
            "tokens_input": output.total_usage().get("input", 0),
            "tokens_output": output.total_usage().get("output", 0),
            "latency_sec": sum(t.latency_sec for t in output.turns),
        }
        for m in metrics:
            row[f"{m.name}_value"] = m.value
            row[f"{m.name}_reason"] = m.reason
            for k, v in (m.extra or {}).items():
                if isinstance(v, (int, float, str, bool)):
                    row[f"{m.name}__{k}"] = v
        self.rows.append(row)

        if self._lumi is not None:
            self._push_lumi(sample, model, round_idx, output, metrics)

    def _push_lumi(self, sample, model, round_idx, output, metrics):
        try:
            run_name = self._lumi_run_name(model, round_idx)
            trace = self._lumi.trace(
                name=self.cfg.experiment_name,
                session_id=run_name,
                input={
                    "sample_id": sample.sample_id,
                    "schema": sample.schema,
                    "turn_kind": sample.turn_kind,
                    "scoring_mode": sample.scoring_mode,
                    **sample.fields,
                },
                output={"prediction": output.final_text, "reasoning": output.final_reasoning},
                metadata={
                    "tested_model": model.model,
                    "run_prefix": model.trace_label(),
                    "dataset": self.cfg.dataset.name,
                    "round": round_idx,
                    **sample.metadata,
                },
                tags=[*self.cfg.tags, model.model, self.cfg.dataset.name,
                      model.trace_label(), f"round_{round_idx}"],
            )
            self._push_lumi_generation(trace, sample, model, output)
            self._push_metric_observations(trace, metrics)
            for m in metrics:
                trace.score(name=m.name, value=m.value, comment=(m.reason or "")[:500])
            self._update_lumi_trace_output(trace, output, metrics)
            self._link_lumi_dataset_run(trace, sample, run_name)
        except Exception as e:
            print(f"[reporter] lumi push 失败 sample={sample.sample_id}: {e}")

    def _lumi_run_name(self, model: ModelSpec, round_idx: int) -> str:
        """Langfuse/Lumi Experiments 视图里的 runName。

        对齐旧脚本的「每个模型一个 Experiment Run」口径；同一个 dataset item
        在不同模型/轮次下会形成不同 run item。
        """
        return f"{self.cfg.experiment_name}__{model.trace_label()}__round{round_idx}"

    def _trace_id(self, trace: Any) -> Optional[str]:
        return getattr(trace, "id", None) or getattr(trace, "trace_id", None)

    def _dataset_item_id(self, sample: Sample) -> Optional[str]:
        raw = sample.raw
        for attr in ("id", "dataset_item_id", "datasetItemId"):
            v = getattr(raw, attr, None)
            if v:
                return str(v)
        return sample.sample_id or None

    def _push_lumi_generation(self, trace: Any, sample: Sample,
                              model: ModelSpec, output: RunOutput) -> None:
        """可选写一条 generation，便于 Lumi trace 里看到模型输出节点。"""
        if not hasattr(trace, "generation"):
            return
        try:
            gen = trace.generation(
                name="model_response",
                model=model.model,
                input={"sample_id": sample.sample_id},
            )
            if hasattr(gen, "end"):
                gen.end(output={
                    "prediction": output.final_text,
                    "reasoning": output.final_reasoning,
                    "usage": output.total_usage(),
                    "error": output.error or "",
                })
        except Exception as e:
            print(f"[reporter] lumi generation 写入失败 sample={sample.sample_id}: {e}")

    def _push_metric_observations(self, trace: Any, metrics: List[MetricResult]) -> None:
        """把 metric 暴露的 step 级信息写入 Lumi trace。

        RVEC 会传入 step1 / step2_R/V/E/C / aggregate / step3。优先用 generation
        对齐旧脚本；aggregate 若 SDK 支持 span 则写 span，否则退化为 generation。
        """
        for m in metrics:
            observations = (m.extra or {}).get("_lumi_observations") or []
            if not isinstance(observations, list):
                continue
            for obs in observations:
                if not isinstance(obs, dict):
                    continue
                name = str(obs.get("name") or f"{m.name}.step")
                kind = str(obs.get("kind") or "generation")
                inp = obs.get("input") or {}
                out = obs.get("output") or {}
                try:
                    if kind == "span" and hasattr(trace, "span"):
                        span = trace.span(name=name, input=inp)
                        if hasattr(span, "end"):
                            span.end(output=out)
                    elif hasattr(trace, "generation"):
                        gen = trace.generation(name=name, model="judge", input=inp)
                        if hasattr(gen, "end"):
                            gen.end(output=out)
                except Exception as e:
                    print(f"[reporter] lumi observation 写入失败 {name}: {str(e)[:120]}")

    def _metric_output_payload(self, metrics: List[MetricResult]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for m in metrics:
            item: Dict[str, Any] = {"value": m.value, "reason": m.reason}
            for k, v in (m.extra or {}).items():
                if k.startswith("_"):
                    continue
                # JSON 字符串保留原样；标量/字符串足够展示 bad_tags_json/good_tags_json/step*_json
                if isinstance(v, (int, float, str, bool)) or v is None:
                    item[k] = v
            payload[m.name] = item
        return payload

    def _update_lumi_trace_output(self, trace: Any, output: RunOutput,
                                  metrics: List[MetricResult]) -> None:
        """更新 trace output，让 Lumi 详情页直接看到 bad/good tags 与 step JSON。"""
        if not hasattr(trace, "update"):
            return
        try:
            trace.update(output={
                "prediction": output.final_text,
                "reasoning": output.final_reasoning,
                "metrics": self._metric_output_payload(metrics),
            })
        except Exception as e:
            print(f"[reporter] lumi trace output 更新失败: {str(e)[:120]}")

    def _link_lumi_dataset_run(self, trace: Any, sample: Sample, run_name: str) -> None:
        """把 trace 关联到 dataset item，生成 Lumi/Langfuse Experiments 里的 run item。

        优先用 dataset item 自带 link；失败后回退到底层 dataset_run_items.create。
        """
        linked = False
        if sample.raw is not None and hasattr(sample.raw, "link"):
            try:
                sample.raw.link(trace, run_name)
                linked = True
            except Exception as e:
                print(f"[reporter] raw.link 失败 sample={sample.sample_id}: {str(e)[:120]}")

        trace_id = self._trace_id(trace)
        item_id = self._dataset_item_id(sample)
        if not trace_id or not item_id:
            return
        try:
            self._lumi.client.dataset_run_items.create(
                request={
                    "datasetItemId": item_id,
                    "runName": run_name,
                    "traceId": trace_id,
                }
            )
            linked = True
        except Exception as e:
            # 多数重复 link 会报已存在；不影响 CSV 和 trace 主流程
            msg = str(e)[:160]
            if "already" not in msg.lower() and "exists" not in msg.lower():
                print(f"[reporter] dataset run item link 失败 sample={sample.sample_id}: {msg}")

        if linked:
            print(f"[reporter] lumi trace linked sample={sample.sample_id} run={run_name}")

    # ---------------- finalize ----------------
    def finalize(self) -> Path:
        csv_path = self.exp_dir / "samples.csv"
        if not self.rows:
            print("[reporter] no rows recorded.")
            return csv_path

        # 收集所有列
        all_keys: List[str] = []
        seen: Set[str] = set()
        for r in self.rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)

        # append 模式（保留之前 resume 之外的行）
        existing_rows: List[Dict[str, Any]] = []
        if csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8") as f:
                    existing_rows = list(csv.DictReader(f))
                for r in existing_rows:
                    for k in r.keys():
                        if k not in seen:
                            seen.add(k); all_keys.append(k)
            except Exception:
                existing_rows = []

        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            for r in existing_rows + self.rows:
                w.writerow({k: r.get(k, "") for k in all_keys})

        # summary
        summary = self._aggregate(existing_rows + self.rows)
        (self.exp_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self._lumi is not None:
            try:
                self._lumi.flush()
            except Exception:
                pass
        print(f"[reporter] CSV 已写入: {csv_path}")
        print(f"[reporter] summary 已写入: {self.exp_dir / 'summary.json'}")
        return csv_path

    def _aggregate(self, rows: List[Dict[str, Any]]):
        groups: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
        for r in rows:
            key = (r.get("model", ""), r.get("run_prefix", ""),
                   int(r.get("round") or 0))
            groups.setdefault(key, []).append(r)
        summary = {
            "experiment_name": self.cfg.experiment_name,
            "tags": self.cfg.tags,
            "dataset": self.cfg.dataset.name,
            "total_rows": len(rows),
            "groups": [],
        }
        metric_cols = [m.column_name for m in self.cfg.metrics]
        for (model, run_prefix, rd), items in groups.items():
            agg = {
                "model": model, "run_prefix": run_prefix, "round": rd,
                "n": len(items),
                "errors": sum(1 for x in items if x.get("error")),
            }
            for col in metric_cols:
                vals = []
                for x in items:
                    v = x.get(f"{col}_value")
                    if v in (None, ""):
                        continue
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        continue
                if vals:
                    agg[f"{col}_mean"] = round(statistics.fmean(vals), 4)
                    agg[f"{col}_std"] = round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0
            summary["groups"].append(agg)
        return summary
