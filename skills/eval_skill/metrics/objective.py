"""客观指标：accuracy / exact_match / contains / array_recall / array_f1 / numeric_match。"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Set

from ..core.runner import RunOutput
from ..core.sample import Sample
from .base import Metric, MetricResult


# ============================================================================
# normalizer
# ============================================================================
_CN_PUNCT_TABLE = str.maketrans({
    "，": ",", "。": ".", "；": ";", "：": ":", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "“": '"', "”": '"',
    "‘": "'", "’": "'", "、": ",",
})


def _normalize(s: str, normalizer: str, case_sensitive: bool) -> str:
    if not isinstance(s, str):
        s = str(s)
    if normalizer == "identity":
        return s if case_sensitive else s.lower()
    if normalizer == "chinese_punct":
        s = unicodedata.normalize("NFKC", s).translate(_CN_PUNCT_TABLE).strip()
        return s if case_sensitive else s.lower()
    # default lower_strip
    s = s.strip()
    return s if case_sensitive else s.lower()


# ============================================================================
# accuracy 答案抽取
# ============================================================================
_CN_FINAL_RE = re.compile(r"最终答案[是为：:]?\s*\(?([A-Za-z])\)?")
_EN_THE_ANSWER_RE = re.compile(r"the\s+answer\s+is\s*\*?\*?\(?([A-Za-z])\)?", re.IGNORECASE)
_LAST_LETTER_RE = re.compile(r"\b([A-Za-z])\b")


def _extract_choice(text: str, extractor: str, regex: str | None) -> str:
    if extractor == "regex":
        if not regex:
            raise ValueError("extractor=regex 需要 extractor_regex")
        m = re.search(regex, text)
        return m.group(1).upper() if m else ""
    if extractor == "en_the_answer":
        m = _EN_THE_ANSWER_RE.search(text)
        return m.group(1).upper() if m else ""
    if extractor == "last_letter":
        found = _LAST_LETTER_RE.findall(text)
        return found[-1].upper() if found else ""
    # default cn_final_answer
    m = _CN_FINAL_RE.search(text)
    if m:
        return m.group(1).upper()
    found = _LAST_LETTER_RE.findall(text)
    return found[-1].upper() if found else ""


# ============================================================================
# Accuracy
# ============================================================================
class Accuracy(Metric):
    name = "accuracy"

    def compute(self, sample: Sample, output: RunOutput, judge=None, **_kwargs) -> MetricResult:
        gt_raw = sample.ground_truth.answer
        gt = (str(gt_raw) if gt_raw is not None else "").strip().upper()
        pred = _extract_choice(output.final_text, self.spec.extractor, self.spec.extractor_regex)
        ok = 1.0 if pred and pred == gt else 0.0
        return MetricResult(
            self.column, ok,
            reason=f"expected={gt} actual={pred} extractor={self.spec.extractor}",
            extra={"prediction": pred, "expected": gt},
        )


# ============================================================================
# string-like
# ============================================================================
class ExactMatch(Metric):
    name = "exact_match"

    def compute(self, sample, output, judge=None, **_kwargs):
        gt = sample.ground_truth.answer
        if gt is None:
            return MetricResult(self.column, 0.0, "no ground truth")
        a = _normalize(str(gt), self.spec.normalizer, self.spec.case_sensitive)
        b = _normalize(output.final_text, self.spec.normalizer, self.spec.case_sensitive)
        return MetricResult(
            self.column, 1.0 if a == b else 0.0,
            reason=f"len_gt={len(a)} len_pred={len(b)} case_sensitive={self.spec.case_sensitive}",
        )


class Contains(Metric):
    name = "contains"

    def compute(self, sample, output, judge=None, **_kwargs):
        gt = sample.ground_truth.answer
        if gt is None:
            return MetricResult(self.column, 0.0, "no ground truth")
        a = _normalize(str(gt), self.spec.normalizer, self.spec.case_sensitive)
        b = _normalize(output.final_text, self.spec.normalizer, self.spec.case_sensitive)
        if a and a in b:
            offset = b.find(a)
            return MetricResult(self.column, 1.0, reason=f"hit at offset={offset}")
        return MetricResult(self.column, 0.0, reason="gt not found in prediction")


# ============================================================================
# array-like
# ============================================================================
def _split(text: str, splitter: str) -> List[str]:
    parts = re.split(splitter, text)
    return [p for p in (s.strip() for s in parts) if p]


def _to_norm_set(items, normalizer: str, case_sensitive: bool) -> Set[str]:
    return {_normalize(str(x), normalizer, case_sensitive) for x in items if str(x).strip()}


class ArrayRecall(Metric):
    name = "array_recall"

    def compute(self, sample, output, judge=None, **_kwargs):
        gt_list = sample.ground_truth.answer or []
        if isinstance(gt_list, str):
            gt_list = _split(gt_list, self.spec.splitter)
        gt = _to_norm_set(gt_list, self.spec.normalizer, self.spec.case_sensitive)
        pred = _to_norm_set(_split(output.final_text, self.spec.splitter),
                            self.spec.normalizer, self.spec.case_sensitive)
        if not gt:
            return MetricResult(self.column, 0.0, "empty ground truth")
        hit = gt & pred
        v = len(hit) / len(gt)
        return MetricResult(
            self.column, v,
            reason=f"hit={sorted(hit)} missed={sorted(gt - pred)}",
            extra={"prediction": sorted(pred), "ground_truth": sorted(gt)},
        )


class ArrayF1(Metric):
    name = "array_f1"

    def compute(self, sample, output, judge=None, **_kwargs):
        gt_list = sample.ground_truth.answer or []
        if isinstance(gt_list, str):
            gt_list = _split(gt_list, self.spec.splitter)
        gt = _to_norm_set(gt_list, self.spec.normalizer, self.spec.case_sensitive)
        pred = _to_norm_set(_split(output.final_text, self.spec.splitter),
                            self.spec.normalizer, self.spec.case_sensitive)
        if not gt or not pred:
            return MetricResult(self.column, 0.0,
                                f"gt_empty={not gt} pred_empty={not pred}")
        tp = len(gt & pred)
        precision = tp / len(pred)
        recall = tp / len(gt)
        f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
        return MetricResult(
            self.column, f1,
            reason=f"precision={precision:.3f} recall={recall:.3f}",
            extra={"precision": precision, "recall": recall},
        )


# ============================================================================
# numeric_match
# ============================================================================
_NUM_RE = re.compile(r"-?\d+(?:[\.,]\d+)*(?:[eE][-+]?\d+)?")


def _parse_number(text: str) -> float | None:
    """从模型输出里抽数字：取最后一个匹配（一般在 '答案：xxx' 后面）。"""
    found = _NUM_RE.findall(text)
    if not found:
        return None
    s = found[-1].replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


class NumericMatch(Metric):
    name = "numeric_match"

    def compute(self, sample, output, judge=None, **_kwargs):
        gt = sample.ground_truth.answer
        if gt is None:
            return MetricResult(self.column, 0.0, "no ground truth")
        try:
            gt_num = float(str(gt).replace(",", ""))
        except ValueError:
            return MetricResult(self.column, 0.0, f"gt not numeric: {gt}")
        pred_num = _parse_number(output.final_text)
        if pred_num is None:
            return MetricResult(self.column, 0.0, "no number in prediction",
                                extra={"prediction_text": output.final_text[-200:]})
        diff = abs(pred_num - gt_num)
        if self.spec.relative:
            denom = abs(gt_num) if gt_num != 0 else 1.0
            ok = (diff / denom) <= self.spec.tolerance
            metric = diff / denom
        else:
            ok = diff <= self.spec.tolerance
            metric = diff
        return MetricResult(
            self.column, 1.0 if ok else 0.0,
            reason=f"pred={pred_num} gt={gt_num} diff={metric:.6g} tol={self.spec.tolerance} relative={self.spec.relative}",
            extra={"prediction": pred_num, "ground_truth": gt_num, "diff": metric},
        )
