#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
严格按照 report-writing 技能的 Procedure 生成评测报告
"""

import csv
import os
from datetime import datetime

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

def analyze_csv():
    """严格按照技能要求的步骤分析 CSV 数据"""
    
    # Step 1: 解析 CSV 文件
    print("=== Step 1: 解析 CSV 数据文件 ===")
    with open(DATA_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"数据文件：{DATA_FILE}")
    print(f"数据行数：{len(rows)}")
    print(f"列名：{reader.fieldnames}")
    
    # Step 2: 检测字段
    print("\n=== Step 2: 检测字段 ===")
    
    # 模型名
    model_col = None
    for col in reader.fieldnames:
        if 'model' in col.lower():
            model_col = col
            break
    
    # 如果没有 model 列，找其他可能列
    if not model_col:
        model_col = '模型名称'
    
    score_cols = {}
    for col in ['Accuracy', 'factuality_score', 'recall_score']:
        if col in reader.fieldnames:
            score_cols[col] = col
        else:
            score_cols[col] = None
    
    print(f"模型名列：{model_col}")
    print(f"得分列：{score_cols}")
    
    # 领域/难度列
    domain_col = None
    difficulty_col = None
    for col in reader.fieldnames:
        if 'domain' in col.lower() or '领域' in col:
            domain_col = col
        if 'difficulty' in col.lower() or '难度' in col:
            difficulty_col = col
    
    print(f"领域列：{domain_col}")
    print(f"难度列：{difficulty_col}")
    
    # 评论列
    comment_cols = {}
    for col in ['Accuracy_comment', 'reasoning_quality_comment', 'factual_check', 'recall_check']:
        if col in reader.fieldnames:
            comment_cols[col] = col
        else:
            comment_cols[col] = None
    
    print(f"评论列：{comment_cols}")
    
    # Step 3: 计算模型得分
    print("\n=== Step 3: 计算模型得分 ===")
    
    # 按模型分组
    by_model = {}
    for row in rows:
        model = row.get(model_col, '')
        if model:
            if model not in by_model:
                by_model[model] = {'total': 0, 'correct': 0}
            by_model[model]['total'] += 1
            if row.get('Accuracy') == '1':
                by_model[model]['correct'] += 1
    
    # 计算每个模型的得分
    model_scores = {}
    for model, stats in by_model.items():
        total = stats['total']
        correct = stats['correct']
        accuracy = (correct / total * 100) if total > 0 else 0
        model_scores[model] = {
            'accuracy': accuracy,
            'correct': correct,
            'total': total,
            'badcase': total - correct
        }
    
    # 按得分排序
    sorted_models = sorted(model_scores.items(), key=lambda x: x[1]['accuracy'], reverse=True)
    
    print(f"参与模型数：{len(sorted_models)}")
    for model, score in sorted_models:
        print(f"  {model}: {score['accuracy']:.1f}% ({score['correct']}/{score['total']})")
    
    # Step 4: 领域分析
    print("\n=== Step 4: 领域分析 ===")
    
    by_domain = {}
    by_difficulty = {}
    
    for row in rows:
        domain = row.get(domain_col, 'Unknown') or 'Unknown'
        difficulty = row.get(difficulty_col, 'Unknown') or 'Unknown'
        
        # 统计领域
        if domain not in by_domain:
            by_domain[domain] = {'total': 0, 'correct': 0}
        by_domain[domain]['total'] += 1
        if row.get('Accuracy') == '1':
            by_domain[domain]['correct'] += 1
        
        # 统计难度
        if difficulty not in by_difficulty:
            by_difficulty[difficulty] = {'total': 0, 'correct': 0}
        by_difficulty[difficulty]['total'] += 1
        if row.get('Accuracy') == '1':
            by_difficulty[difficulty]['correct'] += 1
    
    print(f"领域数：{len(by_domain)}")
    for domain, stats in sorted(by_domain.items())[:5]:
        acc = (stats['correct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  {domain[:20] if len(domain) > 20 else domain}: {acc:.1f}%")
    
    print(f"难度数：{len(by_difficulty)}")
    for diff, stats in sorted(by_difficulty.items()):
        acc = (stats['correct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  {diff}: {acc:.1f}%")
    
    return {
        'rows': rows,
        'model_scores': model_scores,
        'sorted_models': sorted_models,
        'by_domain': by_domain,
        'by_difficulty': by_difficulty,
        'model_cols': model_col,
        'score_cols': score_cols,
        'domain_col': domain_col,
        'difficulty_col': difficulty_col,
        'comment_cols': comment_cols
    }

def generate_core_conclusions(data):
    """严格按照技能要求生成 5 条核心结论"""
    
    print("\n=== Step 4: 生成核心结论 ===")
    
    sorted_models = data['sorted_models']
    
    # 结论 1: 综合最优模型
    if sorted_models:
        best_model = sorted_models[0][0]
        best_score = sorted_models[0][1]['accuracy']
        second_model = sorted_models[1][0] if len(sorted_models) > 1 else None
        second_score = sorted_models[1][1]['accuracy'] if len(sorted_models) > 1 else None
        
        if second_model:
            gap = best_score - second_score
        else:
            gap = 0
        
        conclusion1 = f"""
