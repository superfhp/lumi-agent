"""RVEC 打标结果报告生成器

将 fin_rvec / med_rvec 打标输出的 CSV/JSONL 文件转换为自包含 HTML 可视化报告。

报告模块：
1. 总览卡片（模型数/样本数/均分/P分布）
2. 模型排行榜（多模型对比表）
3. 场景分布（M/F轴 饼图/矩形图）
4. RVEC 问题维度热力图（R/V/E/C 聚合）
5. 高频问题信号 TOP10
6. P 等级分布（堆叠柱状图）
7. 典型 Badcase 展示（score=0/1 的样本详情）
8. AI 归因分析（可选，需 LLM）

用法：
  # 独立调用
  python report_generator.py --input output/samples_rvec_labeled.csv --output reports/

  # 作为 run_tagging.py 的后置步骤（--report 参数）
  python run_tagging.py --mode med_rvec --input data.csv --auto-detect --report
"""

import os
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# 数据聚合
# ════════════════════════════════════════════════════════════

def _safe_int(v, default=0):
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def aggregate_report_data(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从打标结果聚合报告所需的统计数据"""

    # 检测模型列
    model_field = None
    for candidate in ["model", "model_name", "norm_model_name", "experiment_name"]:
        if records and records[0].get(candidate):
            model_field = candidate
            break

    # 按模型分组
    by_model = defaultdict(list)
    for r in records:
        model = str(r.get(model_field, "unknown")).strip() if model_field else "all"
        if not model:
            model = "unknown"
        by_model[model].append(r)

    # 检测场景字段（med_scene / fin_scene）
    scene_field = None
    for candidate in ["label_med_scene", "label_fin_scene"]:
        if records and any(str(r.get(candidate, "")).strip() for r in records[:20]):
            scene_field = candidate
            break

    domain = "medical" if scene_field == "label_med_scene" else "finance"

    # ── 辅助：提取问题文本 ──
    def _get_question(r):
        for col in ["question", "题目", "input", "prompt", "query", "问题"]:
            v = str(r.get(col, "")).strip()
            if v and v != "nan":
                return v
        return ""

    # ── 辅助：提取回答文本 ──
    def _get_answer(r):
        for col in ["prediction", "model_response", "实际回答", "output", "answer", "response"]:
            v = str(r.get(col, "")).strip()
            if v and v != "nan":
                return v
        return ""

    # ── 辅助：提取参考答案 ──
    def _get_reference(r):
        for col in ["ground_truth", "参考答案", "expected_output", "reference", "gold"]:
            v = str(r.get(col, "")).strip()
            if v and v != "nan":
                return v
        return ""

    # ── 辅助：解析 rvec_all 标签列表 ──
    def _parse_rvec_tags(rvec_all_str):
        """将 label_rvec_all 字符串拆分为标签列表
        支持格式：
        - "R-FACT-1 事实错误：P0；R-UND-1 误解用户：P1"
        - "R-FACT-1 事实错误：P0; R-UND-1 误解用户：P1"
        - "R-FACT-1：P0；R-UND-1：P1"
        """
        if not rvec_all_str or rvec_all_str in ("NONE", "nan", "None", ""):
            return []
        # 统一分号：支持中文 ；和英文 ;
        import re
        parts = re.split(r"[；;]", rvec_all_str)
        tags = []
        for part in parts:
            part = part.strip()
            if part and part != "NONE":
                tags.append(part)
        return tags

    def _extract_tag_code(tag_str):
        """从标签字符串中提取编码（如 R-FACT-1、V-USE-2 等）"""
        import re
        m = re.match(r"([RVEC]-[A-Z]+-\d+)", tag_str)
        if m:
            return m.group(1)
        # 去掉后面的 ：P0 / ：P1 等
        clean = re.split(r"[：:]P\d", tag_str)[0].strip()
        return clean

    # ── 全局统计 ──
    all_scores = [_safe_int(r.get("label_score")) for r in records if r.get("label_score") not in (None, "", "nan", "None")]
    all_severities = [str(r.get("label_severity", "NONE")).strip() for r in records
                      if str(r.get("label_severity", "")).strip() not in ("", "nan", "None")]

    global_stats = {
        "total_records": len(records),
        "total_models": len(by_model),
        "avg_score": round(sum(all_scores) / len(all_scores), 2) if all_scores else 0,
        "score_dist": dict(Counter(all_scores)),
        "severity_dist": dict(Counter(all_severities)),
        "domain": domain,
        "scene_field": scene_field,
    }

    # ── 按模型统计 ──
    model_stats = {}
    for model_name, model_records in by_model.items():
        scores = [_safe_int(r.get("label_score")) for r in model_records if r.get("label_score") not in (None, "", "nan", "None")]
        severities = [str(r.get("label_severity", "NONE")).strip() for r in model_records
                      if str(r.get("label_severity", "")).strip() not in ("", "nan", "None")]

        # RVEC 标签聚合
        rvec_counter = Counter()
        rvec_examples = defaultdict(list)
        for r in model_records:
            rvec_all = str(r.get("label_rvec_all", ""))
            tags = _parse_rvec_tags(rvec_all)
            for tag in tags:
                code = _extract_tag_code(tag)
                rvec_counter[tag] += 1
                if len(rvec_examples[code]) < 2:
                    rvec_examples[code].append({
                        "question": _get_question(r)[:150],
                        "evidence": str(r.get("label_evidence", ""))[:200],
                        "score": r.get("label_score", ""),
                    })

        # 场景分布
        scene_counter = Counter()
        for r in model_records:
            scene = str(r.get(scene_field, "")).strip() if scene_field else ""
            if scene and scene not in ("", "nan", "None", "NONE"):
                # 提取编码部分（如 "M01 内科..." → "M01"）
                code = scene.split(" ")[0] if " " in scene else scene[:3]
                scene_counter[code] += 1

        # 任务分布
        task_counter = Counter()
        for r in model_records:
            task = str(r.get("label_task_type", "")).strip()
            if task and task not in ("", "nan", "None", "NONE"):
                code = task.split(" ")[0] if " " in task else task[:3]
                task_counter[code] += 1

        # RVEC 维度聚合（R/V/E/C） — 基于 code 前缀
        dim_counter = {"R": 0, "V": 0, "E": 0, "C": 0}
        for tag, cnt in rvec_counter.items():
            code = _extract_tag_code(tag)
            if code:
                first_char = code[0].upper()
                if first_char in dim_counter:
                    dim_counter[first_char] += cnt

        # 高频 tag 汇总（去掉 P 等级后缀，按纯 code+名称聚合）
        tag_clean_counter = Counter()
        for tag, cnt in rvec_counter.items():
            import re
            # "R-FACT-1 事实错误：P0" → "R-FACT-1 事实错误"
            clean = re.split(r"[：:]P\d", tag)[0].strip()
            if clean:
                tag_clean_counter[clean] += cnt

        model_stats[model_name] = {
            "total": len(model_records),
            "avg_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "score_dist": dict(Counter(scores)),
            "severity_dist": dict(Counter(severities)),
            "p0": severities.count("P0"),
            "p1": severities.count("P1"),
            "p2": severities.count("P2"),
            "rvec_top10": tag_clean_counter.most_common(10),
            "rvec_dim": dim_counter,
            "scene_dist": dict(scene_counter.most_common(10)),
            "task_dist": dict(task_counter.most_common(10)),
            "rvec_examples": dict(rvec_examples),
        }

    # ── Badcase 样本 ──
    badcases = []
    for r in records:
        score = _safe_int(r.get("label_score"), default=-1)
        if score <= 1 and score >= 0:
            badcases.append({
                "model": r.get(model_field, "unknown") if model_field else "all",
                "question": _get_question(r)[:300],
                "answer": _get_answer(r)[:300],
                "reference": _get_reference(r)[:200],
                "score": score,
                "severity": str(r.get("label_severity", "")),
                "rvec_primary": str(r.get("label_rvec_primary", "")),
                "evidence": str(r.get("label_evidence", "")),
                "reason": str(r.get("label_reason", "")),
                "scene": str(r.get(scene_field, "")) if scene_field else "",
            })
    # 取前 20 个 badcase
    badcases = badcases[:20]

    return {
        "global": global_stats,
        "models": model_stats,
        "badcases": badcases,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ════════════════════════════════════════════════════════════
# HTML 生成
# ════════════════════════════════════════════════════════════

def generate_html_report(data: Dict[str, Any], output_path: str, title: str = "RVEC 打标分析报告"):
    """生成自包含 HTML 报告"""
    g = data["global"]
    models = data["models"]
    badcases = data["badcases"]
    ts = data["generated_at"]
    domain_label = "医学" if g["domain"] == "medical" else "金融"

    # ── 模型排行榜 ──
    sorted_models = sorted(models.items(), key=lambda x: -x[1]["avg_score"])
    ranking_rows = ""
    for i, (name, s) in enumerate(sorted_models, 1):
        color = "#059669" if s["avg_score"] >= 3.5 else "#2563EB" if s["avg_score"] >= 2.5 else "#D97706" if s["avg_score"] >= 1.5 else "#DC2626"
        # 提取 TOP3 标签作为快速预览
        top_tags = [tag for tag, _ in s["rvec_top10"][:3]]
        top_tags_html = ", ".join(f'<span class="mini-tag">{_esc(t)}</span>' for t in top_tags) if top_tags else '<span style="color:#aaa">—</span>'
        # RVEC 维度快览
        dim = s["rvec_dim"]
        dim_summary = f'R:{dim.get("R",0)} V:{dim.get("V",0)} E:{dim.get("E",0)} C:{dim.get("C",0)}'
        ranking_rows += f"""<tr>
            <td>{i}</td>
            <td style="font-weight:600">{_esc(name)}</td>
            <td style="color:{color};font-weight:700;font-size:18px">{s['avg_score']}</td>
            <td>{s['total']}</td>
            <td style="color:#DC2626;font-weight:600">{s['p0']}</td>
            <td style="color:#D97706;font-weight:600">{s['p1']}</td>
            <td style="color:#2563EB">{s['p2']}</td>
            <td style="font-size:11px;color:var(--text2)">{dim_summary}</td>
            <td style="font-size:11px">{top_tags_html}</td>
        </tr>"""

    # ── 各模型详情 ──
    model_sections = ""
    for name, s in sorted_models:
        # 分数分布条
        score_bars = ""
        score_colors = ["#DC2626", "#D97706", "#F59E0B", "#2563EB", "#059669"]
        for score in range(5):
            count = s["score_dist"].get(score, 0)
            pct = count / s["total"] * 100 if s["total"] else 0
            score_bars += f'<div class="score-bar-item"><div class="score-bar" style="width:{pct}%;background:{score_colors[score]}"></div><span>{score}分: {count}条 ({pct:.0f}%)</span></div>'

        # RVEC 维度
        rvec_dim = s["rvec_dim"]
        dim_total = sum(rvec_dim.values())
        max_dim = max(rvec_dim.values(), default=1) or 1
        dim_bars = ""
        dim_colors = {"R": "#DC2626", "V": "#D97706", "E": "#2563EB", "C": "#059669"}
        dim_names = {"R": "R 可信性", "V": "V 有用性", "E": "E 体验", "C": "C 亮点"}
        if dim_total > 0:
            for dim in ["R", "V", "E", "C"]:
                cnt = rvec_dim.get(dim, 0)
                pct = cnt / max_dim * 100
                dim_bars += f'<div class="dim-row"><span class="dim-label">{dim_names[dim]}</span><div class="dim-bar-wrap"><div class="dim-bar" style="width:{pct}%;background:{dim_colors[dim]}"></div></div><span class="dim-count">{cnt}</span></div>'
        else:
            dim_bars = '<p style="color:#aaa;font-size:13px">暂无 RVEC 标签数据</p>'

        # TOP 问题标签
        tag_rows = ""
        if s["rvec_top10"]:
            for tag, cnt in s["rvec_top10"][:7]:
                tag_rows += f'<tr><td class="tag-cell">{_esc(tag)}</td><td>{cnt}</td></tr>'
        else:
            tag_rows = '<tr><td colspan="2" style="color:#aaa;font-size:12px">暂无标签（可能所有样本均通过）</td></tr>'

        # 场景分布
        scene_items = ""
        if s["scene_dist"]:
            for code, cnt in list(s["scene_dist"].items())[:6]:
                scene_items += f'<span class="chip">{_esc(code)}: {cnt}</span>'
        else:
            scene_items = '<span style="color:#aaa;font-size:12px">未检测到场景标签</span>'

        # 任务分布
        task_items = ""
        if s["task_dist"]:
            for code, cnt in list(s["task_dist"].items())[:6]:
                task_items += f'<span class="chip">{_esc(code)}: {cnt}</span>'

        # 模型分数对应的颜色
        model_color = "#059669" if s["avg_score"] >= 3.5 else "#2563EB" if s["avg_score"] >= 2.5 else "#D97706" if s["avg_score"] >= 1.5 else "#DC2626"

        model_sections += f"""
        <div class="model-detail" id="model-{_slug(name)}">
            <h3>📌 {_esc(name)} <span class="score-badge" style="background:{model_color}">{s['avg_score']}分</span></h3>
            <div class="detail-grid">
                <div class="detail-card">
                    <h4>📊 分数分布</h4>
                    <div class="score-bars">{score_bars}</div>
                </div>
                <div class="detail-card">
                    <h4>🎯 RVEC 维度</h4>
                    {dim_bars}
                </div>
                <div class="detail-card">
                    <h4>🏷️ 高频问题标签 TOP7</h4>
                    <table class="tag-table">{tag_rows}</table>
                </div>
                <div class="detail-card">
                    <h4>🗂️ 场景分布</h4>
                    <div class="chip-wrap">{scene_items}</div>
                    {'<h4 style="margin-top:12px">📋 任务分布</h4><div class="chip-wrap">' + task_items + '</div>' if task_items else ''}
                </div>
            </div>
        </div>"""

    # ── Badcase 列表 ──
    badcase_html = ""
    for i, bc in enumerate(badcases[:15], 1):
        badcase_html += f"""
        <div class="badcase-card">
            <div class="bc-header">
                <span class="bc-num">#{i}</span>
                <span class="bc-model">{_esc(bc['model'])}</span>
                <span class="bc-score" style="background:{'#DC2626' if bc['score']==0 else '#D97706'}">{bc['score']}分 · {_esc(bc['severity'])}</span>
                <span class="bc-tag">{_esc(bc['rvec_primary'])}</span>
            </div>
            <div class="bc-body">
                <div class="bc-field"><strong>❓ 题目：</strong>{_esc(bc['question'])}</div>
                <div class="bc-field"><strong>🤖 模型回答：</strong>{_esc(bc['answer'])}</div>
                <div class="bc-field"><strong>✅ 参考：</strong>{_esc(bc['reference'])}</div>
                <div class="bc-field bc-evidence"><strong>🔍 证据：</strong>{_esc(bc['evidence'])}</div>
                <div class="bc-field"><strong>💬 理由：</strong>{_esc(bc['reason'])}</div>
            </div>
        </div>"""

    # ── 全局分数分布 ──
    global_score_dist = g["score_dist"]
    global_bars = ""
    total = g["total_records"] or 1
    for score in range(5):
        count = global_score_dist.get(score, 0)
        pct = count / total * 100
        colors = ["#DC2626", "#D97706", "#F59E0B", "#2563EB", "#059669"]
        global_bars += f'<div class="ov-score-item" style="--pct:{pct}%;--color:{colors[score]}"><div class="ov-score-bar"></div><div class="ov-score-label">{score}分<br>{count}条<br>{pct:.0f}%</div></div>'

    # ── 组装 HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f4f6fb; --card:#fff; --border:#e4e8ef; --text:#1a1d23; --text2:#5f6577; --blue:#4361ee; --red:#ef476f; --orange:#f77f00; --green:#06d6a0; --purple:#7209b7; --radius:12px; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Inter',-apple-system,'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }}
.container {{ max-width:1400px; margin:0 auto; padding:32px 24px; }}
h1 {{ font-size:28px; font-weight:800; background:linear-gradient(135deg,var(--blue),var(--purple)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:4px; }}
h2 {{ font-size:20px; font-weight:700; margin:40px 0 16px; padding-bottom:8px; border-bottom:2px solid var(--blue); }}
h3 {{ font-size:18px; margin-bottom:12px; }}
.subtitle {{ color:var(--text2); font-size:13px; margin-bottom:32px; }}

/* 总览卡片 */
.overview {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:32px; }}
.ov-card {{ background:var(--card); border-radius:var(--radius); padding:20px 28px; box-shadow:0 2px 12px rgba(0,0,0,0.06); text-align:center; min-width:120px; flex:1; }}
.ov-label {{ font-size:12px; color:var(--text2); text-transform:uppercase; letter-spacing:0.5px; }}
.ov-val {{ font-size:32px; font-weight:800; margin-top:4px; }}

/* 排行榜 */
.ranking-table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:var(--radius); overflow:hidden; box-shadow:0 2px 12px rgba(0,0,0,0.06); }}
.ranking-table th {{ background:#1a237e; color:white; padding:12px 16px; text-align:center; font-size:13px; }}
.ranking-table td {{ padding:12px 16px; text-align:center; border-bottom:1px solid #f0f2f8; }}
.ranking-table tr:hover {{ background:#f8f9fc; }}

/* 模型详情 */
.model-detail {{ background:var(--card); border-radius:var(--radius); padding:24px; margin-bottom:24px; box-shadow:0 2px 12px rgba(0,0,0,0.06); }}
.detail-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
.detail-card {{ background:#f8f9fc; border-radius:10px; padding:16px; }}
.detail-card h4 {{ font-size:14px; margin-bottom:10px; color:var(--text2); }}

/* 分数条 */
.score-bars {{ display:flex; flex-direction:column; gap:6px; }}
.score-bar-item {{ display:flex; align-items:center; gap:8px; font-size:12px; }}
.score-bar {{ height:18px; border-radius:4px; min-width:2px; transition:width 0.3s; }}
.score-bar-item span {{ min-width:90px; color:var(--text2); }}

/* RVEC 维度 */
.dim-row {{ display:flex; align-items:center; gap:8px; margin:6px 0; }}
.dim-label {{ width:80px; font-size:13px; font-weight:600; }}
.dim-bar-wrap {{ flex:1; background:#e8eaf0; height:16px; border-radius:4px; overflow:hidden; }}
.dim-bar {{ height:100%; border-radius:4px; transition:width 0.3s; }}
.dim-count {{ width:30px; text-align:right; font-size:12px; font-weight:700; }}

/* 标签表 */
.tag-table {{ width:100%; font-size:13px; border-collapse:collapse; }}
.tag-table td {{ padding:4px 8px; border-bottom:1px solid #eee; }}
.tag-cell {{ max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}

/* Chip */
.chip-wrap {{ display:flex; flex-wrap:wrap; gap:6px; }}
.chip {{ background:#e8eaf0; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:500; }}

/* 分数 badge */
.score-badge {{ display:inline-block; color:white; padding:2px 10px; border-radius:12px; font-size:13px; margin-left:8px; }}

/* 全局分数分布 */
.ov-score-row {{ display:flex; gap:8px; align-items:flex-end; height:120px; margin:16px 0; }}
.ov-score-item {{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; }}
.ov-score-bar {{ width:100%; height:var(--pct); background:var(--color); border-radius:6px 6px 0 0; min-height:4px; transition:height 0.3s; }}
.ov-score-label {{ text-align:center; font-size:11px; color:var(--text2); margin-top:4px; }}

/* Badcase */
.badcase-card {{ background:var(--card); border-radius:var(--radius); padding:16px 20px; margin-bottom:12px; box-shadow:0 2px 8px rgba(0,0,0,0.05); border-left:4px solid var(--red); }}
.bc-header {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap; }}
.bc-num {{ font-weight:800; color:var(--text2); }}
.bc-model {{ font-weight:600; color:var(--blue); }}
.bc-score {{ color:white; padding:2px 10px; border-radius:10px; font-size:12px; font-weight:600; }}
.bc-tag {{ background:#FEE2E2; color:#DC2626; padding:2px 10px; border-radius:10px; font-size:12px; }}
.bc-body {{ font-size:13px; line-height:1.8; }}
.bc-field {{ margin:4px 0; }}
.bc-evidence {{ background:#FEF3C7; padding:6px 10px; border-radius:6px; margin-top:6px; }}

.footer {{ text-align:center; color:var(--text2); padding:40px 0 20px; font-size:12px; }}

/* Mini tag in ranking table */
.mini-tag {{ background:#EDE9FE; color:#5B21B6; padding:1px 6px; border-radius:6px; font-size:10px; white-space:nowrap; }}

@media (max-width:768px) {{
  .overview {{ flex-direction:column; }}
  .detail-grid {{ grid-template-columns:1fr; }}
  .ranking-table {{ font-size:12px; }}
}}
</style>
</head><body>
<div class="container">
    <h1>📊 {title}</h1>
    <div class="subtitle">生成时间：{ts} · {g['total_models']} 个模型 · {g['total_records']} 条数据 · {domain_label}领域</div>

    <!-- 总览 -->
    <div class="overview">
        <div class="ov-card"><span class="ov-label">评测样本</span><span class="ov-val">{g['total_records']}</span></div>
        <div class="ov-card"><span class="ov-label">模型数</span><span class="ov-val" style="color:var(--blue)">{g['total_models']}</span></div>
        <div class="ov-card"><span class="ov-label">平均分</span><span class="ov-val" style="color:{'var(--green)' if g['avg_score']>=3 else 'var(--orange)' if g['avg_score']>=2 else 'var(--red)'}">{g['avg_score']}</span></div>
        <div class="ov-card"><span class="ov-label">P0 (致命)</span><span class="ov-val" style="color:var(--red)">{g['severity_dist'].get('P0',0)}</span></div>
        <div class="ov-card"><span class="ov-label">P1 (严重)</span><span class="ov-val" style="color:var(--orange)">{g['severity_dist'].get('P1',0)}</span></div>
        <div class="ov-card"><span class="ov-label">P2 (轻微)</span><span class="ov-val" style="color:var(--blue)">{g['severity_dist'].get('P2',0)}</span></div>
    </div>

    <!-- 全局分数分布 -->
    <h2>📈 整体分数分布</h2>
    <div class="ov-score-row">{global_bars}</div>

    <!-- 排行榜 -->
    <h2>🏆 模型排行榜</h2>
    <table class="ranking-table">
        <tr><th>#</th><th>模型</th><th>均分</th><th>样本数</th><th>P0</th><th>P1</th><th>P2</th><th>RVEC维度</th><th>高频标签 TOP3</th></tr>
        {ranking_rows}
    </table>

    <!-- 各模型详情 -->
    <h2>🔍 各模型详情分析</h2>
    {model_sections}

    <!-- Badcase -->
    <h2>🚨 典型问题样本（score ≤ 1）</h2>
    {badcase_html if badcase_html else '<p style="color:var(--text2)">🎉 无严重问题样本（所有条目均分 ≥ 2）</p>'}

    <div class="footer">
        RVEC 打标分析报告 · eval-dataset-tagging skill · {ts}
    </div>
</div>
</body></html>"""

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"📊 报告已生成: {output_path}")
    return output_path


