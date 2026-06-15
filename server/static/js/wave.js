/* ═══════════════════════════════════════════════════════════
   Wave Dashboard — Main Page Logic
   ═══════════════════════════════════════════════════════════ */

// ── Sidebar Navigation ──
document.querySelectorAll('.sidebar-link').forEach(function(link) {
  link.addEventListener('click', function(e) {
    e.preventDefault();
    var section = this.dataset.section;
    switchSection(section);
    if (section === 'stock-selection') loadPoolsAndStrategies();
    if (section === 'tracker') loadTracker();
    if (section === 'dashboard') loadDashboard();
    if (section === 'sector') sector_loadLatest();
    if (section === 'monitor') loadMonitor();
  });
});

// ═══════════════════════════════════════════════════════════
// Holdings Dashboard
// ═══════════════════════════════════════════════════════════

var _holdingsStrategies = {buy:[], sell:[]};
var _navChart = null;

async function loadDashboard() {
  await Promise.all([loadHoldings(), loadNav()]);
}

async function loadStrategies() {
  try {
    var resp = await fetch('/api/holdings/strategies');
    _holdingsStrategies = await resp.json();
  } catch(e) { console.warn('加载策略失败', e); }
}

function strategyLabel(type, id) {
  var list = _holdingsStrategies[type] || [];
  var found = list.find(function(s) { return s.id === id; });
  return found ? found.label : id;
}

function strategyOptions(type) {
  return (_holdingsStrategies[type] || []).map(function(s) {
    return '<option value="' + s.id + '">' + s.label + '</option>';
  }).join('');
}

// ── Holdings CRUD ──
async function loadHoldings() {
  try {
    var resp = await fetch('/api/holdings?status=open');
    var data = await resp.json();
    renderOpenTrades(data.trades || []);
  } catch(e) { console.warn('加载持仓失败', e); }
  try {
    var resp = await fetch('/api/holdings?status=closed');
    var data = await resp.json();
    renderClosedTrades(data.trades || []);
  } catch(e) { console.warn('加载已清仓失败', e); }
}

function renderOpenTrades(trades) {
  var tbody = document.getElementById('open-trades-body');
  var table = document.getElementById('open-trades-table');
  var empty = document.getElementById('open-trades-empty');
  var area = document.getElementById('add-trade-area');

  area.innerHTML = '<button class="btn btn-primary btn-sm" onclick="showAddForm()">+ 新增持仓</button>';

  if (trades.length === 0) {
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }
  table.style.display = 'table';
  empty.style.display = 'none';

  tbody.innerHTML = trades.map(function(t) {
    return '<tr>' +
      '<td><strong>' + t.stock_name + '</strong></td>' +
      '<td class="text-muted">' + t.stock_code + '</td>' +
      '<td>' + t.buy_date + '</td>' +
      '<td class="num">' + t.cost_price.toFixed(2) + '</td>' +
      '<td class="num">' + t.shares.toLocaleString() + '</td>' +
      '<td><span class="badge badge-blue">' + strategyLabel('buy', t.buy_strategy) + '</span></td>' +
      '<td class="flex gap-2">' +
        '<button class="btn btn-secondary btn-sm" onclick="showEditForm(\'' + t.id + '\')">编辑</button>' +
        '<button class="btn btn-danger btn-sm" onclick="showCloseForm(\'' + t.id + '\')">清仓</button>' +
        '<button class="btn btn-ghost btn-icon" onclick="deleteTrade(\'' + t.id + '\')" title="删除">&times;</button>' +
      '</td></tr>';
  }).join('');
}

function renderClosedTrades(trades) {
  var tbody = document.getElementById('closed-trades-body');
  var table = document.getElementById('closed-trades-table');
  var empty = document.getElementById('closed-trades-empty');

  if (trades.length === 0) {
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }
  table.style.display = 'table';
  empty.style.display = 'none';

  tbody.innerHTML = trades.map(function(t) {
    var pnlClass = t.pnl >= 0 ? 'pnl-up' : 'pnl-down';
    var pnlSign = t.pnl >= 0 ? '+' : '';
    return '<tr>' +
      '<td><strong>' + t.stock_name + '</strong></td>' +
      '<td class="text-muted">' + t.stock_code + '</td>' +
      '<td>' + t.buy_date + '</td>' +
      '<td class="num">' + t.cost_price.toFixed(2) + '</td>' +
      '<td>' + t.sell_date + '</td>' +
      '<td class="num">' + t.sell_price.toFixed(2) + '</td>' +
      '<td class="num ' + pnlClass + '">' + pnlSign + t.pnl.toLocaleString() + '</td>' +
      '<td class="num ' + pnlClass + '">' + pnlSign + t.pnl_pct.toFixed(2) + '%</td>' +
      '<td><span class="badge badge-blue">' + strategyLabel('buy', t.buy_strategy) + '</span></td>' +
      '<td><span class="badge badge-amber">' + strategyLabel('sell', t.sell_strategy) + '</span></td>' +
    '</tr>';
  }).join('');
}

function showAddForm() {
  var area = document.getElementById('add-trade-area');
  area.innerHTML = '<div class="card mt-4">' +
    '<div class="form-row">' +
      '<div class="form-group"><label class="form-label">股票名称</label><input class="form-input" type="text" id="af-name" placeholder="—"></div>' +
      '<div class="form-group"><label class="form-label">股票代码</label><input class="form-input" type="text" id="af-code" placeholder="000001" style="text-transform:uppercase;"></div>' +
      '<div class="form-group"><label class="form-label">买入日期</label><input class="form-input" type="date" id="af-date" value="' + new Date().toISOString().slice(0,10) + '"></div>' +
      '<div class="form-group"><label class="form-label">持仓成本</label><input class="form-input" type="number" id="af-price" step="0.01" min="0" placeholder="—"></div>' +
      '<div class="form-group"><label class="form-label">持仓数量</label><input class="form-input" type="number" id="af-shares" step="100" min="0" placeholder="—"></div>' +
      '<div class="form-group"><label class="form-label">买入策略</label><select class="form-select" id="af-strategy">' + strategyOptions('buy') + '</select></div>' +
    '</div>' +
    '<div class="form-actions">' +
      '<button class="btn btn-primary" onclick="submitAdd()">确认添加</button>' +
      '<button class="btn btn-secondary" onclick="cancelForm()">取消</button>' +
    '</div></div>';
}

