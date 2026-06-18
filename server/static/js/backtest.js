/* ═══════════════════════════════════════════════════════════
   Backtest Page — Logic
   ═══════════════════════════════════════════════════════════ */

var currentTaskId = null;
var pollTimer = null;
var taskListPollTimer = null;

// ── Initialize ──
function initBacktestPage() {
  // 并行加载策略列表、股票池和控制面板默认值
  Promise.all([
    fetch('/api/strategies').then(function(r){return r.json()}),
    fetch('/api/pools').then(function(r){return r.json()}),
    fetch('/api/settings').then(function(r){return r.json()}).catch(function(){return null}),
  ]).then(function(results) {
    var d = results[0];
    var p = results[1];
    var settingsResp = results[2];
    var defaults = (settingsResp && settingsResp.data && settingsResp.data.backtest) || {};

    // 填充策略下拉
    var sel = document.getElementById('bt-strategy');
    if (sel) {
      (d.strategies || []).forEach(function(s) {
        var opt = document.createElement('option');
        opt.value = s.name; opt.textContent = s.display_name;
        sel.appendChild(opt);
      });
      if (defaults.strategy != null) sel.value = defaults.strategy;
    }

    // 填充股票池下拉
    var poolSel = document.getElementById('bt-pool');
    if (poolSel) {
      poolSel.innerHTML = '';
      (p.pools || []).forEach(function(pool) {
        var opt = document.createElement('option');
        opt.value = pool.name; opt.textContent = pool.name;
        poolSel.appendChild(opt);
      });
      if (defaults.pool != null) poolSel.value = defaults.pool;
    }

    // 用控制面板默认值填充表单
    if (defaults.top_n != null) document.getElementById('bt-topn').value = defaults.top_n;
    if (defaults.min_score != null) document.getElementById('bt-minscore').value = defaults.min_score;
    if (defaults.holding_days != null) document.getElementById('bt-hold').value = defaults.holding_days;
    if (defaults.initial_capital != null) document.getElementById('bt-capital').value = defaults.initial_capital;
  }).catch(function(e) { console.error('加载初始化数据失败:', e); });
  var now = new Date();
  var sixMonthsAgo = new Date(now);
  sixMonthsAgo.setMonth(sixMonthsAgo.getMonth() - 6);
  document.getElementById('bt-start').value = sixMonthsAgo.toISOString().slice(0,10);
  document.getElementById('bt-end').value = now.toISOString().slice(0,10);
  refreshTaskList();
}

// Execute immediately since script is at bottom of body
initBacktestPage();

function submitBacktest() {
  var btn = document.getElementById('btn-run');
  btn.disabled = true; btn.textContent = '提交中...';
  var body = {
    strategy: document.getElementById('bt-strategy').value,
    pool: document.getElementById('bt-pool').value,
    top_n: parseInt(document.getElementById('bt-topn').value) || 10,
    min_score: parseInt(document.getElementById('bt-minscore').value) || 25,
    holding_days: parseInt(document.getElementById('bt-hold').value) || 3,
    initial_capital: parseFloat(document.getElementById('bt-capital').value) || 100000,
    start_date: document.getElementById('bt-start').value || null,
    end_date: document.getElementById('bt-end').value || null,
  };
  fetch('/api/backtest', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) })
  .then(function(r){return r.json()}).then(function(d) {
    currentTaskId = d.task_id;
    // 提交成功，按钮恢复可点击，显示"回测中"，点击可跳转到进度区
    btn.disabled = false; btn.textContent = '回测中...';
    btn.onclick = function() { document.getElementById('progress-section').scrollIntoView({behavior:'smooth'}); };
    document.getElementById('progress-section').classList.remove('hidden');
    document.getElementById('result-section').classList.add('hidden');
    refreshTaskList();
    pollProgress();
  }).catch(function(e) {
    showToast('提交失败: ' + e); btn.disabled = false; btn.textContent = '开始回测';
    btn.onclick = submitBacktest;
  });
}

function pollProgress() {
  if (!currentTaskId) return;
  clearTimeout(pollTimer);
  fetch('/api/backtest/' + currentTaskId).then(function(r){return r.json()}).then(function(d) {
    var fill = document.getElementById('progress-fill');
    var text = document.getElementById('progress-text');
    if (d.status === 'completed') {
      fill.style.width = '100%'; fill.className = 'progress-fill done';
      text.innerHTML = '<strong>回测完成</strong>';
      _resetRunBtn();
      renderResult(d.result); refreshTaskList();
      showToast('回测完成', 'success'); return;
    }
    if (d.status === 'failed') {
      fill.className = 'progress-fill fail';
      text.innerHTML = '<strong>回测失败:</strong> ' + (d.error || '未知错误');
      _resetRunBtn();
      refreshTaskList();
      showToast('回测失败: ' + (d.error || '未知错误')); return;
    }
    var p = d.progress || {};
    var info = p.current_date ? ('正在处理: ' + p.current_date) : '准备中...';
    if (p.rounds_done > 0) info += ' | 已完成 ' + p.rounds_done + ' 轮';
    text.innerHTML = info;
    pollTimer = setTimeout(pollProgress, 2000);
  }).catch(function() { pollTimer = setTimeout(pollProgress, 3000); });
}

