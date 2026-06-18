/**
 * 控制面板 — 前端逻辑
 *
 * 加载 → 渲染左侧导航 + 右侧内容 → 用户编辑 → 保存
 */

(function () {
  'use strict';

  var _settings = {};  // 当前配置数据
  var _meta = {};      // 参数元数据
  var _pools = [];     // 可用股票池
  var _strategies = []; // 可用选股策略
  var _monitorStrategies = []; // 可用盯盘策略
  var _activeSection = 'scan';

  // ═══════════════════════════════════════════════════════════
  // 初始化
  // ═══════════════════════════════════════════════════════════

  async function init() {
    try {
      var resp = await fetch('/api/settings');
      var data = await resp.json();
      _settings = data.data;
      _meta = data.meta;
    } catch (e) {
      console.error('加载配置失败', e);
      showToast('加载配置失败', 'error');
      return;
    }

    // 并行加载下拉数据源
    try {
      var results = await Promise.all([
        fetch('/api/pools').then(function(r){ return r.json(); }),
        fetch('/api/strategies').then(function(r){ return r.json(); }),
        fetch('/api/monitor/strategies').then(function(r){ return r.json(); }),
      ]);
      _pools = results[0].pools || [];
      _strategies = results[1].strategies || [];
      _monitorStrategies = results[2].strategies || [];
    } catch (e) {
      console.warn('加载下拉数据源失败', e);
    }

    renderNav();
    renderSection(_activeSection);
  }

  // ═══════════════════════════════════════════════════════════
  // 左侧导航
  // ═══════════════════════════════════════════════════════════

  function renderNav() {
    var nav = document.getElementById('settings-nav');
    var sections = _meta.sections || [];
    var html = '';
    sections.forEach(function (sec) {
      var isActive = sec.id === _activeSection ? ' active' : '';
      var paramCount = Object.keys(_meta.params[sec.id] || {}).length;
      html += '<a class="settings-nav-item' + isActive + '" data-section="' + sec.id + '">'
        + '<span class="nav-icon">' + sec.icon + '</span>'
        + '<span class="nav-label">' + sec.label + '</span>'
        + '<span class="nav-badge">' + paramCount + '</span>'
        + '</a>';
    });
    nav.innerHTML = html;

    nav.addEventListener('click', function (e) {
      var item = e.target.closest('.settings-nav-item');
      if (!item) return;
      e.preventDefault();
      var sectionId = item.getAttribute('data-section');
      if (sectionId === _activeSection) return;
      _activeSection = sectionId;
      // 更新导航高亮
      nav.querySelectorAll('.settings-nav-item').forEach(function (el) {
        el.classList.toggle('active', el.getAttribute('data-section') === sectionId);
      });
      renderSection(sectionId);
    });
  }

  // ═══════════════════════════════════════════════════════════
  // 右侧内容区
  // ═══════════════════════════════════════════════════════════

  function renderSection(sectionId) {
    var body = document.getElementById('settings-body');
    var section = (_meta.sections || []).find(function (s) { return s.id === sectionId; });
    var params = _meta.params[sectionId] || {};
    var values = _settings[sectionId] || {};

    if (!section) {
      body.innerHTML = '<p>未知模块</p>';
      return;
    }

    var html = '<div class="settings-section" data-section="' + sectionId + '">'
      + '<div class="settings-section-header">'
      + '<h1>' + section.icon + ' ' + section.label + '</h1>'
      + '<p>' + section.desc + '</p>'
      + '</div>';

    // 按类型分组渲染
    html += renderParamGroups(sectionId, params, values);

    // 操作按钮
    html += '<div class="settings-actions">'
      + '<button class="btn btn-primary" onclick="saveSection(\'' + sectionId + '\')">保存设置</button>'
      + '<button class="btn btn-secondary" onclick="resetSection(\'' + sectionId + '\')">恢复默认</button>'
      + '</div>'
      + '</div>';

    body.innerHTML = html;
  }

  function renderParamGroups(sectionId, params, values) {
    var html = '';
    var boolParams = [];
    var otherParams = [];

    Object.keys(params).forEach(function (key) {
      if (params[key].type === 'bool') {
        boolParams.push(key);
      } else {
        otherParams.push(key);
      }
    });

    // 数值/文本参数
    if (otherParams.length > 0) {
      html += '<div class="param-card">';
      html += '<div class="param-card-title">参数设置</div>';
      html += '<div class="param-grid">';
      otherParams.forEach(function (key) {
        html += renderParamInput(sectionId, key, params[key], values[key]);
      });
      html += '</div></div>';
    }

    // 开关参数
    if (boolParams.length > 0) {
      html += '<div class="param-card">';
      html += '<div class="param-card-title">开关选项</div>';
      boolParams.forEach(function (key) {
        html += renderToggle(sectionId, key, params[key], values[key]);
      });
      html += '</div>';
    }

    return html;
  }

  function renderParamInput(sectionId, key, meta, value) {
    var inputId = sectionId + '-' + key;
    var fullWidth = (meta.type === 'multi_select') ? ' full-width' : '';

    if (meta.type === 'select') {
      var options = resolveOptions(meta);
      var optHtml = options.map(function (o) {
        var selected = (o.value === value) ? ' selected' : '';
        return '<option value="' + o.value + '"' + selected + '>' + o.label + '</option>';
      }).join('');
      return '<div class="param-item' + fullWidth + '">'
        + '<label for="' + inputId + '">' + meta.label + '</label>'
        + '<select id="' + inputId + '" data-key="' + key + '">' + optHtml + '</select>'
        + '</div>';
    }

    if (meta.type === 'multi_select') {
      var msOptions = resolveOptions(meta);
      var checkboxes = msOptions.map(function (o) {
        var checked = Array.isArray(value) && value.indexOf(o.value) >= 0 ? ' checked' : '';
        return '<label style="display:flex;align-items:center;gap:6px;padding:4px 0;font-size:.85em;cursor:pointer;">'
          + '<input type="checkbox" data-key="' + key + '" value="' + o.value + '"' + checked + '>'
          + o.label + '</label>';
      }).join('');
      return '<div class="param-item full-width">'
        + '<label>' + meta.label + '</label>'
        + '<div style="max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px;">'
        + checkboxes + '</div></div>';
    }

    // int / float / text
    var inputType = (meta.type === 'float' || meta.type === 'int') ? 'number' : 'text';
    var step = meta.step || (meta.type === 'int' ? '1' : 'any');
    var min = meta.min !== undefined ? ' min="' + meta.min + '"' : '';
    var max = meta.max !== undefined ? ' max="' + meta.max + '"' : '';
    return '<div class="param-item' + fullWidth + '">'
      + '<label for="' + inputId + '">' + meta.label + '</label>'
      + '<input type="' + inputType + '" id="' + inputId + '" data-key="' + key + '"'
      + ' value="' + (value !== undefined && value !== null ? value : '') + '"'
      + ' step="' + step + '"' + min + max + '>'
      + '</div>';
  }

  function renderToggle(sectionId, key, meta, value) {
    var inputId = sectionId + '-' + key;
    var checked = value ? ' checked' : '';
    return '<div class="toggle-row">'
      + '<span class="toggle-label">' + meta.label + '</span>'
      + '<label class="toggle-switch">'
      + '<input type="checkbox" id="' + inputId + '" data-key="' + key + '"' + checked + '>'
      + '<span class="toggle-slider"></span>'
      + '</label>'
      + '</div>';
  }

  function resolveOptions(meta) {
    if (meta.source === 'pools') {
      return _pools.map(function (p) { return { value: p.name, label: p.name + ' — ' + p.desc }; });
    }
    if (meta.source === 'strategies') {
      return _strategies.map(function (s) { return { value: s.name, label: s.display_name || s.name }; });
    }
    if (meta.source === 'monitor_strategies') {
      return _monitorStrategies.map(function (s) { return { value: s.name, label: s.strategy_name || s.name }; });
    }
    if (meta.options) {
      return meta.options.map(function (o) { return { value: o, label: o }; });
    }
    return [];
  }

  // ═══════════════════════════════════════════════════════════
  // 保存 / 重置
  // ═══════════════════════════════════════════════════════════

  window.saveSection = function (sectionId) {
    var sectionEl = document.querySelector('.settings-section[data-section="' + sectionId + '"]');
    if (!sectionEl) return;

    var params = _meta.params[sectionId] || {};
    var updates = {};

    // 收集普通输入
    sectionEl.querySelectorAll('input[data-key], select[data-key]').forEach(function (el) {
      var key = el.getAttribute('data-key');
      var meta = params[key];
      if (!meta) return;

      if (meta.type === 'bool') {
        // checkbox
        updates[key] = el.checked;
      } else if (meta.type === 'multi_select') {
        // 跳过，单独处理
      } else if (meta.type === 'int') {
        updates[key] = parseInt(el.value, 10);
      } else if (meta.type === 'float') {
        updates[key] = parseFloat(el.value);
      } else {
        updates[key] = el.value;
      }
    });

    // 收集 multi_select
    Object.keys(params).forEach(function (key) {
      var meta = params[key];
      if (meta.type !== 'multi_select') return;
      var checked = sectionEl.querySelectorAll('input[data-key="' + key + '"]:checked');
      var values = [];
      checked.forEach(function (cb) { values.push(cb.value); });
      updates[key] = values;
    });

    fetch('/api/settings/' + sectionId, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          _settings[sectionId] = data.data;
          showToast('设置已保存', 'success');
        } else {
          showToast('保存失败: ' + (data.error || '未知错误'), 'error');
        }
      })
      .catch(function (e) {
        showToast('保存失败: ' + e.message, 'error');
      });
  };

  window.resetSection = function (sectionId) {
    if (!confirm('确定要将「' + sectionId + '」模块恢复为默认设置吗？')) return;

    fetch('/api/settings/' + sectionId + '/reset', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          _settings[sectionId] = data.data;
          renderSection(sectionId);
          showToast('已恢复默认设置', 'success');
        } else {
          showToast('重置失败: ' + (data.error || '未知错误'), 'error');
        }
      })
      .catch(function (e) {
        showToast('重置失败: ' + e.message, 'error');
      });
  };

  // ═══════════════════════════════════════════════════════════
  // Toast
  // ═══════════════════════════════════════════════════════════

  function showToast(message, type) {
    var toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast ' + (type || 'success');
    // 触发 reflow
    toast.offsetHeight;
    toast.classList.add('show');
    setTimeout(function () { toast.classList.remove('show'); }, 2500);
  }

  // ═══════════════════════════════════════════════════════════
  // 启动
  // ═══════════════════════════════════════════════════════════

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
