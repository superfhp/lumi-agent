"""PDFRetriever：smart / full 抽取 + 脱敏 + 缓存。

- 复用 for_eval/analyse_report.py 与 for_eval/report_generate.py 的策略。
- 缓存 key = (abs_path, mtime, mode, keywords_hash, max_chars/max_pages, redaction_profile)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CACHE_ROOT = Path(__file__).resolve().parents[1] / "outputs" / "_cache" / "pdf"


def _redact(text: str, mapping: Dict[str, str]) -> str:
    if not mapping:
        return text
    items = sorted(mapping.items(), key=lambda x: -len(x[0]))
    for old, new in items:
        text = text.replace(old, new)
    return text


def _cache_key(path: Path, mode: str, params: dict) -> Path:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        mtime = 0
    payload = {
        "path": str(path.resolve()),
        "mtime": mtime,
        "mode": mode,
        "params": params,
    }
    h = hashlib.md5(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return CACHE_ROOT / f"{path.stem}.{mode}.{h}.md"


class PDFRetriever:
    def __init__(self, redaction_profiles: Optional[Dict[str, Dict[str, str]]] = None):
        self.profiles = redaction_profiles or {}

    def _mapping(self, profile: Optional[str]) -> Dict[str, str]:
        if not profile:
            return {}
        return self.profiles.get(profile, {}) or {}

    # ----------------------------------------------------------------------
    def extract_full(self, path: str | Path, max_pages: int = 50,
                     redaction_profile: Optional[str] = None) -> str:
        p = Path(path)
        cache = _cache_key(p, "full", {"max_pages": max_pages, "rp": redaction_profile})
        if cache.exists():
            return cache.read_text(encoding="utf-8")

        text = self._read_full(p, max_pages)
        text = _redact(text, self._mapping(redaction_profile))
        cache.write_text(text, encoding="utf-8")
        return text

    def extract_smart(self, path: str | Path, keywords: List[str],
                      window: Tuple[int, int] = (-1, 3),
                      max_chars: int = 28000,
                      redaction_profile: Optional[str] = None) -> str:
        p = Path(path)
        params = {
            "kw": sorted(list(keywords)),
            "win": list(window),
            "max_chars": max_chars,
            "rp": redaction_profile,
        }
        cache = _cache_key(p, "smart", params)
        if cache.exists():
            return cache.read_text(encoding="utf-8")

        text = self._read_smart(p, keywords, window, max_chars)
        text = _redact(text, self._mapping(redaction_profile))
        cache.write_text(text, encoding="utf-8")
        return text

    # ----------------------------------------------------------------------
    @staticmethod
    def _read_full(p: Path, max_pages: int) -> str:
        try:
            import fitz  # PyMuPDF
            with fitz.open(str(p)) as doc:
                parts = []
                for i, page in enumerate(doc):
                    if i >= max_pages:
                        break
                    t = page.get_text() or ""
                    if t.strip():
                        parts.append(f"\n[Page {i+1}]\n{t}")
                return "".join(parts)
        except ImportError:
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(p))
                pages = reader.pages[:max_pages]
                return "\n\n".join(f"[Page {i+1}]\n{(pg.extract_text() or '')}" for i, pg in enumerate(pages))
            except Exception:
                return p.read_text(encoding="utf-8", errors="ignore")[: max_pages * 2000]

    @staticmethod
    def _read_smart(p: Path, keywords: List[str], window: Tuple[int, int],
                    max_chars: int) -> str:
        kws_lower = [k.lower() for k in keywords]
        try:
            import fitz
            with fitz.open(str(p)) as doc:
                total = doc.page_count
                hit_pages = set()
                for i, page in enumerate(doc):
                    txt = (page.get_text() or "").lower()
                    if any(kw in txt for kw in kws_lower):
                        for off in range(window[0], window[1] + 1):
                            j = i + off
                            if 0 <= j < total:
                                hit_pages.add(j)
                buf: List[str] = []
                length = 0
                for pg in sorted(hit_pages):
                    t = doc[pg].get_text() or ""
                    chunk = f"\n--- Page {pg+1} ---\n{t}"
                    buf.append(chunk)
                    length += len(chunk)
                    if length > max_chars:
                        break
                return "".join(buf) if buf else ""
        except ImportError:
            # 退化：当作纯文本前若干字符
            return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
