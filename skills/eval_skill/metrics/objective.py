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
# ── 主模式：行首 "答案：X" / "答案：BCDE" / "答案：A,C,D" ──
# 医学/金融选择题模型几乎都遵循此格式
_CN_ANSWER_LINE_RE = re.compile(
    r"^\s*答案\s*[：:]\s*([A-Ea-e](?:[,，、\s]*[A-Ea-e])*)",
    re.MULTILINE,
)
# ── 次模式："最终答案是X" / "最终答案为ABCD" ──
_CN_FINAL_RE = re.compile(
    r"最终答案\s*[是为：:]?\s*\(?([A-Ea-e](?:[,，、\s]*[A-Ea-e])*)\)?",
)
# ── 变体模式 ──
_CN_ANSWER_VARIANT_PATTERNS = [
    re.compile(r"(?:答案|正确答案|正确选项)\s*(?:是|为|应选|应该选)\s*[：:]?\s*([A-Ea-e](?:[,，、\s]*[A-Ea-e])*)"),
    re.compile(r"(?:综上|因此|所以)[^。]{0,30}?(?:选|答案[是为]?)\s*([A-Ea-e](?:[,，、\s]*[A-Ea-e])*)"),
]
# ── English ──
_EN_THE_ANSWER_RE = re.compile(r"the\s+answer\s+is\s*\*?\*?\(?([A-Za-z])\)?", re.IGNORECASE)
# ── 最后手段（仅对短文本启用，避免在长文本中误抓分析段的字母） ──
_LAST_LETTER_RE = re.compile(r"\b([A-Za-z])\b")

# 短文本阈值：仅当全文 < 此长度时才使用 last_letter fallback
_SHORT_TEXT_THRESHOLD = 200


def _normalize_choice_letters(raw: str) -> str:
    """从原始捕获组中提取所有选项字母，排序去重后拼接（无分隔符）。

    例：'B,C,D,E' → 'BCDE'；'ACD' → 'ACD'；'A、C、D' → 'ACD'
    """
    letters = re.findall(r"[A-Ea-e]", raw)
    if letters:
        return "".join(sorted({c.upper() for c in letters}))
    return ""


def _extract_choice(text: str, extractor: str, regex: str | None) -> str:
    """从模型回复中抽取选项字母（支持单选和多选）。

    返回格式：排序去重的大写字母拼接，如 "B" / "BCDE" / "ACD"。
    """
    if not text:
        return ""

    if extractor == "regex":
        if not regex:
            raise ValueError("extractor=regex 需要 extractor_regex")
        m = re.search(regex, text)
        if m:
            return _normalize_choice_letters(m.group(1))
        return ""
    if extractor == "en_the_answer":
        m = _EN_THE_ANSWER_RE.search(text)
        return m.group(1).upper() if m else ""
    if extractor == "last_letter":
        found = _LAST_LETTER_RE.findall(text)
        return found[-1].upper() if found else ""

    # default: cn_final_answer（增强版，支持多选）
    # 策略 1：行首 "答案：X" — 最可靠，医学/金融选择题标准格式
    m = _CN_ANSWER_LINE_RE.search(text)
    if m:
        return _normalize_choice_letters(m.group(1))

    # 策略 2："最终答案是/为/：X"
    m = _CN_FINAL_RE.search(text)
    if m:
        return _normalize_choice_letters(m.group(1))

    # 策略 3：变体表述（"正确答案为X" / "综上…选X"）
    for pat in _CN_ANSWER_VARIANT_PATTERNS:
        m = pat.search(text)
        if m:
            return _normalize_choice_letters(m.group(1))

    # 策略 4：最后手段 — 仅对短文本启用，避免长解析中误抓
    if len(text) < _SHORT_TEXT_THRESHOLD:
        found = _LAST_LETTER_RE.findall(text)
        if found:
            return found[-1].upper()

    return ""


# ============================================================================
# Accuracy
# ============================================================================
class Accuracy(Metric):
    name = "accuracy"

    def compute(self, sample: Sample, output: RunOutput, judge=None, **_kwargs) -> MetricResult:
        gt_raw = sample.ground_truth.answer
        gt_str = (str(gt_raw) if gt_raw is not None else "").strip().upper()
        # 标准化 GT：提取字母并排序去重（兼容 "BCDE" / "B,C,D,E" / "B" 等格式）
        gt = _normalize_choice_letters(gt_str) if gt_str else ""
        pred = _extract_choice(output.final_text, self.spec.extractor, self.spec.extractor_regex)
        ok = 1.0 if pred and gt and pred == gt else 0.0
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