function renderResult(result) {
  if (!result) return;
  document.getElementById('result-section').classList.remove('hidden');
  var m = result.metrics || {};
  var grid = document.getElementById('metrics-grid');
  var items = [
    {label:'本金', value:fmtNum(m.initial_capital), cls:''},
    {label:'最终资产', value:fmtNum(m.final_nav), cls:m.final_nav>=m.initial_capital?'up':'down'},
    {label:'总收益率', value:fmtPct(m.total_return_pct), cls:m.total_return_pct>=0?'up':'down'},
    {label:'夏普比率', value:(m.nav_sharpe||0).toFixed(2), cls:''},
    {label:'最大回撤', value:(m.nav_max_drawdown||0).toFixed(2)+'%', cls:'down'},
    {label:'胜率', value:(m.win_rate||0).toFixed(1)+'%', cls:''},
    {label:'交易笔数', value:m.total_trades||0, cls:''},
    {label:'止损次数', value:m.stop_loss_count||0, cls:m.stop_loss_count>0?'down':''},
  ];
  grid.innerHTML = items.map(function(i) {
    return '<div class="bt-metric"><div class="label">' + i.label + '</div><div class="value ' + i.cls + '">' + i.value + '</div></div>';
  }).join('');

  var trades = result.trades || [];
  var tbody = document.getElementById('trades-body');
  tbody.innerHTML = trades.slice(0,200).map(function(t) {
    var cls = t.return_pct >= 0 ? 'pnl-up' : 'pnl-down';
    var reasonMap = {stop_loss:'止损', take_profit:'到期', end_of_backtest:'回测结束'};
    var reason = reasonMap[t.reason] || t.reason;
    return '<tr><td>' + t.code + '</td><td>' + t.name + '</td><td>' + t.buy_date + '</td><td>' + t.sell_date + '</td>' +
      '<td class="num ' + cls + '">' + fmtPct(t.return_pct) + '</td><td class="num">' + t.score + '</td><td>' + reason + '</td></tr>';
  }).join('');

  renderCharts(trades, result.nav_history);
}

function renderCharts(trades, navHistory) {
  var returns = trades.map(function(t) { return t.return_pct; });
  var codes = trades.map(function(t) { return t.code; });
  var navDates = (navHistory||[]).map(function(h) { return h.date; });
  var navValues = (navHistory||[]).map(function(h) { return h.nav; });

  var navChart = echarts.init(document.getElementById('chart-nav'));
  var navInterval = navDates.length > 60 ? Math.ceil(navDates.length / 30) : (navDates.length > 20 ? 2 : 0);
  navChart.setOption({
    tooltip:{trigger:'axis',formatter:function(p){return p[0].axisValue+'<br/>净值: ¥'+Number(p[0].value).toLocaleString();}},
    grid:{left:70,right:20,top:10,bottom:60},
    xAxis:{type:'category',data:navDates,axisLabel:{fontSize:10,rotate:45,interval:navInterval}},
    yAxis:{type:'value',scale:true,axisLabel:{formatter:function(v){return '¥'+(v/10000).toFixed(1)+'万';}},splitLine:{lineStyle:{type:'dashed',color:'rgba(0,0,0,0.06)'}}},
    series:[{type:'line',data:navValues,smooth:false,symbol:'none',
      lineStyle:{color:'#10b981',width:2},
      areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(16,185,129,0.2)'},{offset:1,color:'rgba(16,185,129,0.02)'}]}}
    }]
  });

  var distChart = echarts.init(document.getElementById('chart-dist'));
  distChart.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(p){return codes[p[0].dataIndex]+'<br/>收益率: '+p[0].value.toFixed(2)+'%';}},
    grid:{left:50,right:20,top:10,bottom:50},
    xAxis:{type:'category',data:codes,axisLabel:{rotate:45,fontSize:10,interval:function(i){return returns.length<=30||i%Math.ceil(returns.length/30)===0;}}},
    yAxis:{type:'value',axisLabel:{formatter:'{value}%'},splitLine:{lineStyle:{type:'dashed',color:'rgba(0,0,0,0.06)'}}},
    series:[{type:'bar',data:returns.map(function(v){return{value:v,itemStyle:{color:v>=0?'#ef4444':'#10b981'}};}),barMaxWidth:30}]
  });

  window.addEventListener('resize', function() { navChart.resize(); distChart.resize(); });
}

