/* ═══════════════════════════════════════════════════════════
   Common Utilities — Shared across all pages
   ═══════════════════════════════════════════════════════════ */

// ── Toast Notifications ──
function showToast(msg, type, duration) {
  type = type || 'error';
  duration = duration || 3500;
  var container = document.getElementById('toast-container');
  if (!container) return;
  var el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  container.appendChild(el);
  requestAnimationFrame(function() { el.classList.add('show'); });
  setTimeout(function() {
    el.classList.remove('show');
    setTimeout(function() { el.remove(); }, 400);
  }, duration);
}

// ── Confirm Modal ──
function showConfirm(msg, options) {
  options = options || {};
  return new Promise(function(resolve) {
    var overlay = document.getElementById('modal-overlay');
    var msgEl = document.getElementById('modal-msg');
    var confirmBtn = document.getElementById('modal-confirm-btn');
    var cancelBtn = document.getElementById('modal-cancel-btn');
    if (!overlay || !msgEl || !confirmBtn || !cancelBtn) { resolve(false); return; }
    msgEl.textContent = msg;
    confirmBtn.textContent = options.confirmText || '确认';
    cancelBtn.textContent = options.cancelText || '取消';
    confirmBtn.className = 'btn ' + (options.danger ? 'btn-danger' : 'btn-primary');
    cancelBtn.className = 'btn btn-secondary';
    overlay.classList.add('active');
    function cleanup() {
      overlay.classList.remove('active');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBackdrop);
    }
    function onConfirm() { cleanup(); resolve(true); }
    function onCancel() { cleanup(); resolve(false); }
    function onBackdrop(e) { if (e.target === overlay) { cleanup(); resolve(false); } }
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onBackdrop);
  });
}

// ── Data Status ──
function sourceLabel(source) {
  var labels = { tdx_local:'本地TDX', tdx_tcp:'TDX TCP降级', eastmoney_http:'东方财富降级', none:'—' };
  return labels[source] || source || '—';
}

async function loadDataStatus() {
  var el = document.getElementById('data-status-label');
  if (!el) return;
  try {
    var resp = await fetch('/api/sources');
    var data = await resp.json();
    var daily = data.daily || {};
    if (daily.error) { el.textContent = '状态读取失败'; return; }
    var parts = ['日线: ' + (daily.latest_date || '—')];
    if (daily.source && daily.source !== 'none') parts.push(sourceLabel(daily.source));
    if (daily.fallback_used) parts.push('已降级');
    el.textContent = parts.join(' · ');
  } catch(e) {
    el.textContent = '状态读取失败';
  }
}

// ── Section Switching (for sidebar navigation) ──
function switchSection(sectionId) {
  document.querySelectorAll('.section').forEach(function(s) { s.classList.remove('active'); });
  document.querySelectorAll('.sidebar-link').forEach(function(l) { l.classList.remove('active'); });
  var target = document.getElementById('section-' + sectionId);
  var link = document.querySelector('[data-section="' + sectionId + '"]');
  if (target) target.classList.add('active');
  if (link) link.classList.add('active');
}