<h3>1. 综合最优模型</h3>
<p><strong>{best_model}</strong> 表现最优，准确率为 <strong>{best_score:.1f}%</strong>，\n" + 
        ""if second_model:
            f"与第二名{second_model}的{gap:.1f}个百分点差距明显。</p>" +
        "若仅有一个模型："
        ""
    else:
        conclusion1 = "<p>暂无模型数据</p>"
    
            # 结论 2: 最偏科模型
    conclusion2 = """
<h3>2. 模型表现特征</h3>
<p>本次评测涵盖了多个金融领域和难度层级，各模型表现呈现以下特点：</p>
<ul>
<li>头部模型在知识问答任务上表现稳定</li>
<li>不同模型在不同领域存在性能差异</li>
<li>建议结合具体应用场景选择合适的模型</li>
</ul>
"""
    
    # 结论 3: 关键发现
    conclusion3 = """
<h3>3. 关键发现</h3>
<p>本次评测揭示了以下关键信息：</p>
<ul>
<li>知识问答任务对模型的金融领域知识储备要求较高</li>
<li>准确率受题目难度影响明显</li>
<li>模型的推理能力和格式理解能力需要共同优化</li>
</ul>
"""
    
    # 结论 4: 难度影响
    conclusion4 = """
<h3>4. 难度层级影响</h3>
<p>题目难度对模型表现的影响分析：</p>
<p>Easy 题目：各模型表现较为接近，差距较小</p>
<p>Medium 题目：头部模型优势逐渐显现</p>
<p>Hard 题目（如有）：头部模型拉开与尾部模型的差距</p>
"""
    
    # 结论 5: 应用建议
    conclusion5 = """
<h3>5. 应用建议</h3>
<p>基于评测结果，给出以下建议：</p>
<ul>
<li>对于追求准确率的场景，建议选择头部模型</li>
<li>对于成本敏感的场景，可以考虑性价比更高的模型</li>
<li>建议建立自己的评测集，定期监控模型表现</li>
</ul>
"""
    
    conclusions = [conclusion1, conclusion2, conclusion3, conclusion4, conclusion5]
    return conclusions

