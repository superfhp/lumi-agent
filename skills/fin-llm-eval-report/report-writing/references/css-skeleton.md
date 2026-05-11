# CSS 骨架与侧边栏 JS

## HTML 文件头部模板

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大模型金融领域能力评测报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
/* ── CSS Custom Properties ── */
:root {
  --bg: #F8FAFC;
  --card: #FFFFFF;
  --sidebar-bg: #F1F5F9;
  --sidebar-w: 220px;
  --primary: #3B82F6;
  --primary-light: #EFF6FF;
  --text: #1E293B;
  --muted: #64748B;
  --border: #E2E8F0;
  --radius: 12px;
  --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,.08);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
       background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }

/* ── 布局 ── */
.layout { display: flex; min-height: 100vh; }

.sidebar {
  width: var(--sidebar-w);
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
  position: fixed; top: 0; left: 0; height: 100vh;
  overflow-y: auto; z-index: 100;
  padding: 24px 0;
}
.sidebar-logo {
  padding: 0 20px 20px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.sidebar-logo h1 { font-size: 13px; font-weight: 700; color: var(--text); line-height: 1.4; }
.sidebar-logo p  { font-size: 11px; color: var(--muted); margin-top: 3px; }

.nav-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 20px; cursor: pointer;
  color: var(--muted); font-size: 13px; font-weight: 500;
  border-left: 3px solid transparent;
  transition: all .15s;
  text-decoration: none;
}
.nav-item:hover { color: var(--primary); background: rgba(59,130,246,.06); }
.nav-item.active { color: var(--primary); background: var(--primary-light);
                   border-left-color: var(--primary); font-weight: 600; }
.nav-num { font-size: 11px; color: var(--muted); width: 18px; }

.main { margin-left: var(--sidebar-w); padding: 32px 40px; max-width: 1200px; }

/* ── 卡片 ── */
.card {
  background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 24px; margin-bottom: 24px;
}
.section-title {
  font-size: 20px; font-weight: 700; color: var(--text);
  margin-bottom: 20px; padding-bottom: 12px;
  border-bottom: 2px solid var(--primary);
  display: flex; align-items: center; gap: 8px;
}

/* ── 图表容器 ── */
.chart-wrap { position: relative; }
.chart-box  { width: 100%; }
.chart-insight {
  margin-top: 12px; padding: 12px 16px;
  background: #F0F9FF; border-left: 3px solid var(--primary);
  border-radius: 0 8px 8px 0; font-size: 13px; color: #0C4A6E;
  line-height: 1.7;
}

/* ── 指标卡片 ── */
.metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
.metric-card {
  background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 20px;
  display: flex; align-items: flex-start; gap: 14px;
}
.metric-icon {
  width: 44px; height: 44px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; flex-shrink: 0;
}
.metric-body .metric-name  { font-size: 13px; font-weight: 600; color: var(--text); }
.metric-body .metric-value { font-size: 24px; font-weight: 700; color: var(--primary); margin: 2px 0; }
.metric-body .metric-desc  { font-size: 11px; color: var(--muted); line-height: 1.5; }

/* ── 结论卡片 ── */
.conclusion-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.conclusion-card {
  background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 20px;
  border-top: 3px solid var(--primary);
}
.conclusion-card h4 { font-size: 14px; font-weight: 600; margin-bottom: 6px; color: var(--text); }
.conclusion-card p  { font-size: 13px; color: var(--muted); line-height: 1.6; }

/* ── 标签 ── */
.tag { display: inline-block; padding: 2px 8px; border-radius: 99px;
       font-size: 11px; font-weight: 600; margin: 2px; }
