# ECharts 图表模板库

ECharts 版本：5.4.3（CDN：`https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js`）

---

## 1. 7 维雷达图（综合排名节）

```js
{
  tooltip: { trigger: 'item' },
  legend: { bottom: 0, data: MODELS },
  radar: {
    indicator: DIMS.map(d => ({ name: d, max: 100 })),
    center: ['50%', '52%'], radius: '68%',
    axisName: { fontSize: 12, color: '#475569' },
    splitArea: { areaStyle: { color: ['#f8fafc','#f1f5f9','#e2e8f0','#cbd5e1','#94a3b8'].map((c,i)=>i%2===0?'#f8fafc':'#fff') } }
  },
  series: MODELS.map(m => ({
    type: 'radar', name: m,
    symbol: 'circle', symbolSize: 5,
    lineStyle: { width: 2, color: MC[m] },
    itemStyle: { color: MC[m] },
    areaStyle: { opacity: 0.08, color: MC[m] },
    data: [{ value: DS[m], name: m }]
  }))
}
```

---

## 2. 交叉热力图（模型 × 维度）

```js
// 数据格式：[dimIndex, modelIndex, value]
const hmData = [];
MODELS.forEach((m,mi) => {
  DIMS.forEach((d,di) => {
    hmData.push([di, mi, DS[m][di] ?? '-']);
  });
});

{
  tooltip: {
    formatter: p => `${DIMS[p.data[0]]}<br/>${MODELS[p.data[1]]}: <b>${p.data[2]}</b>`
  },
  grid: { top: 20, bottom: 60, left: 160, right: 80 },
  xAxis: { type: 'category', data: DIMS, axisLabel: { rotate: 30, fontSize: 11 } },
  yAxis: { type: 'category', data: MODELS, axisLabel: { fontSize: 12 } },
  visualMap: {
    min: 40, max: 100, calculable: true,
    orient: 'horizontal', bottom: 0, left: 'center',
    inRange: { color: ['#DC2626','#F97316','#FACC15','#34D399','#059669'] }
  },
  series: [{
    type: 'heatmap', data: hmData,
    label: { show: true, fontSize: 11, formatter: p => p.data[2] ?? 'N/A' }
  }]
}
```

---

## 3. 分组柱状图（多维度得分对比）

```js
{
  tooltip: { trigger: 'axis' },
  legend: { bottom: 0, data: MODELS },
  grid: { top: 20, bottom: 60, left: 50, right: 20 },
  xAxis: { type: 'category', data: DIMS, axisLabel: { fontSize: 11, interval: 0, rotate: 15 } },
  yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
  series: MODELS.map(m => ({
    type: 'bar', name: m,
    itemStyle: { color: MC[m] },
    label: { show: true, position: 'top', fontSize: 10, formatter: p => p.value?.toFixed(1) ?? '' },
    data: DS[m]
  }))
}
```

---

## 4. 水平排名柱状图（综合得分）

```js
const ranked = [...MODELS_FULL].sort((a,b) => OV[b]-OV[a]);
{
  tooltip: { trigger: 'axis' },
  grid: { top: 10, bottom: 20, left: 120, right: 60 },
  xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
  yAxis: { type: 'category', data: ranked.slice().reverse() },
  series: [{
    type: 'bar', barMaxWidth: 36,
    label: { show: true, position: 'right', formatter: p => `${p.value.toFixed(1)}%` },
    data: ranked.slice().reverse().map(m => ({
      value: OV[m],
      itemStyle: { color: MC[m] }
    }))
  }]
}
```

---

## 5. 难度分层折线/柱状图

```js
// FullReport: Easy / Medium
// NewsReport: 简单 / 中等 / 困难
{
  tooltip: { trigger: 'axis' },
  legend: { bottom: 0, data: MODELS },
  xAxis: { type: 'category', data: ['Easy','Medium'] },
  yAxis: { type: 'value', min: 40, max: 100, axisLabel: { formatter: '{value}%' } },
  series: MODELS.map(m => ({
    type: 'line', name: m, smooth: true,
    symbol: 'circle', symbolSize: 8,
    lineStyle: { width: 2, color: MC[m] },
    itemStyle: { color: MC[m] },
    label: { show: true, position: 'top', fontSize: 10 },
    data: difficultyFull[m]   // [easyScore, mediumScore]
  }))
}
```

---

## 6. 稳定性标准差柱状图

```js
// σ = √(p·(1-p))，p = accuracy/100
const sigmaData = MODELS.map(m => ({
  name: m,
  value: +(Math.sqrt((OV[m]/100)*(1-OV[m]/100))*100).toFixed(2),
  itemStyle: { color: MC[m] }
}));

{
  tooltip: { trigger: 'axis', formatter: p => `${p[0].name}<br/>σ = ${p[0].value}%` },
  xAxis: { type: 'category', data: MODELS },
  yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
  series: [{
    type: 'bar', barMaxWidth: 48,
    label: { show: true, position: 'top', formatter: p => `${p.value}%` },
    data: sigmaData
  }]
}
```

---

## 7. Token 消耗堆叠柱状图（效率分析）

```js
// latencyData 结构：{ model, dataset, latency, inputToken, outputToken }
{
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { data: ['输入 Token','输出 Token'], bottom: 0 },
  xAxis: { type: 'category', data: modelDatasetLabels },
  yAxis: { type: 'value', name: 'Tokens' },
  series: [
    {
      name: '输入 Token', type: 'bar', stack: 'token',
      itemStyle: { color: '#93C5FD' },
      label: { show: false },
      data: inputTokens
    },
    {
      name: '输出 Token', type: 'bar', stack: 'token',
      itemStyle: { color: '#3B82F6' },
      label: { show: true, position: 'top', formatter: p => `${p.value}` },
      data: outputTokens
    }
  ]
}
```

---

## 通用初始化模式（懒加载）

```js
function lazyChart(containerId, optionFn) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const chart = echarts.init(el);
        chart.setOption(optionFn());
        window.addEventListener('resize', () => chart.resize());
        observer.unobserve(el);
      }
    });
  }, { threshold: 0.1 });
  observer.observe(el);
}

// 使用
lazyChart('chart-radar', () => radarOption);
lazyChart('chart-heatmap', () => heatmapOption);
```
