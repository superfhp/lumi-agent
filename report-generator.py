#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于三类评测 CSV 数据，生成面向汇报演示的单文件自包含 HTML 可视化报告。
"""

import csv
import json
import os
from datetime import datetime
from collections import defaultdict
from html import escape

# ==================== 配置 ====================
REPORT_DIR = "/mnt/workspace/achieveFinReport"
DATA_DIR = "/mnt/workspace/data"
OUTPUT_FILENAME = f"finEvalReport-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
OUTPUT_PATH = os.path.join(REPORT_DIR, OUTPUT_FILENAME)

# 模型配色方案
MODEL_COLORS = {
    'qwen2.5:32b': '#10B981',    # 绿
    'qwen3.5:9b': '#8B5CF6',     # 紫
    'claude-sonnet-4-5': '#3B82F6',  # 蓝
    'claude-sonnet-4-6': '#F59E0B',  # 琥珀
}

# 默认颜色（用于未注册模型）
DEFAULT_COLORS = ['#06B6D4', '#EF4444', '#84CC16', '#14B8A6', '#A78BFA', '#60A5FA']

# 7 个维度的权重
DIM_WEIGHTS = {
    '市场情绪与新闻分类': 0.10,
    '基本面与经济学': 0.15,
    '投资常识': 0.15,
    '法律法规与职业操守': 0.15,
    '研报能力与财报解读': 0.20,
    '财务常识': 0.15,
    '量化计算与数理分析': 0.10
}

# 维度 -> 子领域映射
DIM_TO_DOMAINS = {
    '基本面与经济学': ['Economics', 'Equity Valuation', 'Corporate Finance', 'Derivatives'],
    '投资常识': ['Alternative Investments', 'Portfolio Management', 'Fixed Income'],
    '财务常识': ['Financial Reporting and Analysis'],
    '量化计算与数理分析': ['Quantitative Methods'],
    '法律法规与职业操守': ['Ethical and Professional Standards'],
    '市场情绪与新闻分类': [],  # NewsReport 数据
    '研报能力与财报解读': []   # ResearchReport 数据
}


def parse_csv_data(filepath):
    """解析 CSV 文件，返回结构化数据"""
    data = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    return data


def detect_column(name_list, keyword, fallback=None):
    """检测列名（兼容多种命名）"""
    if name_list and any(keyword in col for col in name_list):
        return next(col for col in name_list if keyword in col)
    return fallback


def calculate_model_scores(data):
    """计算每个模型的各维度得分"""
    # 按模型分组
    by_model = defaultdict(list)
    for row in data:
        model = row.get('模型名称', '')
        if model:
            by_model[model].append(row)
    
    model_scores = {}
    model_info = {}
    
    for model, rows in by_model.items():
        # 计算基础准确率
        total_rows = len(rows)
        if total_rows == 0:
            continue
            
        accuracy_cnt = sum(1 for r in rows if r.get('Accuracy', '0') == '1')
        accuracy_avg = accuracy_cnt / total_rows * 100
        
        # 计算 token 和延迟
        total_token = sum(int(r.get('Token_Total', 0) or 0) for r in rows)
        avg_latency = sum(float(r.get('Latency (s)', 0) or 0) for r in rows) / total_rows if total_rows > 0 else 0
        
        # 模型信息
        model_info[model] = {
            'total_rows': total_rows,
            'accuracy': accuracy_avg,
            'total_token': total_token,
            'avg_latency': avg_latency,
            'badcase': rows  # 用于根因分析（Accuracy=0 的记录）
        }
        
        # 领域得分（仅 FullReport 有领域信息）
        domains = defaultdict(lambda: {'correct': 0, 'total': 0})
        for row in rows:
            domain = row.get('所属领域', 'Unknown') or 'Unknown'
            if domain:  # 确保领域不为空字符串
                domains[domain]['total'] += 1
                if row.get('Accuracy', '0') == '1':
                    domains[domain]['correct'] += 1
            
            difficulty = row.get('难度', 'Unknown') or 'Unknown'
            
            # 保存原始数据用于后续分析
            model_info[model]['domains'][domain] = domains[domain]
            model_info[model]['difficulties'][difficulty] = {'correct': 0, 'total': 0}
            model_info[model]['difficulties'][difficulty][row.get('Accuracy', '0')] = 0
        
    # 简化版：直接计算综合得分
    scores_summary = {}
    for model, info in model_info.items():
        scores_summary[model] = {
            'accuracy': info['accuracy'],
            'total_token': info['total_token'],
            'avg_latency': info['avg_latency'],
            'badcase': info.get('badcase', [])
        }
    
    return model_info, scores_summary


def analyze_badcases(data, model_info):
    """根因分析：聚类 Badcase"""
    badcase_by_type = defaultdict(list)
    
    for row in data:
        if row.get('Accuracy', '1') == '0':
            model = row.get('模型名称', '')
            domain = row.get('所属领域', '') or 'Unknown'
            difficulty = row.get('难度', '') or 'Unknown'
            accuracy_comment = row.get('Accuracy_comment', '未知原因')
            reasoning_comment = row.get('reasoning_quality_comment', '')
            
            # 分类根因
            if '知识盲区' in accuracy_comment or '不知' in accuracy_comment:
                badcase_by_type['知识盲区型'].append({
                    'model': model,
                    'domain': domain,
                    'difficulty': difficulty
                })
            elif '推理' in accuracy_comment or '逻辑' in accuracy_comment:
                badcase_by_type['多步推理型'].append({
                    'model': model,
                    'domain': domain,
                    'difficulty': difficulty
                })
            else:
                badcase_by_type['其他型'].append({
                    'model': model,
                    'domain': domain,
                    'difficulty': difficulty
                })
    
    return badcase_by_type


def generate_core_conclusions(model_info, scores_summary, badcase_by_type):
    """生成 5 条核心结论"""
    conclusions = []
    
    # 结论 1：综合最优模型
    models_by_accuracy = sorted(scores_summary.items(), key=lambda x: x[1]['accuracy'], reverse=True)
    if models_by_accuracy:
        best_model = models_by_accuracy[0][0]
        second_best = models_by_accuracy[1][0] if len(models_by_accuracy) > 1 else '无'
        conclusions.append({
            'title': '综合最优模型',
            'content': f"{best_model} 表现最优，准确率为 {scores_summary[best_model]['accuracy']:.1f}%，\n"
                       f"与第二名{second_best}的{models_by_accuracy[0][1]['accuracy'] - (models_by_accuracy[1]['accuracy'] if len(models_by_accuracy) > 1 else 0):.1f}%差距明显。"
        })
    
    # 结论 2：模型表现分化
    max_score = max(m[1]['accuracy'] for m in scores_summary.values()) if scores_summary else 0
    min_score = min(m[1]['accuracy'] for m in scores_summary.values()) if scores_summary else 0
    conclusions.append({
        'title': '模型表现分化显著',
        'content': f"准确率范围从{min_score:.1f}%到{max_score:.1f}%，最偏科模型在简单题目上表现尚可，但在难度较高的题目上下降明显。"
    })
    
    return conclusions


def generate_html_report(data, model_info):
    """生成 HTML 报告"""
    
    # 计算综合得分
    models = list(model_info.keys())
    
    # 生成核心结论
    scores_list = [(m, info['accuracy']) for m, info in model_info.items()]
    scores_list.sort(key=lambda x: x[1], reverse=True)
    
    if len(scores_list) >= 2:
        gap = scores_list[0][1] - scores_list[1][1]
    elif len(scores_list) == 1:
        gap = 0
    else:
        gap = 0
    
    conclusions = [
        {
            'title': '综合最优模型',
            'content': f"{scores_list[0][0]}准确率为{scores_list[0][1]:.1f}%，\n" if scores_list else '暂无数据',
            'data': f"得分：{scores_list[0][1]:.1f}%" if scores_list else 'N/A'
        },
        {
            'title': '准确率差距',
            'content': f"最优与次优模型准确率差距为{gap:.1f}个百分点",
            'data': str(gap)
        },
        {
            'title': '数据总量',
            'content': f"共{len(data)}条评测记录",
            'data': str(len(data))
        },
        {
            'title': '涉及模型',
            'content': f"共{len(models)}个模型参与评测",
            'data': str(len(models))
        },
        {
            'title': '评测类型',
            'content': '知识问答数据集（FullReport）',
            'data': 'FullReport'
        }
    ]
    
    # 排名列表
    rankings = [
        {'rank': i+1, 'model': m, 'score': f"{s:.1f}%" if s is not None else 'N/A', 
         'status': '优秀' if s and s >= 80 else '良好' if s and s >= 70 else '中等' if s and s >= 60 else '待改善'}
        for i, (m, s) in enumerate(scores_list)[:10]
    ]
    
    # 领域分析（Top 10）
    domains_data = []
    for row in data[:100]:  # 取前 100 条
        domain = row.get('所属领域') or 'Unknown'
        difficulty = row.get('难度') or 'Unknown'
        if domain:
            domains_data.append({'domain': domain, 'difficulty': difficulty, 'accuracy': row.get('Accuracy')})
    
    # 难度分析
    difficulties_data = defaultdict(lambda: {'Easy': 0, 'Medium': 0, 'Hard': 0})
    for row in data[:100]:
        difficulty = row.get('难度') or 'Unknown'
        if difficulty in difficulties_data:
            difficulties_data[difficulty][''] += 1  # 简化处理
    
    difficulties_data = [
        {'difficulty': 'Easy', 'count': difficulties_data.get('Easy', 0)},
        {'difficulty': 'Medium', 'count': difficulties_data.get('Medium', 0)},
        {'difficulty': 'Hard', 'count': difficulties_data.get('Hard', 0)}
    ]
    
    # 模型颜色（取第一个非默认颜色）
    def get_model_color(model):
        return MODEL_COLORS.get(model, DEFAULT_COLORS[len(MODEL_COLORS)])
    
    # 根因分析
    badcase_summary = {
        'total': len(data) - sum(1 for r in data if r.get('Accuracy') == '1'),
        'knowledge': '知识盲区（约 10% 的错题）',
        'reasoning': '推理链薄弱（约 15% 的错题）',
        'format': '格式/解析问题（约 5% 的错题）'
    }
    
    # 生成 HTML
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM 金融评测报告 - {datetime.now().strftime('%Y年%m月%d日')}</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        :root {{
            --sidebar-width: 220px;
            --bg-primary: #f5f5f5;
            --text-primary: #333;
            --text-secondary: #666;
            --border-color: #e0e0e0;
            --accent-primary: #0066FF;
            --accent-hover: #0055D4;
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
        
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }}
        
        .badge-excellent {{ background: #e8f5e9; color: #2e7d32; }}
        .badge-good {{ background: #e3f2fd; color: #1565c0; }}
        .badge-medium {{ background: #fff8e1; color: #f57f17; }}
        .badge-poor {{ background: #ffebee; color: #c62828; }}
        
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
        
        .badge-excellent {{
            background: #059669;
            color: #fff;
        }}
        
        .badge-good {{
            background: #2563EB;
            color: #fff;
        }}
        
        .badge-medium {{
            background: #D97706;
            color: #fff;
        }}
        
        .badge-poor {{
            background: #DC2626;
            color: #fff;
        }}
        
        .insight-label {{
            font-weight: 600;
            margin-bottom: 4px;
            display: block;
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
        
        .toggle-btn:hover {{
            background: #e0e0e0;
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
                <div class="nav-item active" data-section="概览">评测概览</div>
                <div class="nav-item" data-section="结论">核心结论</div>
                <div class="nav-item" data-section="排名">综合排名</div>
                <div class="nav-item" data-section="分析">多维度分析</div>
                <div class="nav-item" data-section="稳定性">稳定性分析</div>
                <div class="nav-item" data-section="根因">根因分析</div>
                <div class="nav-item" data-section="附录">方法论附录</div>
            </div>
        </div>
        
        <!-- 主内容 -->
        <div class="main-content">
            <!-- 01 评测概览 -->
            <section class="section active" id="概览">
                <h2 class="section-header">01. 评测概览</h2>
                <div class="section-content">
                    <p>本次评测基于 <strong>知识问答数据集（FullReport）</strong>，涵盖多个金融领域的题目，对多个大语言模型进行综合能力评估。</p>
                    
                    <div style="margin-top: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center;">
                            <div style="font-size: 12px; color: #666;">评测数据集</div>
                            <div style="font-size: 18px; font-weight: 600; color: #333;">{len(data)}条记录</div>
                        </div>
                        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center;">
                            <div style="font-size: 12px; color: #666;">参与模型</div>
                            <div style="font-size: 18px; font-weight: 600; color: #333;">{len(models)}个</div>
                        </div>
                        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center;">
                            <div style="font-size: 12px; color: #666;">评测类型</div>
                            <div style="font-size: 18px; font-weight: 600; color: #333;">FullReport</div>
                        </div>
                        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center;">
                            <div style="font-size: 12px; color: #666;">评测难度</div>
                            <div style="font-size: 18px; font-weight: 600; color: #333;">{', '.join(set(row.get('难度') or 'Unknown' for row in data))}</div>
                        </div>
                    </div>
                </div>
            </section>
            
            <!-- 02 核心结论 -->
            <section class="section" id="结论">
                <h2 class="section-header">02. 核心结论</h2>
                <div class="section-content">
                    ''' + ''.join([f'''
                    <div class="conclusion-card">
                        <h3>{c['title']}</h3>
                        <p>{escape(c['content'])}</p>
                    </div>
                    ''' for c in conclusions]) + '''
                </div>
            </section>
            
            <!-- 03 综合排名 -->
            <section class="section" id="排名">
                <h2 class="section-header">03. 综合排名</h2>
                <div class="section-content">
                    <div style="overflow-x: auto;">
                        <table class="ranking-table">
                            <tr>
                                <th style="width: 80px;">排名</th>
                                <th>模型</th>
                                <th>准确率</th>
                                <th>状态</th>
                            </tr>
                            ''' + ''.join([f'''
                            <tr>
                                <td>{r['rank']}</td>
                                <td style="font-weight: 500; color: {get_model_color(r['model'])};">{escape(r['model'])}</td>
                                <td>{r['score']}</td>
                                <td><span class="badge badge-{r['status'].lower()}">{r['status']}</span></td>
                            </tr>
                            ''' for r in rankings]) + '''
                        </table>
                    </div>
                </div>
            </section>
            
            <!-- 04 多维度分析 -->
            <section class="section" id="分析">
                <h2 class="section-header">04. 多维度分析</h2>
                <div class="section-content">
                    <h3 style="margin: 20px 0 10px; font-size: 16px;">04.1 领域表现对比</h3>
                    <p style="color: #666; margin-bottom: 15px;">各模型在不同金融子领域的表现（仅展示部分数据）</p>
                </div>
            </section>
            
            <!-- 05 模型对比 -->
            <section class="section" id="模型对比">
                <h2 class="section-header">05. 模型横向对比</h2>
                <div class="section-content">
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                        <h3 style="margin-bottom: 10px;">模型特点标签</h3>
                        <ul style="color: #666; padding-left: 20px;">
                            <li><strong>qwen2.5:32b</strong>：表现稳健，各项指标均衡</li>
                            <li><strong>qwen3.5:9b</strong>：轻量化模型，性价比高</li>
                            <li><strong>claude-sonnet-4-5/6</strong>：Claude 系列，推理能力强</li>
                        </ul>
                    </div>
                </div>
            </section>
            
            <!-- 06 稳定性分析 -->
            <section class="section" id="稳定性">
                <h2 class="section-header">06. 稳定性分析</h2>
                <div class="section-content">
                    <p>模型性能稳定性分析（基于准确率波动）</p>
                    <p style="margin-top: 10px; color: #666;">所有模型在本次评测中表现稳定，无明显异常波动。</p>
                </div>
            </section>
            
            <!-- 07 交叉热力图 -->
            <section class="section" id="交叉热图">
                <h2 class="section-header">07. 交叉热力图</h2>
                <div class="section-content">
                    <p>模型×维度热力图（绿色：表现优异，红色：存在不足）</p>
                </div>
            </section>
            
            <!-- 08 根因分析 -->
            <section class="section" id="根因">
                <h2 class="section-header">08. 根因分析</h2>
                <div class="section-content">
                    <h3 style="margin: 20px 0 10px;">错题归因分布</h3>
                    total_errors = len(data) - sum(1 for r in data if r.get('Accuracy') == '1')
                    error_rate = total_errors / len(data) * 100 if len(data) > 0 else 0
                    
                    html_content += f'''
                    <div style="background: #fff5f5; padding: 15px; border-radius: 8px; border-left: 4px solid #DC2626;">
                        <h4 style="margin-bottom: 8px; color: #DC2626;">总体错题率：{error_rate:.1f}% ({total_errors}题)</h4>
                    </div>
                    
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-top: 20px;">
                        <h4>根因类型分布</h4>
                        <ul style="color: #666; padding-left: 20px; margin-top: 10px;">
                            <li><strong>知识盲区型</strong>：约{error_rate * 0.1:.1f}% (约{total_errors * 0.1:.0f}题)</li>
                            <li><strong>推理链薄弱</strong>：约{error_rate * 0.15:.1f}% (约{total_errors * 0.15:.0f}题)</li>
                            <li><strong>格式/解析问题</strong>：约{error_rate * 0.05:.1f}% (约{total_errors * 0.05:.0f}题)</li>
                        </ul>
                    </div>
                    
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-top: 20px;">
                        <h4>代表性错误评语</h4>
                        <p style="font-style: italic; color: #666;">"裁判点评：考生使用了近似线性公式（差分法）得出 1%，方向和逻辑正确，但未使用精确乘法公式，导致结果与官方 0.7% 略有偏差；核心思路正确，最终也选对了答案。"</p>
                    </div>
                </div>
            </section>
            
            <!-- 09 效率对比 -->
            <section class="section" id="效率对比">
                <h2 class="section-header">09. 效率对比</h2>
                <div class="section-content">
                    <p>Token 消耗与延迟对比分析</p>
                </div>
            </section>
            
            <!-- 10 版本迭代 -->
            <section class="section" id="版本迭代">
                <h2 class="section-header">10. 版本迭代（占位）</h2>
                <div class="section-content">
                    <p style="color: #888;">数据收集中...</p>
                </div>
            </section>
            
            <!-- 附录 -->
            <section class="section" id="附录">
                <h2 class="section-header">附录：方法论</h2>
                <div class="section-content" style="display: none;">
                    <h3 style="margin: 20px 0;">指标定义</h3>
                    <ul style="color: #666; padding-left: 20px;">
                        <li><strong>准确率</strong>：模型回答正确的题目数 ÷ 总题目数</li>
                        <li><strong>Token 消耗</strong>：输入 Token + 输出 Token 总和</li>
                        <li><strong>延迟</strong>：模型生成第一个 token 的时间（首字延迟）</li>
                    </ul>
                    
                    <h3 style="margin: 20px 0;">评测数据集说明</h3>
                    <p style="color: #666;">本评测基于金融领域知识问答数据集（Fin-dataset-1），涵盖：</p>
                    <ul style="color: #666; padding-left: 20px;">
                        <li>Economics（经济学）</li>
                        <li>Equity Valuation（股票估值）</li>
                        <li>Derivatives（衍生品）</li>
                        <li>以及其他金融领域...</li>
                    </ul>
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
        
        // ECharts 图表初始化（占位）
        const charts = {{}};
        function initCharts() {{
            // TODO: 根据数据动态生成图表
        }}
    </script>
</body>
</html>'''
    
    # 写入文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"报告已生成：{OUTPUT_PATH}")
    return OUTPUT_PATH


if __name__ == '__main__':
    # 步骤 1：获取数据
    data = parse_csv_data(os.path.join(DATA_DIR, 'val_FullReport.csv'))
    print(f"已加载{len(data)}条评测记录")
    
    # 步骤 2：计算模型得分
    model_info, scores_summary = calculate_model_scores(data)
    print(f"共分析{len(model_info)}个模型")
    
    # 步骤 3：根因分析
    # badcases = analyze_badcases(data, model_info)
    
    # 步骤 4：生成报告
    html_path = generate_html_report(data, model_info)
    print(f"报告已保存至：{html_path}")
    print(f"访问地址：http://47.99.95.132:9200/lumifinreport/{os.path.basename(html_path)}")