async function submitAdd() {
  var body = {
    stock_name: document.getElementById('af-name').value.trim(),
    stock_code: document.getElementById('af-code').value.trim(),
    buy_date: document.getElementById('af-date').value,
    cost_price: document.getElementById('af-price').value,
    shares: document.getElementById('af-shares').value,
    buy_strategy: document.getElementById('af-strategy').value,
  };
  if (!body.stock_name || !body.stock_code || !body.buy_date || !body.cost_price || !body.shares) {
    showToast('请填写所有必填字段'); return;
  }
  try {
    var resp = await fetch('/api/holdings', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    cancelForm(); loadHoldings();
    showToast('持仓添加成功', 'success');
  } catch(e) { showToast('添加失败: ' + e.message); }
}

function cancelForm() {
  document.getElementById('add-trade-area').innerHTML =
    '<button class="btn btn-primary btn-sm" onclick="showAddForm()">+ 新增持仓</button>';
}

async function showEditForm(tradeId) {
  var resp = await fetch('/api/holdings?status=open');
  var data = await resp.json();
  var t = (data.trades || []).find(function(x) { return x.id === tradeId; });
  if (!t) return;
  var area = document.getElementById('add-trade-area');
  area.innerHTML = '<div class="card mt-4">' +
    '<div class="form-row">' +
      '<div class="form-group"><label class="form-label">股票名称</label><input class="form-input" type="text" id="ef-name" value="' + t.stock_name + '"></div>' +
      '<div class="form-group"><label class="form-label">股票代码</label><input class="form-input" type="text" id="ef-code" value="' + t.stock_code + '" style="text-transform:uppercase;"></div>' +
      '<div class="form-group"><label class="form-label">买入日期</label><input class="form-input" type="date" id="ef-date" value="' + t.buy_date + '"></div>' +
      '<div class="form-group"><label class="form-label">持仓成本</label><input class="form-input" type="number" id="ef-price" step="0.01" min="0" value="' + t.cost_price + '"></div>' +
      '<div class="form-group"><label class="form-label">持仓数量</label><input class="form-input" type="number" id="ef-shares" step="100" min="0" value="' + t.shares + '"></div>' +
      '<div class="form-group"><label class="form-label">买入策略</label><select class="form-select" id="ef-strategy">' + strategyOptions('buy') + '</select></div>' +
    '</div>' +
    '<div class="form-actions">' +
      '<button class="btn btn-primary" onclick="submitEdit(\'' + tradeId + '\')">保存修改</button>' +
      '<button class="btn btn-secondary" onclick="cancelForm()">取消</button>' +
    '</div></div>';
  document.getElementById('ef-strategy').value = t.buy_strategy;
}

async function submitEdit(tradeId) {
  var body = {
    stock_name: document.getElementById('ef-name').value.trim(),
    stock_code: document.getElementById('ef-code').value.trim(),
    buy_date: document.getElementById('ef-date').value,
    cost_price: document.getElementById('ef-price').value,
    shares: document.getElementById('ef-shares').value,
    buy_strategy: document.getElementById('ef-strategy').value,
  };
  try {
    var resp = await fetch('/api/holdings/' + tradeId, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    cancelForm(); loadHoldings();
    showToast('持仓修改成功', 'success');
  } catch(e) { showToast('修改失败: ' + e.message); }
}

function showCloseForm(tradeId) {
  var area = document.getElementById('add-trade-area');
  area.innerHTML = '<div class="card mt-4">' +
    '<div class="form-row">' +
      '<div class="form-group"><label class="form-label">卖出日期</label><input class="form-input" type="date" id="cf-date" value="' + new Date().toISOString().slice(0,10) + '"></div>' +
      '<div class="form-group"><label class="form-label">卖出价格</label><input class="form-input" type="number" id="cf-price" step="0.01" min="0" placeholder="—"></div>' +
      '<div class="form-group"><label class="form-label">卖出策略</label><select class="form-select" id="cf-strategy">' + strategyOptions('sell') + '</select></div>' +
    '</div>' +
    '<div class="form-actions">' +
      '<button class="btn btn-danger" onclick="submitClose(\'' + tradeId + '\')">确认清仓</button>' +
      '<button class="btn btn-secondary" onclick="cancelForm()">取消</button>' +
    '</div></div>';
}

async function submitClose(tradeId) {
  var body = {
    sell_date: document.getElementById('cf-date').value,
    sell_price: document.getElementById('cf-price').value,
    sell_strategy: document.getElementById('cf-strategy').value,
  };
  if (!body.sell_date || !body.sell_price) { showToast('请填写卖出日期和价格'); return; }
  try {
    var resp = await fetch('/api/holdings/' + tradeId + '/close', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    cancelForm(); loadHoldings(); loadNav();
    showToast('清仓操作成功', 'success');
  } catch(e) { showToast('清仓失败: ' + e.message); }
}

async function deleteTrade(tradeId) {
  if (!(await showConfirm('确认删除该条交易记录？', {danger:true}))) return;
  try {
    var resp = await fetch('/api/holdings/' + tradeId, { method:'DELETE' });
    var data = await resp.json();
    if (data.status === 'deleted') { loadHoldings(); showToast('交易记录已删除', 'success'); }
  } catch(e) { showToast('删除失败: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════
// NAV Management
// ═══════════════════════════════════════════════════════════

async function loadNav() {
  try {
    var resp = await fetch('/api/nav');
    var nav = await resp.json();
    renderNavSummary(nav);
    renderNavChart(nav);
    renderNavActions(nav);
  } catch(e) { console.warn('加载净值失败', e); }
}

function renderNavSummary(nav) {
  var div = document.getElementById('nav-summary');
  var records = nav.records || [];
  var hasInit = records.length > 0;
  var totalDeposit = records.filter(function(r) { return r.type === 'deposit'; }).reduce(function(s,r) { return s + (r.amount||0); }, 0);
  var totalWithdraw = records.filter(function(r) { return r.type === 'withdraw'; }).reduce(function(s,r) { return s + (r.amount||0); }, 0);
  var investReturn = nav.current_nav - nav.initial_nav - totalDeposit + totalWithdraw;
  var totalReturn = nav.initial_nav > 0 ? (investReturn / nav.initial_nav * 100).toFixed(2) : '0.00';
  var returnClass = investReturn >= 0 ? 'pnl-up' : 'pnl-down';
  var investSign = investReturn >= 0 ? '+' : '';

  div.innerHTML =
    '<div class="metric-card card card-hover"><div class="metric-label">当前净值</div><div class="metric-value font-mono">' + (hasInit ? '¥' + nav.current_nav.toLocaleString() : '—') + '</div></div>' +
    '<div class="metric-card card card-hover"><div class="metric-label">投资收益</div><div class="metric-value font-mono ' + returnClass + '">' + (hasInit ? investSign + investReturn.toLocaleString() + '元' : '—') + '</div></div>' +
    '<div class="metric-card card card-hover"><div class="metric-label">投资收益率</div><div class="metric-value font-mono ' + returnClass + '">' + (hasInit ? investSign + totalReturn + '%' : '—') + '</div></div>' +
    '<div class="metric-card card card-hover"><div class="metric-label">交易笔数</div><div class="metric-value font-mono">' + records.filter(function(r) { return r.type === 'close'; }).length + '</div></div>';
}

function renderNavActions(nav) {
  var area = document.getElementById('nav-actions-area');
  var hasInit = (nav.records || []).length > 0;
  if (!hasInit) {
    area.innerHTML = '<button class="btn btn-primary" onclick="showInitNavForm()">初始化净值</button>';
    document.getElementById('nav-empty').style.display = 'block';
    return;
  }
  document.getElementById('nav-empty').style.display = 'none';
  var hasMultiple = (nav.records || []).length > 1;
  area.innerHTML =
    '<button class="btn btn-secondary btn-sm" onclick="showAdjustForm(\'deposit\')">入金</button>' +
    '<button class="btn btn-secondary btn-sm" onclick="showAdjustForm(\'withdraw\')">出金</button>' +
    (hasMultiple ? '<button class="btn btn-secondary btn-sm" onclick="undoNav()">撤销上一步</button>' : '') +
    '<button class="btn btn-ghost btn-sm text-red" onclick="resetNav()">重置净值</button>';
}

function showInitNavForm() {
  var area = document.getElementById('nav-actions-area');
  area.innerHTML = '<div class="card mt-4">' +
    '<div class="form-row">' +
      '<div class="form-group"><label class="form-label">初始净值(元)</label><input class="form-input" type="number" id="init-nav" step="1000" min="0" placeholder="100000"></div>' +
      '<div class="form-group"><label class="form-label">初始日期</label><input class="form-input" type="date" id="init-date" value="' + new Date().toISOString().slice(0,10) + '"></div>' +
    '</div>' +
    '<div class="form-actions">' +
      '<button class="btn btn-primary" onclick="submitInitNav()">确认初始化</button>' +
      '<button class="btn btn-secondary" onclick="loadNav()">取消</button>' +
    '</div></div>';
}

async function submitInitNav() {
  var body = { initial_nav: document.getElementById('init-nav').value, initial_date: document.getElementById('init-date').value };
  if (!body.initial_nav || !body.initial_date) { showToast('请填写净值和日期'); return; }
  try {
    var resp = await fetch('/api/nav/init', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    loadNav(); showToast('净值初始化成功', 'success');
  } catch(e) { showToast('初始化失败: ' + e.message); }
}

function showAdjustForm(direction) {
  var label = direction === 'deposit' ? '入金' : '出金';
  var area = document.getElementById('nav-actions-area');
  area.innerHTML = '<div class="card mt-4">' +
    '<div class="form-row">' +
      '<div class="form-group"><label class="form-label">' + label + '金额(元)</label><input class="form-input" type="number" id="adj-amount" step="1000" min="0" placeholder="50000"></div>' +
      '<div class="form-group"><label class="form-label">日期</label><input class="form-input" type="date" id="adj-date" value="' + new Date().toISOString().slice(0,10) + '"></div>' +
    '</div>' +
    '<div class="form-actions">' +
      '<button class="btn btn-primary" onclick="submitAdjust(\'' + direction + '\')">确认' + label + '</button>' +
      '<button class="btn btn-secondary" onclick="loadNav()">取消</button>' +
    '</div></div>';
}

async function submitAdjust(direction) {
  var body = { amount: document.getElementById('adj-amount').value, direction: direction, date: document.getElementById('adj-date').value };
  if (!body.amount || !body.date) { showToast('请填写金额和日期'); return; }
  try {
    var resp = await fetch('/api/nav/adjust', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    loadNav(); showToast('出入金操作成功', 'success');
  } catch(e) { showToast('操作失败: ' + e.message); }
}

async function undoNav() {
  if (!(await showConfirm('确认撤销最后一条净值记录？'))) return;
  try {
    var resp = await fetch('/api/nav/undo', { method:'POST' });
    var data = await resp.json();
    if (data.error) { showToast(data.error); return; }
    loadNav(); showToast('已撤销最后一条记录', 'success');
  } catch(e) { showToast('撤销失败: ' + e.message); }
}

async function resetNav() {
  if (!(await showConfirm('确认重置所有净值记录？此操作不可恢复。', {danger:true}))) return;
  try {
    var resp = await fetch('/api/nav/reset', { method:'POST' });
    await resp.json();
    loadNav(); showToast('净值已重置', 'success');
  } catch(e) { showToast('重置失败: ' + e.message); }
}

function renderNavChart(nav) {
  var records = nav.records || [];
  var canvas = document.getElementById('nav-chart');
  var emptyDiv = document.getElementById('nav-empty');
  if (records.length < 1) { canvas.style.display = 'none'; emptyDiv.style.display = 'block'; return; }
  canvas.style.display = 'block'; emptyDiv.style.display = 'none';
  var labels = records.map(function(r) { return r.date; });
  var cumDeposit = 0, cumWithdraw = 0;
  var values = records.map(function(r) {
    if (r.type === 'deposit') cumDeposit += (r.amount || 0);
    if (r.type === 'withdraw') cumWithdraw += (r.amount || 0);
    return r.nav - cumDeposit + cumWithdraw;
  });
  if (_navChart) _navChart.destroy();
  _navChart = new Chart(canvas, {
    type: 'line',
    data: { labels: labels, datasets: [{
      label: '投资净值', data: values,
      borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)',
      fill: true, tension: 0.3, pointRadius: 4, pointBackgroundColor: '#10b981',
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: {
          label: function(ctx) { var r = records[ctx.dataIndex]; return '投资净值: ¥' + ctx.parsed.y.toLocaleString() + '  实际净值: ¥' + r.nav.toLocaleString(); },
          afterLabel: function(ctx) { var r = records[ctx.dataIndex]; var typeMap = {init:'初始', deposit:'入金', withdraw:'出金', close:'清仓'}; var lines = [typeMap[r.type] || r.type]; if (r.amount) lines.push((r.type==='deposit'?'+':'-') + '¥' + r.amount.toLocaleString()); if (r.pnl!==undefined&&r.pnl!==null) lines.push('盈亏: ' + (r.pnl>=0?'+':'') + r.pnl.toLocaleString()); return lines; }
        }
      }},
      scales: {
        x: { ticks: { color:'#94a3b8', font:{size:11} }, grid: { color:'rgba(0,0,0,0.04)' } },
        y: { ticks: { color:'#94a3b8', font:{size:11}, callback: function(v) { return '¥' + v.toLocaleString(); } }, grid: { color:'rgba(0,0,0,0.04)' } }
      }
    }
  });
}

// ═══════════════════════════════════════════════════════════
// Stock Selection Scan
// ═══════════════════════════════════════════════════════════

var _pollTimer = null;

async function loadPoolsAndStrategies() {
  try {
    var resps = await Promise.all([fetch('/api/pools').then(function(r){return r.json()}), fetch('/api/strategies').then(function(r){return r.json()})]);
    var stratSel = document.getElementById('ss-strategy');
    if (resps[1].strategies && resps[1].strategies.length > 0) {
      stratSel.innerHTML = resps[1].strategies.map(function(s) { return '<option value="' + s.name + '">' + s.display_name + '</option>'; }).join('');
    }
  } catch(e) { console.warn('加载选项失败', e); }
}

async function startScan() {
  var btn = document.getElementById('ss-btn');
  var progress = document.getElementById('ss-progress');
  var progressFill = document.getElementById('ss-progress-fill');
  var progressText = document.getElementById('ss-progress-text');
  var resultsDiv = document.getElementById('ss-results');

  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  resultsDiv.classList.add('hidden');

  var params = new URLSearchParams({
    pool: document.getElementById('ss-pool').value,
    strategy: document.getElementById('ss-strategy').value,
    top_n: document.getElementById('ss-top').value,
    min_score: document.getElementById('ss-min-score').value,
    delay: document.getElementById('ss-delay').value,
  });

  btn.style.display = 'none';
  var stopBtn = document.getElementById('ss-stop-btn');
  stopBtn.classList.remove('hidden'); stopBtn.disabled = false; stopBtn.textContent = '停止扫描';
  progress.classList.remove('hidden'); progressFill.style.width = '0%'; progressFill.className = 'progress-fill';
  progressText.innerHTML = '提交扫描任务...';

  try {
    var resp = await fetch('/api/scan?' + params.toString(), { method:'POST' });
    var data = await resp.json();
    if (!data.task_id) throw new Error(data.error || '提交失败');
    pollTask(data.task_id);
  } catch(e) {
    resetScanButtons(); progress.classList.add('hidden');
    showToast('提交失败: ' + e.message);
  }
}

function resetScanButtons() {
  var btn = document.getElementById('ss-btn');
  var stopBtn = document.getElementById('ss-stop-btn');
  btn.style.display = 'inline-flex'; btn.disabled = false; btn.textContent = '开始扫描';
  stopBtn.classList.add('hidden'); stopBtn.disabled = true;
}

async function stopScan() {
  var stopBtn = document.getElementById('ss-stop-btn');
  stopBtn.disabled = true; stopBtn.textContent = '停止中...';
  try {
    var resp = await fetch('/api/scan/tasks');
    var data = await resp.json();
    var running = (data.tasks || []).find(function(t) { return t.status === 'running'; });
    if (!running) { stopBtn.textContent = '无运行中的任务'; resetScanButtons(); return; }
    var stopResp = await fetch('/api/scan/' + running.task_id + '/stop', { method:'POST' });
    var stopData = await stopResp.json();
    if (stopData.status === 'cancelling') { stopBtn.textContent = '停止中...'; } else { resetScanButtons(); }
  } catch(e) { showToast('停止失败: ' + e.message); resetScanButtons(); }
}

function pollTask(taskId) {
  var progressFill = document.getElementById('ss-progress-fill');
  var progressText = document.getElementById('ss-progress-text');
  _pollTimer = setInterval(async function() {
    try {
      var resp = await fetch('/api/scan/' + taskId);
      var data = await resp.json();
      if (!data || data.error) { clearInterval(_pollTimer); _pollTimer = null; resetScanButtons(); showToast(data.error || '任务查询失败'); return; }
      var p = data.progress;
      var pct = p.total > 0 ? Math.min(100, Math.round(p.scanned / p.total * 100)) : 0;
      progressFill.style.width = pct + '%';
      if (data.status === 'running') {
        progressFill.className = 'progress-fill running';
        progressText.innerHTML = '<strong>' + pct + '%</strong> 已扫描 ' + p.scanned + '/' + p.total + ' · 合格 <strong>' + p.passed + '</strong> 只' + (p.current_stock ? ' · 当前 ' + p.current_stock : '') + ' · 耗时 ' + p.elapsed + 's' + (p.eta > 0 ? ' · 剩余 ' + p.eta + 's' : '');
      } else if (data.status === 'completed') {
        clearInterval(_pollTimer); _pollTimer = null;
        progressFill.className = 'progress-fill done';
        progressText.innerHTML = '<strong>扫描完成</strong> 共扫描 ' + p.scanned + ' 只 · 合格 <strong>' + p.passed + '</strong> 只 · 耗时 ' + p.elapsed + 's';
        resetScanButtons(); fetchResult(taskId);
      } else if (data.status === 'cancelled') {
        clearInterval(_pollTimer); _pollTimer = null;
        progressFill.className = 'progress-fill';
        progressText.innerHTML = '<strong>扫描已停止</strong> 已扫描 ' + p.scanned + '/' + p.total + ' · 合格 ' + p.passed + ' 只';
        resetScanButtons();
      } else if (data.status === 'failed') {
        clearInterval(_pollTimer); _pollTimer = null;
        progressFill.className = 'progress-fill fail';
        progressText.innerHTML = '<strong>扫描失败</strong>: ' + (data.error || '未知错误');
        resetScanButtons();
      }
    } catch(e) { clearInterval(_pollTimer); _pollTimer = null; resetScanButtons(); showToast('轮询异常: ' + e.message); }
  }, 1500);
}

async function fetchResult(taskId) {
  var resultsDiv = document.getElementById('ss-results');
  var summary = document.getElementById('ss-result-summary');
  var tableDiv = document.getElementById('ss-result-table');
  try {
    var resp = await fetch('/api/scan/' + taskId + '/result');
    var data = await resp.json();
    if (!data.results || data.results.length === 0) {
      resultsDiv.classList.remove('hidden');
      summary.textContent = '— 无符合条件的标的';
      tableDiv.innerHTML = '<div class="empty-state"><div class="empty-text">当前市场条件下，暂无触发买入信号的标的</div></div>';
      loadDataStatus(); return;
    }
    resultsDiv.classList.remove('hidden');
    summary.textContent = '— 共 ' + data.count + ' 只';
    var html = '<div class="table-wrap"><table><thead><tr><th>#</th><th>股票</th><th>代码</th><th>行业</th><th class="text-right">得分</th><th class="text-right">现价</th><th class="text-right">涨跌</th><th>核心信号</th></tr></thead><tbody>';
    data.results.forEach(function(r, i) {
      var info = r.latest_info;
      var sign = info.pct_chg >= 0 ? '+' : '';
      var topSignals = r.details.slice().sort(function(a,b) { return b.score - a.score; }).slice(0,2);
      var signalDesc = topSignals.map(function(d) { return d.desc.replace(/[（(].*[）)]/g,''); }).join(' + ');
      var scoreClass = r.score >= 50 ? 'score-high' : (r.score >= 30 ? 'score-mid' : 'score-low');
      var pnlClass = info.pct_chg >= 0 ? 'pnl-up' : 'pnl-down';
      html += '<tr class="cursor-pointer" onclick="toggleDetail(this)">' +
        '<td class="text-muted">' + (i+1) + '</td>' +
        '<td><strong>' + r.name + '</strong></td>' +
        '<td class="text-muted">' + r.code + '</td>' +
        '<td class="text-xs">' + (r.industry || '—') + '</td>' +
        '<td class="num ' + scoreClass + '">' + r.score + '</td>' +
        '<td class="num">' + info.close.toFixed(2) + '</td>' +
        '<td class="num ' + pnlClass + '">' + sign + info.pct_chg.toFixed(2) + '%</td>' +
        '<td class="text-xs">' + signalDesc + '</td></tr>';
      html += '<tr class="detail-row hidden"><td colspan="8"><div class="detail-content card" style="padding:12px;margin:4px 0;">';
      html += '<div class="detail-grid"><span class="text-muted">日期</span><span>' + info.date + '</span>';
      html += '<span class="text-muted">成交量</span><span>' + (info.volume/10000).toFixed(0) + '万</span>';
      if (r.indicators) { Object.entries(r.indicators).forEach(function(kv) { if (kv[1]!==null&&kv[1]!==undefined) html += '<span class="text-muted">' + kv[0] + '</span><span>' + (typeof kv[1]==='number'?kv[1].toFixed(2):kv[1]) + '</span>'; }); }
      html += '</div><div class="mt-2 text-sm fw-600">匹配条件:</div>';
      r.details.sort(function(a,b) { return b.score - a.score; }).forEach(function(d) {
        html += '<div class="text-xs" style="padding:2px 0"><span class="text-accent">&#10003; ' + d.desc + '</span> <span class="text-muted">' + d.score + '/' + d.weight + '</span> · ' + (d.detail.reason || '') + '</div>';
      });
      html += '</div></td></tr>';
    });
    html += '</tbody></table></div>';
    tableDiv.innerHTML = html;
    loadDataStatus();
  } catch(e) { showToast('获取结果失败: ' + e.message); }
}

function toggleDetail(row) {
  var detailRow = row.nextElementSibling;
  if (detailRow && detailRow.classList.contains('detail-row')) {
    detailRow.classList.toggle('hidden');
  }
}

// ═══════════════════════════════════════════════════════════
// Single Stock Diagnosis
// ═══════════════════════════════════════════════════════════

async function testStock() {
  var code = document.getElementById('ss-test-code').value.trim();
  var strategy = document.getElementById('ss-test-strategy').value;
  var resultDiv = document.getElementById('ss-test-result');
  if (!code) { showToast('请输入股票代码'); return; }
  resultDiv.classList.remove('hidden');
  resultDiv.innerHTML = '<div class="text-muted">正在分析...</div>';
  try {
    var resp = await fetch('/api/scan/test/' + code + '?strategy=' + strategy, { method:'POST' });
    var data = await resp.json();
    if (data.error) { resultDiv.innerHTML = '<span class="text-red">诊断失败: ' + data.error + '</span>'; return; }
    if (!data.passed) { resultDiv.innerHTML = '<span class="text-amber">' + data.name + '(' + data.code + ') 被排除</span>\n原因: ' + (data.reason || '不满足基础条件'); return; }
    var html = '<span class="text-accent fw-600">' + data.name + '(' + data.code + ')</span>  得分: <strong>' + data.score + '</strong>\n\n';
    var info = data.latest_info;
    html += '现价: ' + info.close.toFixed(2) + '  ' + (info.pct_chg>=0?'+':'') + info.pct_chg.toFixed(2) + '%  日期: ' + info.date + '\n';
    if (data.indicators) { Object.entries(data.indicators).forEach(function(kv) { if (kv[1]!==null) html += kv[0] + ': ' + (typeof kv[1]==='number'?kv[1].toFixed(2):kv[1]) + '  '; }); }
    html += '\n\n匹配条件:\n';
    data.details.sort(function(a,b) { return b.score - a.score; }).forEach(function(d) { html += '  ✓ ' + d.desc + '  (' + d.score + '/' + d.weight + ')  ' + (d.detail.reason || '') + '\n'; });
    resultDiv.innerHTML = html;
  } catch(e) { resultDiv.innerHTML = '<span class="text-red">请求异常: ' + e.message + '</span>'; }
}

// ═══════════════════════════════════════════════════════════
// Stock Tracking
// ═══════════════════════════════════════════════════════════

var _trackerData = [];  // all entries flat list
var _trackerSelectedId = null;

async function loadTracker() {
  var emptyDiv = document.getElementById('tracker-empty');
  var masterDetail = document.getElementById('tracker-master-detail');
  var refreshInfo = document.getElementById('tracker-refresh-info');
  try {
    var resp = await fetch('/api/tracker');
    var data = await resp.json();
    var grouped = data.grouped || {};
    var entries = Object.values(grouped);
    var allEntries = entries.reduce(function(a, g) { return a.concat(g.entries || []); }, []);
    if (allEntries.length === 0) { emptyDiv.style.display = 'block'; masterDetail.classList.add('hidden'); refreshInfo.textContent = '上次刷新: —'; return; }
    emptyDiv.style.display = 'none'; masterDetail.classList.remove('hidden');
    var lastRefresh = null;
    allEntries.forEach(function(e) { (e.stocks || []).forEach(function(s) { if (s.latest_date && (!lastRefresh || s.latest_date > lastRefresh)) lastRefresh = s.latest_date; }); });
    refreshInfo.textContent = lastRefresh ? '上次刷新: ' + lastRefresh : '上次刷新: 从未';
    // Flatten and sort by date desc
    _trackerData = [];
    for (var stratKey in grouped) {
      var stratGroup = grouped[stratKey];
      (stratGroup.entries || []).forEach(function(entry) {
        entry._strategy_name = stratGroup.strategy_name;
        _trackerData.push(entry);
      });
    }
    _trackerData.sort(function(a, b) { return b.scan_date.localeCompare(a.scan_date); });
    renderTrackerList();
    // Auto-select first if nothing selected
    if (_trackerSelectedId && !_trackerData.find(function(e) { return e.id === _trackerSelectedId; })) {
      _trackerSelectedId = null;
    }
    if (!_trackerSelectedId && _trackerData.length > 0) {
      selectTrackerEntry(_trackerData[0].id);
    }
  } catch(e) { emptyDiv.style.display = 'block'; masterDetail.classList.add('hidden'); emptyDiv.innerHTML = '<span class="text-red">加载失败: ' + e.message + '</span>'; }
}

function renderTrackerList() {
  var listDiv = document.getElementById('tracker-list');
  var html = '';
  _trackerData.forEach(function(entry) {
    var activeClass = entry.id === _trackerSelectedId ? ' active' : '';
    var stockCount = (entry.stocks || []).length;
    html += '<div class="tracker-list-item' + activeClass + '" onclick="selectTrackerEntry(\'' + entry.id + '\')">';
    html += '<div class="item-strategy">' + (entry._strategy_name || '') + '</div>';
    html += '<div class="item-meta"><span>' + entry.scan_date + '</span><span>' + (entry.pool_name || '') + ' Top' + (entry.top_n || '?') + '</span><span>' + stockCount + '只</span></div>';
    html += '</div>';
  });
  listDiv.innerHTML = html;
}

function selectTrackerEntry(entryId) {
  _trackerSelectedId = entryId;
  renderTrackerList();  // update active state
  var entry = _trackerData.find(function(e) { return e.id === entryId; });
  var detailDiv = document.getElementById('tracker-detail');
  if (!entry) { detailDiv.innerHTML = '<div class="empty-state"><div class="empty-text">未找到记录</div></div>'; return; }

  var stocks = entry.stocks || [];
  var html = '<div class="tracker-detail-header">';
  html += '<h3>' + (entry._strategy_name || '') + '</h3>';
  html += '<div class="detail-meta"><span>' + entry.scan_date + '</span><span>' + (entry.pool_name || '') + ' Top' + (entry.top_n || '?') + '</span>';
  html += '<button class="btn btn-ghost btn-sm text-red" onclick="deleteTrackerEntry(\'' + entry.id + '\')">删除记录</button></div></div>';

  if (stocks.length === 0) {
    html += '<div class="empty-state"><div class="empty-text">该次扫描无结果</div></div>';
  } else {
    html += '<div class="table-wrap"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>行业</th><th class="text-right">得分</th><th class="text-right">被选价</th><th class="text-right">最新价</th><th class="text-right">涨跌</th></tr></thead><tbody>';
    stocks.forEach(function(s, i) {
      var pnlClass = s.pct_change === null ? '' : (s.pct_change >= 0 ? 'pnl-up' : 'pnl-down');
      var pnlText = s.pct_change === null ? '—' : (s.pct_change >= 0 ? '+' : '') + s.pct_change.toFixed(2) + '%';
      var scoreClass = s.score >= 50 ? 'score-high' : (s.score >= 30 ? 'score-mid' : 'score-low');
      html += '<tr><td class="text-muted">' + (i+1) + '</td><td class="text-muted">' + s.code + '</td><td><strong>' + s.name + '</strong></td>';
      html += '<td class="text-xs">' + (s.industry || '—') + '</td>';
      html += '<td class="num ' + scoreClass + '">' + (s.score || 0) + '</td>';
      html += '<td class="num">' + (s.scan_price ? s.scan_price.toFixed(2) : '—') + '</td>';
      html += '<td class="num">' + (s.latest_price ? s.latest_price.toFixed(2) : '—') + '</td>';
      html += '<td class="num ' + pnlClass + '">' + pnlText + '</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  detailDiv.innerHTML = html;
}

async function refreshTracker() {
  var btn = document.getElementById('tracker-refresh-btn');
  var refreshInfo = document.getElementById('tracker-refresh-info');
  btn.disabled = true; btn.textContent = '刷新中...';
  refreshInfo.textContent = '正在获取最新价格...';
  try {
    var resp = await fetch('/api/tracker/refresh', { method:'POST' });
    var data = await resp.json();
    refreshInfo.textContent = '上次刷新: ' + (data.refresh_time || '刚完成');
    loadDataStatus();
    await loadTracker();
  } catch(e) { refreshInfo.textContent = '刷新失败: ' + e.message; }
  finally { btn.disabled = false; btn.textContent = '刷新全部价格'; }
}

async function deleteTrackerEntry(entryId) {
  if (!(await showConfirm('确认删除该条跟踪记录？', {danger:true}))) return;
  try {
    var resp = await fetch('/api/tracker/' + entryId, { method:'DELETE' });
    var data = await resp.json();
    if (data.status === 'deleted') { loadTracker(); showToast('跟踪记录已删除', 'success'); }
  } catch(e) { showToast('删除失败: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════
// Sector Monitoring
// ═══════════════════════════════════════════════════════════

function sector_scoreClass(score) { if (score >= 70) return 'score-high'; if (score >= 50) return 'score-mid'; return 'score-low'; }

function sector_renderCard(s) {
  var cat = s.category || 'neutral';
  var catClass = cat === 'mainline' ? 'sector-mainline' : (cat === 'potential' ? 'sector-potential' : (cat === 'fading' ? 'sector-fading' : ''));
  var pctStr = (s.pct_chg >= 0 ? '+' : '') + (s.pct_chg || 0).toFixed(2) + '%';
  var pctColor = s.pct_chg >= 0 ? 'var(--red)' : 'var(--accent)';
  var upDown = '';
  if (s.up_count || s.down_count) upDown = '<span class="text-red">&#8593;' + (s.up_count||0) + '</span> <span class="text-accent">&#8595;' + (s.down_count||0) + '</span>';
  var details = s.details || [];
  var detailRows = '';
  for (var i = 0; i < details.length; i++) { var d = details[i]; detailRows += '<div class="flex justify-between text-xs"><span>' + d.desc + '</span><span class="' + sector_scoreClass(d.score) + '">' + d.score + '/' + d.weight + '</span></div>'; }
  return '<div class="card card-hover ' + catClass + '" onclick="sector_toggleStocks(this)" style="cursor:pointer">' +
    '<div class="flex justify-between items-center mb-2"><span class="fw-600">' + (s.name||'') + '</span><span class="font-mono fw-700 ' + sector_scoreClass(s.total_score) + '">' + (s.total_score||0) + '分</span></div>' +
    '<div class="flex gap-2 text-xs text-muted mb-2"><span style="color:' + pctColor + ';font-weight:600">' + pctStr + '</span>' + upDown + '</div>' +
    '<div class="text-xs" style="border-top:1px solid var(--border-subtle);padding-top:6px">' + detailRows + '</div>' +
    '<div class="card-stocks hidden" data-code="' + (s.code||'') + '" data-name="' + (s.name||'') + '"><div class="text-xs text-muted">点击加载成分股...</div></div>' +
    '</div>';
}

function sector_renderTable(sectors, limit) {
  limit = limit || 15;
  var list = (sectors || []).slice(0, limit);
  if (list.length === 0) return '<div class="text-sm text-muted text-center" style="padding:24px">暂无数据</div>';
  var html = '<div class="table-wrap"><table><thead><tr><th>#</th><th>板块</th><th class="text-right">涨跌幅</th><th class="text-right">评分</th><th class="text-right">涨/跌</th><th class="text-right">RS</th><th class="text-right">动量</th><th class="text-right">成交</th><th class="text-right">涨停</th><th class="text-right">普涨</th><th class="text-right">资金</th></tr></thead><tbody>';
  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var pctStr = (s.pct_chg >= 0 ? '+' : '') + (s.pct_chg || 0).toFixed(2) + '%';
    var pctColor = s.pct_chg >= 0 ? 'var(--red)' : 'var(--accent)';
    var details = s.details || [];
    var scores = {};
    for (var j = 0; j < details.length; j++) scores[details[j].criterion] = details[j].score;
    html += '<tr><td class="text-muted">' + (i+1) + '</td><td>' + (s.name||'') + '</td>';
    html += '<td class="num" style="color:' + pctColor + '">' + pctStr + '</td>';
    html += '<td class="num ' + sector_scoreClass(s.total_score) + '">' + (s.total_score||0) + '</td>';
    html += '<td class="num">' + (s.up_count||0) + '/' + (s.down_count||0) + '</td>';
    html += '<td class="num">' + (scores.rs||0) + '</td><td class="num">' + (scores.momentum||0) + '</td>';
    html += '<td class="num">' + (scores.activity||0) + '</td><td class="num">' + (scores.limit_up||0) + '</td>';
    html += '<td class="num">' + (scores.breadth||0) + '</td><td class="num">' + (scores.capital_flow||0) + '</td></tr>';
  }
  html += '</tbody></table></div>';
  return html;
}

function sector_renderResults(data) {
  var mainline = data.mainline || [];
  var potential = data.potential || [];
  var fading = data.fading || [];
  var mainlineDiv = document.getElementById('sector-mainline');
  var potentialDiv = document.getElementById('sector-potential');
  var fadingDiv = document.getElementById('sector-fading');
  var fadingTitle = document.getElementById('sector-fading-title');
  var industryDiv = document.getElementById('sector-industry-table');
  var conceptDiv = document.getElementById('sector-concept-table');

  mainlineDiv.innerHTML = mainline.length > 0 ? mainline.map(sector_renderCard).join('') : '<div class="text-sm text-muted" style="padding:24px">暂无主线板块</div>';
  potentialDiv.innerHTML = potential.length > 0 ? potential.map(sector_renderCard).join('') : '<div class="text-sm text-muted" style="padding:24px">暂无潜在主线板块</div>';
  if (fading.length > 0) { fadingTitle.classList.remove('hidden'); fadingDiv.classList.remove('hidden'); fadingDiv.innerHTML = fading.map(function(s) { return '<span class="badge badge-red">' + s.name + '</span>'; }).join(''); }
  else { fadingTitle.classList.add('hidden'); fadingDiv.classList.add('hidden'); }
  industryDiv.innerHTML = sector_renderTable(data.industry, 15);
  conceptDiv.innerHTML = sector_renderTable(data.concept, 15);
}

async function sector_loadLatest() {
  var infoEl = document.getElementById('sector-info');
  try {
    var resp = await fetch('/api/sector/latest');
    var data = await resp.json();
    if (data.status === 'empty') { infoEl.textContent = data.message; return; }
    if (data.scan_time) infoEl.textContent = '上次分析: ' + data.scan_time;
    document.getElementById('sector-empty').style.display = 'none';
    document.getElementById('sector-results').classList.remove('hidden');
    sector_renderResults(data);
  } catch(e) { infoEl.textContent = '加载失败: ' + e.message; }
}

async function sector_analyze() {
  var btn = document.getElementById('sector-analyze-btn');
  var infoEl = document.getElementById('sector-info');
  btn.disabled = true; btn.textContent = '分析中...';
  infoEl.textContent = '正在获取板块数据并分析...';
  try {
    var resp = await fetch('/api/sector/analyze', { method:'POST' });
    var data = await resp.json();
    if (data.error) { infoEl.textContent = '分析失败: ' + data.error; showToast('板块分析失败: ' + data.error); return; }
    if (data.scan_time) infoEl.textContent = '上次分析: ' + data.scan_time;
    document.getElementById('sector-empty').style.display = 'none';
    document.getElementById('sector-results').classList.remove('hidden');
    sector_renderResults(data);
    showToast('板块分析完成', 'success');
  } catch(e) { infoEl.textContent = '分析失败: ' + e.message; showToast('板块分析失败: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = '分析板块主线'; }
}

async function sector_toggleStocks(card) {
  var stocksDiv = card.querySelector('.card-stocks');
  if (!stocksDiv.classList.contains('hidden')) { stocksDiv.classList.add('hidden'); return; }
  var code = stocksDiv.dataset.code;
  var name = stocksDiv.dataset.name;
  if (!code) { stocksDiv.innerHTML = '<div class="text-xs text-muted">该板块无板块代码</div>'; stocksDiv.classList.remove('hidden'); return; }
  stocksDiv.innerHTML = '<div class="text-xs text-muted">加载中...</div>';
  stocksDiv.classList.remove('hidden');
  try {
    var resp = await fetch('/api/sector/' + encodeURIComponent(name) + '/stocks');
    var data = await resp.json();
    if (data.error) { stocksDiv.innerHTML = '<div class="text-xs text-red">' + data.error + '</div>'; return; }
    var stocks = data.stocks || [];
    if (stocks.length === 0) { stocksDiv.innerHTML = '<div class="text-xs text-muted">无成分股数据</div>'; return; }
    var html = '<div class="table-wrap"><table><thead><tr><th>代码</th><th>名称</th></tr></thead><tbody>';
    var limit = Math.min(stocks.length, 20);
    for (var i = 0; i < limit; i++) html += '<tr><td>' + stocks[i].code + '</td><td>' + stocks[i].name + '</td></tr>';
    if (stocks.length > 20) html += '<tr><td colspan="2" class="text-muted">... 共 ' + stocks.length + ' 只</td></tr>';
    html += '</tbody></table></div>';
    stocksDiv.innerHTML = html;
  } catch(e) { stocksDiv.innerHTML = '<div class="text-xs text-red">加载失败: ' + e.message + '</div>'; }
}

// ═══════════════════════════════════════════════════════════
// Monitor (盯盘)
// ═══════════════════════════════════════════════════════════

var _monitorPollTimer = null;
var _monitorStrategies = [];
var _prevTriggeredIds = {};

async function loadMonitor() {
  await monitorLoadStrategies();
  await monitorLoadPool();
  await monitorLoadStatus();
}

async function monitorLoadStrategies() {
  try {
    var resp = await fetch('/api/monitor/strategies');
    var data = await resp.json();
    _monitorStrategies = data.strategies || [];
    var sel = document.getElementById('monitor-strategy-select');
    sel.innerHTML = '';
    _monitorStrategies.forEach(function(s) {
      var opt = document.createElement('option');
      opt.value = s.name;
      opt.textContent = s.strategy_name + ' (' + s.signal_count + '个信号)';
      sel.appendChild(opt);
    });
  } catch(e) {
    console.error('load monitor strategies error:', e);
  }
}

async function monitorLoadPool() {
  var emptyDiv = document.getElementById('monitor-pool-empty');
  var contentDiv = document.getElementById('monitor-pool-content');
  var countSpan = document.getElementById('monitor-pool-count');
  try {
    var resp = await fetch('/api/monitor/pool');
    var data = await resp.json();
    var targets = data.targets || [];
    countSpan.textContent = targets.length;
    if (targets.length === 0) {
      emptyDiv.style.display = 'block';
      contentDiv.classList.add('hidden');
      return;
    }
    emptyDiv.style.display = 'none';
    contentDiv.classList.remove('hidden');
    monitorRenderPool(targets);
  } catch(e) {
    emptyDiv.style.display = 'block';
    contentDiv.classList.add('hidden');
    emptyDiv.innerHTML = '<span class="text-red">加载失败: ' + e.message + '</span>';
  }
}

function monitorRenderPool(targets) {
  var tbody = document.getElementById('monitor-pool-body');
  var html = '';
  targets.forEach(function(t) {
    var fromLabel = t.added_from === 'tracker' ? '选股跟踪' : '手动添加';
    html += '<tr>' +
      '<td>' + t.code + '</td>' +
      '<td>' + t.name + '</td>' +
      '<td class="num">' + (t.score || '—') + '</td>' +
      '<td>' + (t.industry || '—') + '</td>' +
      '<td><span class="badge badge-neutral">' + fromLabel + '</span></td>' +
      '<td><button class="btn btn-sm btn-ghost" onclick="monitorRemoveTarget(\'' + t.id + '\')">删除</button></td>' +
      '</tr>';
  });
  tbody.innerHTML = html;
}

function monitorShowAddForm() {
  var area = document.getElementById('monitor-add-area');
  if (area.innerHTML.trim()) { area.innerHTML = ''; return; }
  area.innerHTML =
    '<div class="flex gap-2 items-end" style="padding:12px 0">' +
    '<div class="form-group"><label class="form-label">股票代码</label>' +
    '<input class="form-input" type="text" id="monitor-add-code" placeholder="600519" style="text-transform:uppercase;width:120px"></div>' +
    '<div class="form-group"><label class="form-label">名称（可选）</label>' +
    '<input class="form-input" type="text" id="monitor-add-name" placeholder="贵州茅台" style="width:120px"></div>' +
    '<button class="btn btn-primary btn-sm" onclick="monitorAddTarget()">添加</button>' +
    '<button class="btn btn-ghost btn-sm" onclick="document.getElementById(\'monitor-add-area\').innerHTML=\'\'">取消</button>' +
    '</div>';
  document.getElementById('monitor-add-code').focus();
}

async function monitorAddTarget() {
  var code = document.getElementById('monitor-add-code').value.trim();
  var name = document.getElementById('monitor-add-name').value.trim() || code;
  if (!code) { showToast('请输入股票代码', 'error'); return; }
  try {
    var resp = await fetch('/api/monitor/pool', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: code, name: name}),
    });
    var data = await resp.json();
    if (resp.ok) {
      showToast('已添加 ' + name, 'success');
      document.getElementById('monitor-add-area').innerHTML = '';
      await monitorLoadPool();
    } else {
      showToast(data.error || '添加失败', 'error');
    }
  } catch(e) { showToast('添加失败: ' + e.message, 'error'); }
}

async function monitorRemoveTarget(id) {
  if (!await showConfirm('确定删除该目标？')) return;
  try {
    await fetch('/api/monitor/pool/' + id, {method: 'DELETE'});
    await monitorLoadPool();
  } catch(e) { showToast('删除失败', 'error'); }
}

async function monitorClearPool() {
  if (!await showConfirm('确定清空全部目标池？', {danger: true})) return;
  try {
    await fetch('/api/monitor/pool', {method: 'DELETE'});
    await monitorLoadPool();
  } catch(e) { showToast('清空失败', 'error'); }
}

async function monitorImportFromTracker() {
  var area = document.getElementById('monitor-import-area');
  if (!area.classList.contains('hidden')) { area.classList.add('hidden'); return; }

  try {
    var trackerResp = await fetch('/api/tracker');
    var trackerData = await trackerResp.json();
    var grouped = trackerData.grouped || {};
    var strategies = Object.keys(grouped);
    if (strategies.length === 0) {
      showToast('暂无选股跟踪数据', 'error');
      return;
    }

    // 获取已导入的 entry_id 集合
    var poolResp = await fetch('/api/monitor/pool');
    var poolData = await poolResp.json();
    var importedEntryIds = {};
    (poolData.targets || []).forEach(function(t) {
      if (t.entry_id) importedEntryIds[t.entry_id] = true;
    });

    // 收集所有批次，按日期降序排列
    var batches = [];
    strategies.forEach(function(strategy) {
      var group = grouped[strategy];
      (group.entries || []).forEach(function(e) {
        batches.push({
          id: e.id,
          scan_date: e.scan_date,
          strategy_name: group.strategy_name,
          stock_count: (e.stocks || []).length,
          imported: !!importedEntryIds[e.id],
        });
      });
    });
    batches.sort(function(a, b) { return b.scan_date.localeCompare(a.scan_date); });

    var html = '<div style="font-weight:600;font-size:.9em;margin-bottom:8px">选择要导入的跟踪批次</div>';
    batches.forEach(function(b) {
      var label = b.scan_date + '  ' + b.strategy_name + ' — ' + b.stock_count + '只';
      if (b.imported) {
        html += '<label style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:.84em;color:var(--text3);cursor:default">' +
          '<input type="checkbox" disabled style="accent-color:var(--text3)">' +
          label + ' <span class="badge badge-neutral" style="margin-left:4px">已导入</span>' +
          '</label>';
      } else {
        html += '<label style="display:flex;align-items:center;gap:8px;padding:3px 0;cursor:pointer;font-size:.84em">' +
          '<input type="checkbox" class="monitor-import-cb" value="' + b.id + '" style="accent-color:var(--accent)">' +
          label +
          '</label>';
      }
    });

    html += '<div class="form-actions">' +
      '<button class="btn btn-primary btn-sm" onclick="monitorDoImport()">确认导入</button>' +
      '<button class="btn btn-ghost btn-sm" onclick="document.getElementById(\'monitor-import-area\').classList.add(\'hidden\')">取消</button>' +
      '</div>';

    area.innerHTML = html;
    area.classList.remove('hidden');
  } catch(e) { showToast('加载跟踪数据失败: ' + e.message, 'error'); }
}

async function monitorDoImport() {
  var checked = document.querySelectorAll('.monitor-import-cb:checked');
  var ids = Array.from(checked).map(function(cb) { return cb.value; });
  if (ids.length === 0) { showToast('请至少选择一个批次', 'error'); return; }
  try {
    var resp = await fetch('/api/monitor/pool/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({entry_ids: ids}),
    });
    var data = await resp.json();
    if (resp.ok) {
      showToast('导入完成：新增' + data.added + '只，跳过' + data.skipped + '只', 'success');
      document.getElementById('monitor-import-area').classList.add('hidden');
      await monitorLoadPool();
    } else {
      showToast(data.error || '导入失败', 'error');
    }
  } catch(e) { showToast('导入失败: ' + e.message, 'error'); }
}

async function monitorStart() {
  var sel = document.getElementById('monitor-strategy-select');
  var selected = Array.from(sel.selectedOptions).map(function(o) { return o.value; });
  if (selected.length === 0) { showToast('请选择至少一个策略', 'error'); return; }
  try {
    var resp = await fetch('/api/monitor/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({strategies: selected}),
    });
    var data = await resp.json();
    if (resp.ok) {
      showToast('盯盘已开启', 'success');
      monitorUpdateButtons(true);
      monitorStartPolling();
    } else {
      showToast(data.error || '开启失败', 'error');
    }
  } catch(e) { showToast('开启失败: ' + e.message, 'error'); }
}

async function monitorStop() {
  if (!await showConfirm('确定停止盯盘？触发记录将被清空。')) return;
  try {
    await fetch('/api/monitor/stop', {method: 'POST'});
    showToast('盯盘已停止', 'info');
    monitorUpdateButtons(false);
    monitorStopPolling();
    _prevTriggeredIds = {};
    await monitorLoadStatus();
  } catch(e) { showToast('停止失败', 'error'); }
}

function monitorUpdateButtons(running) {
  var startBtn = document.getElementById('monitor-start-btn');
  var stopBtn = document.getElementById('monitor-stop-btn');
  var sel = document.getElementById('monitor-strategy-select');
  if (running) {
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    sel.disabled = true;
  } else {
    startBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
    sel.disabled = false;
  }
}

function monitorStartPolling() {
  monitorStopPolling();
  _monitorPollTimer = setInterval(monitorPoll, 3000);
}

function monitorStopPolling() {
  if (_monitorPollTimer) {
    clearInterval(_monitorPollTimer);
    _monitorPollTimer = null;
  }
}

async function monitorLoadStatus() {
  try {
    var resp = await fetch('/api/monitor/status');
    var data = await resp.json();
    monitorUpdateUI(data);
    if (data.status === 'running') {
      monitorUpdateButtons(true);
      monitorStartPolling();
    }
    monitorRenderSignals(data.triggered || {});
  } catch(e) { console.error('monitor load status error:', e); }
}

async function monitorPoll() {
  try {
    var resp = await fetch('/api/monitor/status');
    var data = await resp.json();
    monitorUpdateUI(data);
    if (data.status !== 'running') {
      monitorStopPolling();
      monitorUpdateButtons(false);
    }
    var triggered = data.triggered || {};
    monitorCheckNewSignals(triggered);
    monitorRenderSignals(triggered);
  } catch(e) { console.error('monitor poll error:', e); }
}

function monitorUpdateUI(data) {
  var info = document.getElementById('monitor-info');
  var stats = data.stats || {};
  var parts = [];
  if (data.status === 'running') {
    parts.push('运行中');
    if (stats.total_ticks) parts.push('轮询' + stats.total_ticks + '次');
    if (stats.total_signals) parts.push('信号' + stats.total_signals + '个');
    if (stats.last_tick_at) parts.push('最近: ' + stats.last_tick_at.substring(11, 19));
  } else if (data.status === 'stopped') {
    parts.push('已停止');
  } else {
    parts.push('未启动');
  }
  info.textContent = parts.join(' | ');
}

function monitorCheckNewSignals(triggered) {
  var newIds = {};
  var newSignals = [];
  Object.keys(triggered).forEach(function(strategy) {
    var group = triggered[strategy];
    (group.signals || []).forEach(function(s) {
      newIds[s.id] = true;
      if (!_prevTriggeredIds[s.id]) {
        newSignals.push(s);
      }
    });
  });
  _prevTriggeredIds = newIds;

  if (newSignals.length > 0) {
    monitorNotify(newSignals);
  }
}

function monitorNotify(newSignals) {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission === 'granted') {
    newSignals.forEach(function(s) {
      new Notification('盯盘信号: ' + s.stock_name, {
        body: s.signal_name + ' [' + (s.level || '') + '] - ' + (s.strategy_display || ''),
        tag: 'monitor-' + s.id,
      });
    });
  } else if (Notification.permission !== 'denied') {
    Notification.requestPermission();
  }
}

function monitorRenderSignals(triggered) {
  var emptyDiv = document.getElementById('monitor-signals-empty');
  var contentDiv = document.getElementById('monitor-signals-content');
  var strategies = Object.keys(triggered);
  if (strategies.length === 0) {
    emptyDiv.style.display = 'block';
    contentDiv.classList.add('hidden');
    return;
  }
  emptyDiv.style.display = 'none';
  contentDiv.classList.remove('hidden');

  var html = '';
  strategies.forEach(function(strategy) {
    var group = triggered[strategy];
    var signals = group.signals || [];
    html += '<div class="signal-section-title">' + (group.strategy_display || strategy) + ' <span class="badge badge-blue">' + signals.length + '</span></div>';
    html += '<div class="signal-grid">';
    signals.forEach(function(s) {
      var pctClass = s.pct_chg >= 0 ? 'pnl-up' : 'pnl-down';
      var pctText = s.pct_chg >= 0 ? '+' + s.pct_chg.toFixed(2) + '%' : s.pct_chg.toFixed(2) + '%';
      html += '<div class="card signal-card" id="signal-' + s.id + '">' +
        '<div class="signal-header">' +
        '<span class="signal-stock">' + s.stock_name + ' ' + s.stock_code + '</span>' +
        '<button class="btn btn-sm btn-ghost" onclick="monitorRemoveSignal(\'' + s.id + '\')">&#10005;</button>' +
        '</div>' +
        '<div class="signal-meta">' +
        '<span class="badge badge-' + monitorLevelColor(s.level) + '">' + (s.level || '信号') + '</span> ' +
        s.signal_name +
        '</div>' +
        '<div class="signal-meta">' +
        (s.score ? '选股分: ' + s.score + ' | ' : '') +
        (s.industry ? s.industry + ' | ' : '') +
        '<span class="' + pctClass + '">' + pctText + '</span>' +
        (s.price ? ' @ ' + s.price : '') +
        '</div>' +
        '<div class="signal-meta text-muted">' + (s.triggered_at || '') + '</div>' +
        '</div>';
    });
    html += '</div>';
  });
  contentDiv.innerHTML = html;
}

function monitorLevelColor(level) {
  if (!level) return 'neutral';
  var l = level.toLowerCase();
  if (l.indexOf('强烈') >= 0 || l.indexOf('强') >= 0) return 'red';
  if (l.indexOf('温和') >= 0 || l.indexOf('中') >= 0) return 'amber';
  if (l.indexOf('关注') >= 0 || l.indexOf('弱') >= 0) return 'blue';
  return 'neutral';
}

async function monitorRemoveSignal(id) {
  try {
    await fetch('/api/monitor/triggered/' + id, {method: 'DELETE'});
    delete _prevTriggeredIds[id];
    var el = document.getElementById('signal-' + id);
    if (el) el.remove();
  } catch(e) { showToast('删除失败', 'error'); }
}

// ═══════════════════════════════════════════════════════════
// Initialize
// ═══════════════════════════════════════════════════════════
(async function init() {
  loadDataStatus();
  await loadStrategies();
  loadDashboard();
})();