function refreshTaskList() {
  fetch('/api/backtest/tasks').then(function(r){return r.json()}).then(function(d) {
    var list = document.getElementById('task-list');
    var tasks = d.tasks || [];
    if (!tasks.length) { list.innerHTML = '<div class="text-sm text-muted text-center" style="padding:24px">暂无回测记录</div>'; _stopTaskListPoll(); return; }
    var hasRunning = false;
    list.innerHTML = tasks.map(function(t) {
      if (t.status === 'running' || t.status === 'pending') hasRunning = true;
      var statusClass = 'status-' + t.status;
      var statusText = {pending:'等待中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已中断'}[t.status] || t.status;
      var info = '<strong>' + t.strategy + '</strong> @ ' + t.pool + ' | top' + t.top_n + ' | 持有' + t.holding_days + '天';
      if (t.start_date) info += ' | ' + t.start_date + '~' + (t.end_date||'至今');
      // 运行中任务显示实时进度
      var progressHtml = '';
      if (t.status === 'running' && t.progress) {
        var p = t.progress;
        var phase = p.current_date || '准备中...';
        var elapsed = p.elapsed ? (p.elapsed + 's') : '';
        progressHtml = '<div class="task-progress"><div class="task-progress-bar"><div class="task-progress-fill running"></div></div><div class="task-progress-text">' + phase + (elapsed ? ' | ' + elapsed : '') + '</div></div>';
      }
      if (t.status === 'completed' && t.progress) info += ' | ' + t.progress.elapsed + 's';
      if (t.status === 'failed' && t.error) info += ' | ' + t.error;
      var isActive = currentTaskId === t.task_id;
      var activeClass = isActive ? ' active' : '';
      var clickAction = t.status === 'completed' ? ' onclick="loadTask(\'' + t.task_id + '\')"' : '';
      var actions = '';
      if (t.status === 'running') actions = ' <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();stopTask(\'' + t.task_id + '\')">停止</button>';
      return '<div class="task-item' + activeClass + '" data-task-id="' + t.task_id + '"' + clickAction + '><div class="info">' + info + progressHtml + '</div><div class="flex gap-2 items-center">' + actions + '<span class="status ' + statusClass + '">' + statusText + '</span></div></div>';
    }).join('');
    // 有运行中任务时自动轮询任务列表
    if (hasRunning) { _startTaskListPoll(); } else { _stopTaskListPoll(); }
  }).catch(function() {});
}

function _startTaskListPoll() {
  if (taskListPollTimer) return;
  taskListPollTimer = setInterval(function() { refreshTaskList(); }, 3000);
}

function _stopTaskListPoll() {
  if (taskListPollTimer) { clearInterval(taskListPollTimer); taskListPollTimer = null; }
}

async function stopTask(taskId) {
  if (!(await showConfirm('确定要停止这个回测任务吗？中断后数据不会保存。', {danger:true}))) return;
  fetch('/api/backtest/' + taskId + '/stop', {method:'POST'}).then(function(r){return r.json()}).then(function(d) {
    if (d.status === 'stopped') {
      clearTimeout(pollTimer);
      _stopTaskListPoll();  // 立即停止任务列表轮询
      document.getElementById('progress-section').classList.add('hidden');
      _resetRunBtn();
      refreshTaskList(); showToast('回测已停止', 'info');
    } else { showToast(d.error || '停止失败'); }
  });
}

function _resetRunBtn() {
  var btn = document.getElementById('btn-run');
  btn.disabled = false; btn.textContent = '开始回测';
  btn.onclick = submitBacktest;
}

function loadTask(taskId) {
  currentTaskId = taskId;
  document.querySelectorAll('.task-item').forEach(function(el) { el.classList.toggle('active', el.dataset.taskId === taskId); });
  fetch('/api/backtest/' + taskId).then(function(r){return r.json()}).then(function(d) { if (d.result) renderResult(d.result); });
}

function closeResult() {
  currentTaskId = null;
  document.getElementById('result-section').classList.add('hidden');
  document.querySelectorAll('.task-item.active').forEach(function(el) { el.classList.remove('active'); });
}

function fmtPct(v) { if (v===undefined||v===null) return '0.00%'; return (v>=0?'+':'') + v.toFixed(2) + '%'; }
function fmtNum(v) { if (v===undefined||v===null) return '0'; return '¥' + v.toLocaleString(); }