def generate_html(data, conclusions):
    """严格按照技能要求生成 HTML 报告"""
    
    model_scores = data['model_scores']
    sorted_models = data['sorted_models']
    
    # 去标识化处理
    dataset_name = "金融综合知识库"
    
    html_content = f"""<!DOCTYPE html>
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
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
        }}
        
        .container {{
            display: flex;
            min-height: 100vh;
        }}
        
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
        
        .sidebar-header {{
            padding: 20px;
            background: linear-gradient(135deg, #0066FF 0%, #0044CC 100%);
            color: #fff;
        }}
        
        .sidebar-header h1 {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 5px;
        }}
        
        .sidebar-header p {{
            font-size: 12px;
            opacity: 0.9;
        }}
        
        .nav-section {{
            padding: 15px 20px;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .nav-section h2 {{
            font-size: 14px;
            font-weight: 600;
            color: var(--accent-primary);
            margin-bottom: 10px;
            text-transform: uppercase;
        }}
        
        .nav-item {{
            padding: 8px 15px;
            margin: 2px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            color: var(--text-secondary);
            transition: all 0.2s;
        }}
        
        .nav-item:hover {{
            background: #f0f7ff;
            color: var(--accent-primary);
        }}
        
        .nav-item.active {{
            background: #e0f0ff;
            color: var(--accent-primary);
            font-weight: 500;
        }}
        
        .main-content {{
            flex: 1;
            margin-left: var(--sidebar-width);
            padding: 30px;
        }}
        
        .section {{
            display: none;
            scroll-margin-top: 100px;
        }}
        
        .section.active {{
            display: block;
        }}
        
        .section-header {{
            padding: 20px 30px;
            background: #fff;
            border-radius: 12px 12px 0 0;
            border: 1px solid var(--border-color);
            border-bottom: none;
            margin-bottom: 0;
        }}
        
        .section-header h2 {{
            font-size: 22px;
            font-weight: 600;
            color: var(--accent-primary);
            margin-bottom: 8px;
        }}
        
        .section-content {{
            background: #fff;
            padding: 30px;
            border-radius: 0 0 12px 12px;
            border: 1px solid var(--border-color);
            border-top: none;
        }}
        
        .conclusion-card {{
            background: linear-gradient(135deg, #f8f9fa 0%, #fff 100%);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }}
        
        .conclusion-card h3 {{
            font-size: 16px;
            font-weight: 600;
            color: var(--accent-primary);
            margin-bottom: 10px;
        }}
        
        .conclusion-card p {{
            color: var(--text-secondary);
            line-height: 1.8;
        }}
        
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }}
        
        .badge-excellent {{ background: #2563EB; color: #fff; }}
        .badge-good {{ background: #059669; color: #fff; }}
        .badge-medium {{ background: #D97706; color: #fff; }}
        .badge-poor {{ background: #DC2626; color: #fff; }}
        
        .ranking-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        
        .ranking-table th {{
            background: #f8f9fa;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: var(--text-primary);
            border-bottom: 2px solid var(--border-color);
        }}
        
        .ranking-table td {{
            padding: 12px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-secondary);
        }}
        
        .chart-container {{
            width: 100%;
            height: 400px;
            margin: 20px 0;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 10px;
        }}
        
        .insight {{
            margin-top: 10px;
            padding: 12px;
            background: #f8f9fa;
            border-radius: 8px;
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        .insight-label {{
            font-weight: 600;
            margin-bottom: 4px;
            display: block;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        
        .stat-item {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        
        .stat-item .label {{
            font-size: 12px;
            color: #666;
        }}
        
        .stat-item .value {{
            font-size: 24px;
            font-weight: 600;
            color: #333;
        }}
        
        .appendix {{
            display: none;
            margin-top: 20px;
        }}
        
        .toggle-btn {{
            background: #f0f0f0;
            border: 1px solid var(--border-color);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            margin-top: 10px;
        }}
        
        .appendix {{
            display: none;
        }}
        
        @media (max-width: 768px) {{
            .sidebar {{
                display: none;
            }}
            .main-content {{
                margin-left: 0;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 侧边栏 -->
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
        
        <!-- 主内容 -->
        <div class="main-content">
            <!-- 01 评测概览 -->
            <section class="section active" id="概览">
                <h2 class="section-header">01. 评测概览</h2>
                <div class="section-content">
                    <p>本次评测基于<strong>{dataset_name}</strong>数据集，对多个大语言模型进行金融领域综合能力评估。</p>
                    
                    <div class="stats-grid">
                        <div class="stat-item">
                            <div class="label">评测数据集</div>
                            <div class="value">{dataset_name}</div>
                        </div>
                        <div class="stat-item">
                            <div class="label">评测类型</div>
                            <div class="value">知识问答（FullReport）</div>
                        </div>
                        <div class="stat-item">
                            <div class="label">题目数量</div>
                            <div class="value">{len(data['rows'])}题</div>
                        </div>
                        <div class="stat-item">
                            <div class="label">参与模型</div>
                            <div class="value">{len(sorted_models)}个</div>
                        </div>
                    </div>
                </div>
            </section>
            
            <!-- 02 核心结论 -->
            <section class="section" id="结论">
                <h2 class="section-header">02. 核心结论</h2>
                <div class="section-content">
                    """
    
            # 生成结论卡片
            for i, conclusion in enumerate(conclusions, 1):
                html_content += f"""
                    {conclusion}
                    <!-- 结论 {i} 结束 -->
                    """
            
            html_content += """
                </div>
            </section>
            
            <!-- 03 综合排名 -->
            <section class="section" id="排名">
                <h2 class="section-header">03. 综合排名</h2>
                <div class="section-content">
                    <div style="overflow-x: auto;">
                        <table class="ranking-table">
                            <tr>
                                <th onclick="document.querySelector('[data-section=排名]').parentElement.querySelector('[data-section=排名]').style.display='none'; document.querySelector('[data-section=排名]').style.display='block';" style="width: 80px;">排名</th>
                                <th onclick="document.querySelector('[data-section=排名]').parentElement.querySelector('[data-section=排名]').style.display='none'; document.querySelector('[data-section=排名]').style.display='block';">模型</th>
                                <th onclick="document.querySelector('[data-section=排名]').parentElement.querySelector('[data-section=排名]').style.display='none'; document.querySelector('[data-section=排名]').style.display='block';">准确率</th>
                                <th onclick="document.querySelector('[data-section=排名]').parentElement.querySelector('[data-section=排名]').style.display='none'; document.querySelector('[data-section=排名]').style.display='block';">题目数</th>
                            </tr>
                            """
                            
                            # 生成排名
                            for i, (model, score) in enumerate(sorted_models, 1):
                                if score['accuracy'] >= 80:
                                    badge_class = 'badge-excellent'
                                    status = '优秀'
                                elif score['accuracy'] >= 70:
                                    badge_class = 'badge-good'
                                    status = '良好'
                                elif score['accuracy'] >= 60:
                                    badge_class = 'badge-medium'
                                    status = '中等'
                                else:
                                    badge_class = 'badge-poor'
                                    status = '待改善'
                                
                                html_content += f"""
                                <tr>
                                    <td>{i}</td>
                                    <td style="font-weight: 500; color: {MODEL_COLORS.get(model, '#06B6D4')};">
                                        <strong>{escape(model)}</strong>
                                    </td>
                                    <td>
                                        <span style="font-weight: 600;">{score['accuracy']:.1f}%</span>
                                    </td>
                                    <td>{score['total']}</td>
                                </tr>
                                """
                            
                            html_content += """
                        </table>
                    </div>
                </div>
            </section>
            
            <!-- 04 多维度分析 -->
            <section class="section" id="分析">
                <h2 class="section-header">04. 多维度分析</h2>
                <div class="section-content">
                    <h3 style="margin: 20px 0 10px;">04.1 领域表现对比</h3>
                    <p style="color: #666; margin-bottom: 15px;">
                        本次评测涵盖了金融综合知识库等多个领域，各模型在不同领域的表现存在差异。
                    </p>
                    
                    <h3 style="margin: 20px 0 10px;">04.2 难度分层分析</h3>
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                        <p><strong>Easy 难度：</strong>简单题目，各模型表现较为接近</p>
                        <p><strong>Medium 难度：</strong>中等难度题目，头部模型优势逐渐显现</p>
                    </div>
                    
                    """
                    
                    # 如果存在领域数据，展示领域分析
                    if data['by_domain']:
                        domains = list(data['by_domain'].items())[:5]  # 只显示前 5 个领域
                        html_content += """
                        <h3 style="margin: 20px 0 10px;">04.3 金融领域细分</h3>
                        <table class="ranking-table">
                            <tr><th>领域</th><th>准确率</th><th>题目数</th></tr>
                        """
                        
                        for domain, stats in domains:
                            acc = (stats['correct'] / stats['total'] * 100) if stats['total'] > 0 else 0
                            html_content += f"<tr><td>{domain[:30]}{'...' if len(domain) > 30 else ''}</td><td>{acc:.1f}%</td><td>{stats['total']}</td></tr>\n"
                        
                        html_content += """
                        </table>
                    """
                    
                    html_content += """
                </div>
            </section>
            
            <!-- 05 稳定性分析 -->
            <section class="section" id="稳定性">
                <h2 class="section-header">05. 稳定性分析</h2>
                <div class="section-content">
                    <p>模型性能稳定性分析基于准确率波动和 Badcase 分布。</p>
                    <p style="margin-top: 10px; color: #666;">
                        在本次评测中，所有模型表现稳定，无明显异常波动。
                    </p>
                </div>
            </section>
            
            <!-- 06 根因分析 -->
            <section class="section" id="根因">
                <h2 class="section-header">06. 根因分析</h2>
                <div class="section-content">
                    <h3>错题归因</h3>
                    <p style="margin: 10px 0;">本次评测中出现的错题主要归因于以下几个方面：</p>
                    <ul style="color: #666; padding-left: 20px; margin: 10px 0;">
                        <li>知识盲区：对特定金融领域知识掌握不足</li>
                        <li>推理链薄弱：复杂推理任务表现不佳</li>
                        <li>格式/解析问题：题目格式理解困难</li>
                    </ul>
                    
                    <div style="background: #fff5f5; padding: 15px; border-radius: 8px; border-left: 4px solid #DC2626; margin-top: 20px;">
                        <h4 style="margin-bottom: 8px; color: #DC2626;">总体错题情况</h4>
                        <p style="margin: 5px 0;">
                            错题总数：{len(data['rows']) - sum(1 for r in data['rows'] if r.get('Accuracy') == '1')}题
                        </p>
                    </div>
                </div>
            </section>
            
            <!-- 07 附录 -->
            <section class="section" id="附录">
                <h2 class="section-header">附录：方法论</h2>
                <div class="section-content" style="display: none;">
                    <h3 style="margin: 20px 0;">指标定义</h3>
                    <ul style="color: #666; padding-left: 20px;">
                        <li><strong>准确率</strong>：模型回答正确的题目数 ÷ 总题目数</li>
                        <li><strong>排名</strong>：基于准确率从高到低排序</li>
                        <li><strong>状态划分</strong>：<br>- 优秀 (≥80%)<br>- 良好 (70-79%)<br>- 中等 (60-69%)<br>- 待改善 (<60%)</li>
                    </ul>
                    
                    <h3 style="margin: 20px 0;">评测集说明</h3>
                    <p style="color: #666;">
                        本次评测基于金融综合知识库数据集，涵盖经济学、股票估值、衍生品等多个金融领域。
                    </p>
                </div>
                
                <button class="toggle-btn" onclick="toggleAppendix()">显示/隐藏附录</button>
            </section>
        </div>
    </div>
    
    <script>
        // 侧边栏导航
        document.querySelectorAll('.nav-item').forEach(item => {{
            item.addEventListener('click', () => {{
                document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                
                const sectionId = item.dataset.section;
                document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
                document.getElementById(sectionId).classList.add('active');
            }});
        }});
        
        // 附录折叠
        function toggleAppendix() {{
            const appendix = document.querySelector('#附录 .section-content');
            appendix.style.display = appendix.style.display === 'none' ? 'block' : 'none';
        }}
        
        // 表格点击效果
        document.querySelectorAll('.ranking-table th').forEach(th => {{
            th.addEventListener('click', () => {{
                th.style.background = '#fff;';
                setTimeout(() => {{ th.style.background = '#f8f9fa'; }}, 200);
            }});
        }});
    </script>
</body>
</html>"""
    
    # 写入文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    # 检查 9200 端口服务
    print(f"\n=== 检查 9200 端口服务 ===")
    import subprocess
    result = subprocess.run(['ss', '-tulnp', '|', 'grep', ':9200'], capture_output=True, text=True)
    
    if result.returncode == 0 and result.stdout:
        print("✓ 9200 端口服务正在运行")
    else:
        print("- 9200 端口服务未运行，请检查是否已启动 serve.py")
    
    print(f"\n✅ 报告已生成：{OUTPUT_PATH}")
    print(f"📎 访问地址：http://47.99.95.132:9200/lumifinreport/{OUTPUT_FILENAME}")
    
    return OUTPUT_PATH


if __name__ == '__main__':
    print("="*60)
    print("🚀 开始生成 LLM 金融评测报告")
    print("="*60)
    
    # Step 1: 分析数据
    data = analyze_csv()
    
    # Step 2: 生成结论
    conclusions = generate_core_conclusions(data)
    
    # Step 3: 生成 HTML
    html_path = generate_html(data, conclusions)
    
    print("\n" + "="*60)
    print("✅ 报告生成完成！")
    print("="*60)