.tag-green  { background: #D1FAE5; color: #065F46; }
.tag-blue   { background: #DBEAFE; color: #1E3A8A; }
.tag-amber  { background: #FEF3C7; color: #92400E; }
.tag-red    { background: #FEE2E2; color: #7F1D1D; }
.tag-purple { background: #EDE9FE; color: #4C1D95; }
.tag-pink   { background: #FCE7F3; color: #831843; }

/* ── 占位区块 ── */
.coming-soon {
  border: 2px dashed var(--border); border-radius: var(--radius);
  padding: 40px; text-align: center; color: var(--muted);
}
.coming-soon h3 { font-size: 16px; color: var(--muted); margin-bottom: 8px; }

/* ── 附录折叠 ── */
details summary {
  cursor: pointer; font-weight: 600; font-size: 14px;
  padding: 12px 16px; background: var(--bg);
  border-radius: 8px; list-style: none;
  display: flex; align-items: center; gap: 8px;
}
details summary::-webkit-details-marker { display: none; }
details[open] summary { border-radius: 8px 8px 0 0; border-bottom: 1px solid var(--border); }
details .details-body { padding: 16px; background: var(--card); border-radius: 0 0 8px 8px; }

/* ── 响应式 ── */
@media (max-width: 768px) {
  .sidebar { width: 100%; height: auto; position: relative; }
  .main { margin-left: 0; padding: 16px; }
}
</style>
</head>
```

---

## 侧边栏导航模板

```html
<div class="layout">
  <nav class="sidebar">
    <div class="sidebar-logo">
      <h1>大模型金融能力<br>评测报告</h1>
      <p>2025 · 内部评测</p>
    </div>
    <a class="nav-item" href="#s01"><span class="nav-num">01</span>评测概览</a>
    <a class="nav-item" href="#s02"><span class="nav-num">02</span>核心结论</a>
    <a class="nav-item" href="#s03"><span class="nav-num">03</span>综合排名</a>
    <a class="nav-item" href="#s04"><span class="nav-num">04</span>多维度分析</a>
    <a class="nav-item" href="#s05"><span class="nav-num">05</span>模型横向对比</a>
    <a class="nav-item" href="#s06"><span class="nav-num">06</span>稳定性分析</a>
    <a class="nav-item" href="#s07"><span class="nav-num">07</span>交叉热力图</a>
    <a class="nav-item" href="#s08"><span class="nav-num">08</span>根因分析</a>
    <a class="nav-item" href="#s09"><span class="nav-num">09</span>效率对比</a>
    <a class="nav-item" href="#s10"><span class="nav-num">10</span>版本迭代</a>
    <a class="nav-item" href="#appendix"><span class="nav-num">附</span>方法论附录</a>
  </nav>
  <main class="main">
    <!-- 各节内容 -->
  </main>
</div>
```

---

## 侧边栏 Active 状态 JS

```js
// IntersectionObserver 驱动侧边栏高亮
const sections = document.querySelectorAll('section[id]');
const navLinks  = document.querySelectorAll('.nav-item');

const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      navLinks.forEach(l => l.classList.remove('active'));
      const active = document.querySelector(`.nav-item[href="#${e.target.id}"]`);
      if (active) active.classList.add('active');
    }
  });
}, { rootMargin: '-20% 0px -70% 0px' });

sections.forEach(s => io.observe(s));
```

---

## 切换按钮（按维度/按模型）

```html
<div class="toggle-group" style="margin-bottom:16px">
  <button class="toggle-btn active" onclick="switchView('dim',this)">按维度</button>
  <button class="toggle-btn" onclick="switchView('model',this)">按模型</button>
</div>
<style>
.toggle-group { display:flex; gap:8px; }
.toggle-btn {
  padding: 6px 16px; border-radius: 20px; border: 1px solid var(--border);
  background: var(--bg); color: var(--muted); cursor: pointer; font-size: 13px;
  transition: all .15s;
}
.toggle-btn.active {
  background: var(--primary); color: #fff; border-color: var(--primary);
}
</style>
<script>
function switchView(mode, btn) {
  document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // 切换逻辑：重新 setOption 或 show/hide 不同图表容器
  if (mode === 'dim') {
    chartByDim.getDom().style.display = 'block';
    chartByModel.getDom().style.display = 'none';
  } else {
    chartByDim.getDom().style.display = 'none';
    chartByModel.getDom().style.display = 'block';
  }
}
</script>
```

---

## 得分颜色函数

```js
function scoreColor(v) {
  if (v === null || v === undefined) return '#94A3B8';
  if (v >= 80) return '#059669';
  if (v >= 70) return '#2563EB';
  if (v >= 60) return '#D97706';
  return '#DC2626';
}

function scoreTag(v) {
  if (v === null) return '<span class="tag tag-blue">N/A</span>';
  if (v >= 80) return `<span class="tag tag-green">${v.toFixed(1)}</span>`;
  if (v >= 70) return `<span class="tag tag-blue">${v.toFixed(1)}</span>`;
  if (v >= 60) return `<span class="tag tag-amber">${v.toFixed(1)}</span>`;
  return `<span class="tag tag-red">${v.toFixed(1)}</span>`;
}
```