def _esc(s: str) -> str:
    """HTML 转义"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _slug(s: str) -> str:
    """生成 HTML ID 友好的 slug"""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "-", s)[:30]


# ════════════════════════════════════════════════════════════
# 快捷入口：从打标结果文件直接生成报告
# ════════════════════════════════════════════════════════════

def generate_report_from_file(input_path: str, output_dir: str = None,
                               title: str = None) -> str:
    """从打标结果文件生成 HTML 报告

    Args:
        input_path: 打标结果文件路径（csv/jsonl/xlsx）
        output_dir: 报告输出目录（默认与输入文件同目录）
        title: 报告标题（默认根据文件名生成）

    Returns:
        生成的 HTML 文件路径
    """
    # 导入数据读取
    import sys
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from io_utils import read_data

    records = read_data(input_path)
    if not records:
        raise ValueError(f"文件为空或无法读取: {input_path}")

    data = aggregate_report_data(records)

    if not title:
        stem = Path(input_path).stem
        domain = data["global"]["domain"]
        title = f"{'医学' if domain == 'medical' else '金融'} RVEC 打标分析报告 — {stem}"

    # 默认输出目录：与输入文件同目录
    if not output_dir:
        output_dir = str(Path(input_path).resolve().parent)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"rvec_report_{ts}.html")
    return generate_html_report(data, output_path, title)


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="RVEC 打标结果 → HTML 分析报告")
    parser.add_argument("--input", required=True, help="打标结果文件（csv/jsonl/xlsx）")
    parser.add_argument("--output", default=None, help="输出目录（默认: 与输入文件同目录，或 ../output）")
    parser.add_argument("--title", default=None, help="报告标题")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 默认输出目录：优先输入文件所在目录，其次 ../output
    output_dir = args.output
    if not output_dir:
        input_dir = str(Path(args.input).resolve().parent)
        output_dir = input_dir

    path = generate_report_from_file(args.input, output_dir, args.title)
    print(f"\n{'='*60}")
    print(f"✅ 报告生成完成: {path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
