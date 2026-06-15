"""dataset：从 Langfuse(Lumi) 拉取 dataset，解析为 List[Sample]，按 SamplingSpec 抽样。"""
from __future__ import annotations

import random
from typing import Iterator, List

from skill_commons import build_lumi_client

from .config import DatasetSpec, SamplingSpec
from .sample import Sample
from .sample_parser import parse_sample


def _iter_lumi_items(name: str) -> Iterator:
    client = build_lumi_client()
    ds = client.get_dataset(name)
    for item in ds.items:
        yield item


def load_dataset(spec: DatasetSpec, sampling: SamplingSpec) -> List[Sample]:
    samples: List[Sample] = []
    for i, item in enumerate(_iter_lumi_items(spec.name)):
        samples.append(parse_sample(item, fallback_id=f"{spec.name}-{i}"))

    if sampling.mode == "n" and sampling.n and sampling.n < len(samples):
        rng = random.Random(sampling.seed)
        samples = rng.sample(samples, sampling.n)
    elif sampling.mode == "ratio" and sampling.ratio:
        rng = random.Random(sampling.seed)
        k = max(1, int(len(samples) * sampling.ratio))
        samples = rng.sample(samples, k)

    return samples
