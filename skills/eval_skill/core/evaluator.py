"""Evaluator：编排 dataset / runner / judge / metrics / reporter。"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import List

from ..metrics.base import MetricResult
from ..metrics.registry import build_metrics
from skill_commons import load_redaction_profiles
from .config import ExperimentConfig, ModelSpec
from .dataset import load_dataset
from .judge import Judge
from .pdf_retriever import PDFRetriever
from .prompt_builder import PromptBuilder
from .reporter import Reporter
from .runner import Runner
from .sample import Sample


class Evaluator:
    def __init__(self, cfg: ExperimentConfig, project_root: Path):
        self.cfg = cfg
        self.project_root = project_root

        self.system_prompt = cfg.prompt_strategy.resolved_system_prompt(project_root)
        self.user_template = cfg.prompt_strategy.resolved_user_template(project_root)

        # PDF / redaction
        red_profiles = load_redaction_profiles()
        self.pdf_retriever = PDFRetriever(red_profiles)

        # judge
        self.judge = Judge(cfg.judge) if cfg.judge else None
        # metrics
        self.metrics = build_metrics(cfg.metrics)
        # reporter
        self.reporter = Reporter(cfg)

    # ---------------------------------------------------------------- run
    def run(self) -> Path:
        samples = load_dataset(self.cfg.dataset, self.cfg.sampling)
        print(f"[evaluator] dataset={self.cfg.dataset.name} loaded {len(samples)} samples")

        # PDF 预处理（attachments_md）
        if any(s.has_pdf for s in samples):
            self._enrich_pdfs(samples)

        # 续跑过滤
        completed = self.reporter.load_completed_keys() if self.cfg.execution.resume else set()
        if completed:
            print(f"[evaluator] resume: {len(completed)} rows already completed")

        for round_idx in range(1, self.cfg.execution.rounds + 1):
            for model in self.cfg.all_models():
                key_filter = lambda s, m=model, r=round_idx: (
                    s.sample_id, m.model, m.trace_label(), r
                ) not in completed
                todo = [s for s in samples if key_filter(s)]
                if not todo:
                    print(f"[evaluator] skip round={round_idx} model={model.model} (all done)")
                    continue
                print(f"\n=== round={round_idx} model={model.model} run_prefix={model.trace_label()} "
                      f"todo={len(todo)}/{len(samples)} ===")
                self._run_one(model, todo, round_idx)

        path = self.reporter.finalize()
        print(f"\n[evaluator] done. samples.csv => {path}")
        return path

    # ---------------------------------------------------------------- pdf
    def _enrich_pdfs(self, samples: List[Sample]) -> None:
        pp = self.cfg.preprocess.pdf
        for s in samples:
            for ref in s.pdf_refs:
                try:
                    if pp.mode == "smart":
                        md = self.pdf_retriever.extract_smart(
                            ref.path, pp.keywords, tuple(pp.window),
                            max_chars=pp.max_chars,
                            redaction_profile=pp.redaction_profile,
                        )
                    else:
                        md = self.pdf_retriever.extract_full(
                            ref.path, max_pages=pp.max_pages,
                            redaction_profile=pp.redaction_profile,
                        )
                    s.attachments_md[ref.label] = md
                except Exception as e:
                    s.attachments_md[ref.label] = f"[PDF load failed: {e}]"

    # ---------------------------------------------------------------- per model
    def _run_one(self, model: ModelSpec, samples: List[Sample], round_idx: int) -> None:
        builder = PromptBuilder(self.cfg.prompt_strategy, self.system_prompt, self.user_template)
        runner = Runner(model, builder)
        max_workers = self.cfg.execution.concurrency

        def _do(sample: Sample):
            output = runner.run(sample)
            results: List[MetricResult] = []
            for m in self.metrics:
                try:
                    results.append(m.compute(
                        sample, output, self.judge,
                        is_baseline=model.is_baseline,
                    ))
                except Exception as e:
                    results.append(MetricResult(m.column, 0.0, f"metric error: {e}"))
            return sample, output, results

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_do, s) for s in samples]
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    sample, output, results = fut.result()
                except Exception as e:
                    print(f"  [{i}] thread crash: {e}")
                    continue
                self.reporter.record(sample, model, round_idx, output, results)
                short = ", ".join(f"{r.name}={r.value:.2f}" for r in results)
                err = f" ERROR={output.error}" if output.error else ""
                print(f"  [{i}/{len(samples)}] {sample.sample_id} {short}{err}")
