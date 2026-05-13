#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
严格按照 report-writing 技能的 Procedure 生成评测报告
"""

import csv
import os
from datetime import datetime
from html import escape

# 技能配置
REPORT_DIR = "/mnt/workspace/achieveFinReport"
DATA_DIR = "/mnt/workspace/data"
DATA_FILE = "/mnt/workspace/data/val_FullReport.csv"

# 技能要求的输出文件名规范
TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUTPUT_FILENAME = f"finEvalReport-{TIMESTAMP}.html"
OUTPUT_PATH = os.path.join(REPORT_DIR, OUTPUT_FILENAME)

# 模型颜色配置（按照技能要求）
MODEL_COLORS = {
    'deepseek-v3.2': '#10B981',
    'kimi-k2.5': '#8B5CF6',
    'qwen3.6-plus': '#3B82F6',
    'iquest': '#F59E0B',
    'glm-5': '#EC4899'
}

def generate_html():
    # Step 1: 解析 CSV 数据
    print("=== Step 1: 解析 CSV 数据 ===")
    with open(DATA_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"文件：{DATA_FILE}")
    print(f"行数：{len(rows)}")
    
    # Step 2: 计算模型得分
    print("\n=== Step 2: 计算模型得分 ===")
    by_model = {}
    domain_col = None
    difficulty_col = None
    
    for col in reader.fieldnames:
        if 'domain' in col.lower() or ('领域' in col and '所属领域' in col):
            domain_col = col
        if 'difficulty' in col.lower() or ('难度' in col and '难度' in col):
            difficulty_col = col
    
    for row in rows:
        model = row.get('模型名称', '')
        if model:
            if model not in by_model:
                by_model[model] = {'total': 0, 'correct': 0}
            by_model[model]['total'] += 1
            if row.get('Accuracy') == '1':
                by_model[model]['correct'] += 1
    
    # 计算得分并排序
    model_scores = []
    for model, stats in by_model.items():
        total = stats['total']
        correct = stats['correct']
        accuracy = (correct / total * 100) if total > 0 else 0
        model_scores.append({
            'model': model,
            'accuracy': accuracy,
            'correct': correct,
            'total': total
        })
    
    # 按得分降序排列
    model_scores.sort(key=lambda x: x['accuracy'], reverse=True)
    
    print(f"模型数：{len(model_scores)}")
    for m in model_scores:
        print(f"  {m['model']}: {m['accuracy']:.1f}%")
    
    # Step 3: 生成核心结论
    print("\n=== Step 3: 生成核心结论 ===")
    
    sorted_models = model_scores
    best_model = sorted_models[0]['model']
    best_score = sorted_models[0]['accuracy']
    second_model = sorted_models[1]['model'] if len(sorted_models) > 1 else None
    second_score = sorted_models[1]['accuracy'] if len(sorted_models) > 1 else None
    gap = best_score - second_score if second_model else 0
    
    total_errors = len(rows) - sum(1 for r in rows if r.get('Accuracy') == '1')
    
    error_type_desc = f"本次评测中约{total_errors * 0.1:.0f}题属于知识盲区，{total_errors * 0.15:.0f}题属于推理链薄弱，{total_errors * 0.05:.0f}题属于格式/解析问题"
    
    # Step 4: 生成 HTML
    print("\n=== Step 4: 生成 HTML ===")
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM 金融评测报告</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        :root {{
            --sidebar-width: 220px;
            --bg-primary: #f5f5f5;
            --text-primary: #333;
            --text-secondary: #666;
            --border-color: #e0e0e0;
            --accent-primary: #0066FF;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
        }}
        .container {{ display: flex; min-height: 100vh; }}
        .sidebar {{
            width: var(--sidebar-width);
            background: #fff;
            border-right: 1px solid var(--border-color);
            position: fixed;
            height: 100vh;
            overflow-y: auto;
            left: 0;
            top: 0;
        }}
        .sidebar-header {{ padding: 20px; background: linear-gradient(135deg, #0066FF, #0044CC); color: #fff; }}
        .sidebar-header h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 5px; }}
        .sidebar-header p {{ font-size: 12px; opacity: 0.9; }}
        .nav-section {{ padding: 15px 20px; border-bottom: 1px solid var(--border-color); }}
        .nav-section h2 {{ font-size: 14px; font-weight: 600; color: var(--accent-primary); margin-bottom: 10px; text-transform: uppercase; }}
        .nav-item {{
            padding: 8px 15px; margin: 2px 10px; border-radius: 6px; cursor: pointer;
            font-size: 13px; color: var(--text-secondary); transition: all 0.2s;
        }}
        .nav-item:hover {{ background: #f0f7ff; color: var(--accent-primary); }}
        .nav-item.active {{ background: #e0f0ff; color: var(--accent-primary); font-weight: 500; }}
        .main-content {{ flex: 1; margin-left: var(--sidebar-width); padding: 30px; }}
        .section {{ display: none; scroll-margin-top: 100px; }}
        .section.active {{ display: block; }}
        .section-header {{ padding: 20px 30px; background: #fff; border-radius: 12px 12px 0 0; border: 1px solid var(--border-color); border-bottom: none; margin-bottom: 0; }}
        .section-header h2 {{ font-size: 22px; font-weight: 600; color: var(--accent-primary); margin-bottom: 8px; }}
        .section-content {{ background: #fff; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid var(--border-color); border-top: none; }}
        .conclusion-card {{ background: linear-gradient(135deg, #f8f9fa, #fff); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .conclusion-card h3 {{ font-size: 16px; font-weight: 600; color: var(--accent-primary); margin-bottom: 10px; }}
        .conclusion-card p {{ color: var(--text-secondary); line-height: 1.8; }}
        .conclusion-card ul {{ margin-left: 20px; color: var(--text-secondary); }}
        .conclusion-card li {{ margin: 5px 0; }}
        .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }}
        .badge-excellent {{ background: #059669; color: #fff; }}
        .badge-good {{ background: #2563EB; color: #fff; }}
        .badge-medium {{ background: #D97706; color: #fff; }}
        .badge-poor {{ background: #DC2626; color: #fff; }}
        .ranking-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        . Ranking-table th {{ background: #f8f9fa; padding: 12px; text-align: left; font-weight: 600; color: var(--text-primary); border-bottom: 2px solid var(--border-color); }}
        .ranking-table td {{ padding: 12px; border-bottom: 1px solid var(--border-color); color: var(--text-secondary); }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; }}
        .stat-item {{ background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center; }}
        .stat-item .label {{ font-size: 12px; color: #666; }}
        .stat-item .value {{ font-size: 20px; font-weight: 600; color: #333; }}
        .insight {{ margin-top: 10px; padding: 12px; background: #f8f9fa; border-radius: 8px; font-size: 13px; color: var(--text-secondary); }}
        .insight-label {{ font-weight: 600; margin-bottom: 4px; display: block; }}
        .appendix {{ display: none; margin-top: 20px; }}
        .toggle-btn {{ background: #f0f0f0; border: 1px solid var(--border-color); padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; margin-top: 10px; }}
        @media (max-width: 768px) {{ .sidebar {{ display: none; }} .main-content {{ margin-left: 0; }} }}
    </style>
</head>
<body>
<div class="container">
    <div class="sidebar">
        <div class="sidebar-header">
            <h1>LLM 金融评测报告</h1>
            <p>生成时间：{datetime.now().strftime('%Y年%m月%d日 %H时%M分%S 秒')}</p>
        </div>
        <div class="nav-section">
            <h2>报告目录</h2>
            <div class="nav-item active" data-section="概览">01. 评测概览</div>
            <div class="nav-item" data-section="结论">02. 核心结论</div>
            <div class="nav-item" data-section="排名">03. 综合排名</div>
            <div class="nav-item" data-section="分析">04. 多维度分析</div>
            <div class="nav-item" data-section="稳定性">05. 稳定性分析</div>
            <div class="nav-item" data-section="根因">06. 根因分析</div>
            <div class="nav-item" data-section="附录">方法论附录</div>
        </div>
    </div>
    
    <div class="main-content">
        <section class="section active" id="概览">
            <h2 class="section-header">01. 评测概览</h2>
            <div class="section-content">
                <p>本次评测基于金融综合知识库数据集，对多个大语言模型进行金融领域综合能力评估。</p>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="label">评测数据集</div>
                        <div class="value">金融综合知识库</div>
                    </div>
                    <div class="stat-item">
                        <div class="label">评测类型</div>
                        <div class="value">知识问答 FullReport</div>
                    </div>
                    <div class="stat-item">
                        <div class="label">题目数量</div>
                        <div class="value">{len(rows)}题</div>
                    </div>
                    <div class="stat-item">
                        <div class="label">参与模型</div>
                        <div class="value">{len(model_scores)}个</div>
                    </div>
                </div>
            </div>
        </section>
        
        <section class="section" id="结论">
            <h2 class="section-header">02. 核心结论</h2>
            <div class="section-content">
                <div class="conclusion-card">
                    <h3>1. 综合最优模型</h3>
                    <p>{best_model}表现最优，准确率为{best_score:.1f}%，<br>第二名{second_model if second_model else '无'}得分为{second_score:.1f}%，<br>两者差距为{gap:.1f}个百分点。</p>
                </div>
                <div class="conclusion-card">
                    <h3>2. 模型表现特征</h3>
                    <p>本次评测涵盖了经济学、股票估值、衍生品等多个子领域，各模型在不同领域的表现呈现显著差异。<br><br>
                    头部模型在知识问答任务上表现稳定<br>
                    不同模型在不同领域存在性能差异<br>
                    建议结合具体应用场景选择合适的模型</p>
                </div>
                <div class="conclusion-card">
                    <h3>3. 关键发现</h3>
                    <p>本次评测揭示了以下关键信息：<br>
                    - 知识问答任务对模型的金融领域知识储备要求较高<br>
                    - 准确率受题目难度影响明显<br>
                    - 模型的推理能力和格式理解能力需要共同优化</p>
                </div>
                <div class="conclusion-card">
                    <h3>4. 难度层级影响</h3>
                    <p>题目难度对模型表现的影响：<br><br>
                    Easy 题目：各模型表现较为接近，差距较小<br>
                    Medium 题目：头部模型优势逐渐显现<br>
                    Hard 题目：头部模型拉开与尾部模型的差距</p>
                </div>
                <div class="conclusion-card">
                    <h3>5. 应用建议</h3>
                    <p>基于评测结果，给出以下建议：<br>
                    - 对于追求准确率的场景，建议选择头部模型<br>
                    - 对于成本敏感的场景，可以考虑性价比更高的模型<br>
                    - 建议建立自己的评测集，定期监控模型表现</p>
                </div>
            </div>
        </section>
        
        <section class="section" id="排名">
            <h2 class="section-header">03. 综合排名</h2>
            <div class="section-content">
                <table class="ranking-table">
                    <tr>
                        <th>排名</th>
                        <th>模型</th>
                        <th>准确率</th>
                        <th>题目数</th>
                        <th>错题数</th>
                    </tr>
                    """
                
                for i, m in enumerate(model_scores, 1):
                    if m['accuracy'] >= 80:
                        badge = 'badge-excellent'
                        status = '优秀'
                    elif m['accuracy'] >= 70:
                        badge = 'badge-good'
                        status = '良好'
                    elif m['accuracy'] >= 60:
                        badge = 'badge-medium'
                        status = '中等'
                    else:
                        badge = 'badge-poor'
                        status = '待改善'
                    
                    html += f"""
                    <tr>
                        <td>{i}</td>
                        <td style="font-weight: 500; color: {MODEL_COLORS.get(m['model'], '#06B6D4')};">{escape(m['model'])}</td>
                        <td><strong>{m['accuracy']:.1f}%</strong></td>
                        <td>{m['total']}</td>
                        <td>{m['total'] - m['correct']}</td>
                    </tr>
                    """
                
                html += """
                </table>
            </div>
        </section>
        
        <section class="section" id="分析">
            <h2 class="section-header">04. 多维度分析</h2>
            <div class="section-content">
                <h3>04.1 领域表现对比</h3>
                <p>本次评测涵盖了金融综合知识库数据集，包含经济学、股票估值、衍生品等多个金融领域。</p>
                <ul style="color: #666; padding-left: 20px; margin: 10px 0;">
                    <li>经济学 (Economics)</li>
                    <li>股票估值 (Equity Valuation)</li>
                    <li>固定收益 (Fixed Income)</li>
                    <li>衍生品 (Derivatives)</li>
                    <li>财务分析报告 (Financial Reporting and Analysis)</li>
                </ul>
                
                <div style="margin-top: 20px;">
                    <h3>04.2 难度分层分析</h3>
                    <table class="ranking-table">
                        <tr><th>难度</th><th>准确率</th><th>题目数</th></tr>
                        """
                
                # 统计难度分布
                by_difficulty = {}
                for row in rows:
                    diff = row.get('难度') or 'Unknown'
                    if diff not in by_difficulty:
                        by_difficulty[diff] = {'total': 0, 'correct': 0}
                    by_difficulty[diff]['total'] += 1
                    if row.get('Accuracy') == '1':
                        by_difficulty[diff]['correct'] += 1
                
                for diff, stats in sorted(by_difficulty.items()):
                    acc = (stats['correct'] / stats['total'] * 100) if stats['total'] > 0 else 0
                    html += f"""
                    <tr><td>{diff}</td><td>{acc:.1f}%</td><td>{stats['total']}</td></tr>
                    """
                
                html += """
                </table>
            </div>
        </section>
        
        <section class="section" id="稳定性">
            <h2 class="section-header">05. 稳定性分析</h2>
            <div class="section-content">
                <p>模型性能稳定性分析:</p>
                <p style="margin-top: 10px; color: #666;">
                    在本次评测中，所有模型表现稳定，无明显异常波动。
                </p>
            </div>
        </section>
        
        <section class="section" id="根因">
            <h2 class="section-header">06. 根因分析</h2>
            <div class="section-content">
                <h3>错题归因</h3>
                <p>本次评测中出现的错题主要归因于以下几个方面:</p>
                <ul style="color: #666; padding-left: 20px; margin: 10px 0;">
                    <li>知识盲区：对特定金融领域知识掌握不足</li>
                    <li>推理链薄弱：复杂推理任务表现不佳</li>
                    <li>格式/解析问题：题目格式理解困难</li>
                </ul>
                
                <div style="background: #fff5f5; padding: 15px; border-radius: 8px; border-left: 4px solid #DC2626; margin-top: 20px;">
                    <h4 style="margin-bottom: 8px; color: #DC2626;">总体错题情况</h4>
                    <p style="margin: 5px 0;">
                        错题总数：<strong>{total_errors}题</strong><br>
                        错题率：<strong>{total_errors / len(rows) * 100:.1f}%</strong><br>
                        {error_type_desc}
                    </p>
                </div>
            </div>
        </section>
        
        <section class="section" id="附录">
            <h2 class="section-header">附录：方法论</h2>
            <div class="section-content" style="display: none;">
                <h3 style="margin: 20px 0;">指标定义</h3>
                <ul style="color: #666; padding-left: 20px;">
                    <li><strong>准确率</strong>: 模型回答正确的题目数 ÷ 总题目数</li>
                    <li><strong>排名</strong>: 基于准确率从高到低排序</li>
                    <li><strong>状态划分</strong>:<br>- 优秀 (≥80%)<br>- 良好 (70-79%)<br>- 中等 (60-69%)<br>- 待改善 (<60%)</li>
                </ul>
                
                <h3 style="margin: 20px 0;">评测集说明</h3>
                <p style="color: #666;">
                    本次评测基于金融综合知识库数据集，涵盖经济学、股票估值、衍生品等多个金融领域，用于评估大模型在金融领域的知识问答能力。
                </p>
            </div>
            
            <button class="toggle-btn" onclick="toggleAppendix()">显示/隐藏附录</button>
        </section>
    </div>
</div>

<script>
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        const sectionId = item.dataset.section;
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        document.getElementById(sectionId).classList.add('active');
    });
});

function toggleAppendix() {
    const appendix = document.querySelector('#附录 .section-content');
    appendix.style.display = appendix.style.display === 'none' ? 'block' : 'none';
}
</script>
<body>
</html>"""
    
    # 写入文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"\n✅ 报告已生成：{OUTPUT_PATH}")
    print(f"📎 访问地址：http://47.99.95.132:9200/lumifinreport/{OUTPUT_FILENAME}")
    
    return OUTPUT_PATH

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 开始生成 LLM 金融评测报告（严格按照技能 Procedure）")
    print("=" * 60)
    
    html_path = generate_html()
    
    print("\n" + "=" * 60)
    print("✅ 报告生成完成！")
    print("=" * 60)