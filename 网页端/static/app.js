const $ = (id) => document.getElementById(id);
const state = {
  view: 'side', exercise: 'squat', file: null, exercises: [], records: [],
  foodTimer: null, section: 'pose',
  calYear: new Date().getFullYear(), calMonth: new Date().getMonth() + 1, selectedDay: null,
};

/* ------------------------------------------------ 基础工具 */

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const response = await fetch(path, { headers, ...options });
  if (response.status === 401) {
    showAuthScreen();
    throw new Error('登录已过期，请重新登录');
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (error) { /* ignore */ }
    throw new Error(detail);
  }
  return response.json();
}

let toastTimer = null;
function toast(message, options = {}) {
  const box = $('toast');
  box.innerHTML = `<span class="toast-text">${escapeHtml(message)}</span>`;
  if (options.actionLabel) {
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'toast-action';
    action.textContent = options.actionLabel;
    action.addEventListener('click', () => {
      clearTimeout(toastTimer);
      box.hidden = true;
      if (typeof options.onAction === 'function') options.onAction();
    });
    box.append(action);
  }
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, options.duration || 2600);
}

const empty = (text) => `<div class="empty">${text}</div>`;

function formatSize(bytes) {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

const escapeHtml = (text) => String(text ?? '').replace(/[&<>"']/g, (ch) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
));

/* ------------------------------------------------ 自定义下拉框
   原生 <select> 的弹层由系统绘制，在暗色界面里会是刺眼的白底，而且列不全。
   这里把每个 select 包一层玻璃弹层，但仍以原生 select 作为数据来源，
   保证读取 .value 的旧代码与 change 事件都照常工作。 */

const dropdowns = new Map();

function enhanceSelect(select) {
  if (!select.id || dropdowns.has(select.id)) return;

  const wrap = document.createElement('div');
  wrap.className = 'select-wrap' + (select.classList.contains('compact-select') ? ' select-wrap-compact' : '');
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.classList.add('select-native');

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'select-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  const value = document.createElement('span');
  value.className = 'select-value';
  const chevron = document.createElement('i');
  chevron.className = 'select-chevron';
  trigger.append(value, chevron);

  const menu = document.createElement('div');
  menu.className = 'select-menu';
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;

  wrap.append(trigger, menu);
  dropdowns.set(select.id, { select, wrap, trigger, menu, value });

  const close = () => {
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    closeAllDropdowns(select.id);
    renderDropdown(select.id);
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
  };

  trigger.addEventListener('click', (event) => {
    event.stopPropagation();
    if (menu.hidden) open(); else close();
  });
  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
      const items = [...menu.querySelectorAll('.select-option')];
      const current = menu.querySelector('.select-option.is-selected') || items[0];
      if (current) current.focus();
    }
  });
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.select-option');
    if (!option) return;
    event.stopPropagation();
    chooseDropdownValue(select.id, option.dataset.value);
  });
  menu.addEventListener('keydown', (event) => {
    const items = [...menu.querySelectorAll('.select-option')];
    const index = items.indexOf(document.activeElement);
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      (items[index + 1] || items[0]).focus();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      (items[index - 1] || items[items.length - 1]).focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (index >= 0) chooseDropdownValue(select.id, items[index].dataset.value);
    } else if (event.key === 'Escape' || event.key === 'Tab') {
      close();
      trigger.focus();
    }
  });
  wrap.addEventListener('click', (event) => event.stopPropagation());

  renderDropdown(select.id);
}

function renderDropdown(id) {
  const entry = dropdowns.get(id);
  if (!entry) return;
  const { select, menu, value } = entry;
  const options = [...select.options];
  const selected = options.find((option) => option.value === select.value) || options[0];
  value.textContent = selected ? selected.textContent : '请选择';
  menu.innerHTML = options.length
    ? options.map((option) => `<div class="select-option${option.value === select.value ? ' is-selected' : ''}"
          role="option" tabindex="-1" data-value="${escapeHtml(option.value)}"
          aria-selected="${option.value === select.value}">
          <span>${escapeHtml(option.textContent)}</span>
          ${option.value === select.value ? '<span class="select-check">✓</span>' : ''}
        </div>`).join('')
    : '<div class="select-option is-empty">暂无可选项</div>';
}

function refreshSelect(id) {
  renderDropdown(id);
}

function chooseDropdownValue(id, nextValue) {
  const entry = dropdowns.get(id);
  if (!entry) return;
  const { select } = entry;
  if (select.value !== nextValue) {
    select.value = nextValue;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
  closeAllDropdowns();
  renderDropdown(id);
  entry.trigger.focus();
}

function closeAllDropdowns(exceptId) {
  dropdowns.forEach((entry, id) => {
    if (id === exceptId) return;
    entry.menu.hidden = true;
    entry.trigger.setAttribute('aria-expanded', 'false');
  });
}

document.addEventListener('click', () => closeAllDropdowns());

/* ------------------------------------------------ 模块切换 */

function showSection(name) {
  state.section = name;
  document.querySelectorAll('.section').forEach((section) => section.classList.remove('is-active'));
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('is-active'));
  $('section-' + name).classList.add('is-active');
  document.querySelector(`.nav-item[data-section="${name}"]`).classList.add('is-active');
  if (name === 'eat') { loadNutrition(); loadVisionStatus(); }
  if (name === 'me') { loadProfile(); loadPlanChanges(); loadHealth(); }
  if (name === 'log') loadHistory();
  if (name === 'posture') loadPostureHistory();
}

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => showSection(item.dataset.section));
});

document.addEventListener('keydown', (event) => {
  const tag = (event.target.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
  if (typing) {
    if (event.key === 'Enter' && event.target.id === 'log-text' && !event.shiftKey) {
      event.preventDefault();
      $('log-parse').click();
    }
    return;
  }
  if (['1', '2', '3', '4', '5'].includes(event.key)) {
    showSection({ 1: 'pose', 2: 'log', 3: 'eat', 4: 'posture', 5: 'me' }[event.key]);
  }
  if (event.key === '/') {
    event.preventDefault();
    showSection('eat');
    setTimeout(() => $('food-search').focus(), 50);
  }
});

/* ------------------------------------------------ 动作自检 */

document.querySelectorAll('#pose-view .seg-item').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('#pose-view .seg-item').forEach((item) => item.classList.remove('is-active'));
    button.classList.add('is-active');
    state.view = button.dataset.view;
    // 记住这个动作选过的角度，切走再切回来时能恢复
    if (state.exercise) poseViewMemory[state.exercise] = state.view;
  });
});

let poseExercises = [];

const POSE_GUIDES = {
  squat: {
    rules: [
      '全身入镜，头顶到脚底都要在画面里。',
      '手机固定好，距离 2 至 3 米，不要手持跟拍。',
      '侧面拍摄时镜头与髋部同高，正面拍摄时放在膝盖高度附近。',
      '光线从正面来，不要背对窗户拍摄。',
      '一次做 3 至 5 个，站定 1 秒后再开始下蹲。',
    ],
    title: '为什么要拍两段',
    body: '侧面用来判断下蹲深度、躯干前倾与起身时序；正面用来判断膝内扣。用侧面视频是测不出膝内扣的。',
  },
  deadlift: {
    rules: [
      '从侧面拍，人和杠铃都要在画面里。',
      '手机放在膝盖高度，距离 2 至 3 米，保持不动。',
      '要让起始的臀部位置与最后的锁定位置都拍进去。',
      '一次做 3 至 5 次，每次落下后停 1 秒再起。',
      '穿深色衣服时避开深色背景，避免关键点丢失。',
    ],
    title: '系统在看什么',
    body: '起始躯干前倾角、起身时髋膝是否同步、锁定位置有没有过度后仰、结束有没有完全站直。',
  },
  bench_press: {
    rules: [
      '从侧面上方或斜 45 度拍，保证肩、肘、腕、髋都能看见。',
      '手机固定在三脚架或凳子上，不要手持。',
      '画面里要包含整个平凳与头部。',
      '一次做 3 至 5 次，每次在胸部上方停 1 秒再推。',
      '注意光线，深色杠铃片容易挡住手肘。',
    ],
    title: '系统在看什么',
    body: '最低点肘角判断是否下放到胸，最高点肘角判断是否推直，肩髋连线判断臀部有没有离凳，两侧手肘角度差异判断发力是否均衡。',
  },
  pushup: {
    rules: [
      '手机放在地面高度，从正侧面拍。',
      '让肩、髋、踝三个点都能看见，不要只拍上半身。',
      '距离 2 米左右，画面留出地面。',
      '一次做 5 至 10 个，保持节奏稳定。',
      '铺一块垫子，避免手机被震动影响。',
    ],
    title: '系统在看什么',
    body: '最低点肘角判断下放幅度，肩髋踝夹角判断身体有没有塌腰或撅臀。',
  },
  pullup: {
    rules: [
      '正面或侧面拍都行，手机固定、距离 2 至 3 米。',
      '画面里要能看到双手、下巴和髋部，不要俯拍。',
      '最好连单杠一起拍进去，方便目测下巴有没有过杠。',
      '一次做 3 至 5 个，节奏稳定，不要晃动。',
      '腿弯着或伸直都行；侧面拍还能额外判断身体有没有前后摆动。',
    ],
    title: '系统在看什么',
    body: '顶部手肘角度与下巴是否过杠、下放时手臂是否伸直、髋部有没有前后摆动借用惯性。',
  },
  custom: {
    rules: [
      '输入动作名称后，系统会按动作主关节自动识别次数。',
      '全身入镜、侧面或正面均可，主关节（膝/肘/髋）要在画面里清晰可见。',
      '手机固定、距离 2 至 3 米，一次做 3 至 8 个，节奏稳定。',
      '深蹲、硬拉、卧推、俯卧撑、引体向上请用上面的专属动作，精度更高。',
      '判定由大模型结合几何量程完成，结果供参考。',
    ],
    title: '自由动作怎么判',
    body: '几何层自动找出运动幅度最大的关节用来数次数，大模型再结合角度量程给动作质量打分与纠错。',
  },
};

function updatePoseGuide() {
  const id = $('pose-exercise').value;
  const guide = POSE_GUIDES[id] || POSE_GUIDES.squat;
  $('pose-rules').innerHTML = guide.rules.map((text) => `<li>${text}</li>`).join('');
  $('pose-callout-title').textContent = guide.title;
  $('pose-callout-body').textContent = guide.body;
}

/* 接口拿不到动作清单时的兜底，保证下拉框不会空着 */
const POSE_FALLBACK_EXERCISES = [
  { id: 'squat', label: '深蹲', views: ['side', 'front'], default_view: 'side' },
  { id: 'deadlift', label: '硬拉', views: ['side'], default_view: 'side' },
  { id: 'bench_press', label: '卧推', views: ['side', 'front'], default_view: 'side' },
  { id: 'pushup', label: '俯卧撑', views: ['side'], default_view: 'side' },
  { id: 'pullup', label: '引体向上', views: ['front', 'side'], default_view: 'front' },
  { id: 'custom', label: '自由动作', views: ['side', 'front'], default_view: 'side', custom: true },
];

async function loadPoseExercises() {
  try {
    poseExercises = await api('/api/pose/exercises');
  } catch (error) {
    poseExercises = POSE_FALLBACK_EXERCISES;
    toast('动作清单读取失败，已使用本地默认列表');
  }
  if (!Array.isArray(poseExercises) || !poseExercises.length) {
    poseExercises = POSE_FALLBACK_EXERCISES;
  }
  const select = $('pose-exercise');
  select.innerHTML = poseExercises.map((item) => `<option value="${item.id}">${escapeHtml(item.label)}</option>`).join('');
  select.value = 'squat';
  state.exercise = 'squat';
  refreshSelect('pose-exercise');
  updatePoseHint();
}

function currentPoseExercise() {
  return poseExercises.find((item) => item.id === $('pose-exercise').value) || { id: 'squat', label: '深蹲' };
}

/* 记住每个动作上次选过的拍摄角度，切换动作时不再把用户的选择悄悄改掉 */
const poseViewMemory = {};

function updatePoseHint() {
  const item = currentPoseExercise();
  $('pose-hint').textContent = item.hint || (item.custom ? '输入任意动作名称，由大模型结合几何量程通用判定' : '');
  // 自由动作：显示动作名称输入框
  $('pose-custom-row').hidden = !item.custom;
  // 有些动作只支持侧面拍摄，这时禁用正面按钮
  const supportsFront = (item.views || ['side', 'front']).includes('front');
  const frontButton = document.querySelector('#pose-view .seg-item[data-view="front"]');
  frontButton.disabled = !supportsFront;
  frontButton.style.opacity = supportsFront ? '1' : '.4';
  // 优先沿用这个动作上次选的角度，其次用它的默认角度；正面不支持时强制侧面
  const remembered = poseViewMemory[item.id];
  state.view = supportsFront ? (remembered || item.default_view || 'side') : 'side';
  document.querySelectorAll('#pose-view .seg-item').forEach((btn) => btn.classList.toggle('is-active', btn.dataset.view === state.view));
  updatePoseGuide();
}

$('pose-exercise').addEventListener('change', () => {
  // 先把上一个动作选过的角度记下来，再切到新动作
  if (state.exercise) poseViewMemory[state.exercise] = state.view;
  state.exercise = $('pose-exercise').value;
  updatePoseHint();
});

function pickFile(file) {
  if (!file) return;
  state.file = file;
  $('pose-filename').textContent = `已选择：${file.name}（${(file.size / 1048576).toFixed(1)} MB）`;
  $('pose-start').disabled = false;
}

$('pose-file').addEventListener('change', (event) => pickFile(event.target.files[0]));

const dropzone = $('pose-drop');
['dragenter', 'dragover'].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add('is-over');
  });
});
['dragleave', 'drop'].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove('is-over');
  });
});
dropzone.addEventListener('drop', (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) pickFile(file);
});

$('pose-start').addEventListener('click', () => {
  if (!state.file) return;
  const exercise = $('pose-exercise').value;
  const customName = $('pose-custom-name').value.trim();
  if (exercise === 'custom' && !customName) {
    toast('请先输入动作名称');
    $('pose-custom-name').focus();
    return;
  }
  const form = new FormData();
  form.append('file', state.file);
  form.append('view', state.view);
  form.append('exercise', exercise);
  if (exercise === 'custom') form.append('custom_name', customName);

  $('pose-progress').hidden = false;
  $('pose-result').hidden = true;
  $('pose-start').disabled = true;
  setProgress(0, '正在上传视频…');

  const request = new XMLHttpRequest();
  request.open('POST', '/api/pose/analyze');
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) setProgress(Math.round((event.loaded / event.total) * 88), '正在上传视频…');
  };
  request.onload = () => {
    $('pose-start').disabled = false;
    if (request.status === 401) {
      showAuthScreen();
      setProgress(0, '登录已过期，请重新登录');
      return;
    }
    if (request.status !== 200) {
      setProgress(0, '上传失败，请重试');
      toast('上传失败：' + request.status);
      return;
    }
    setProgress(92, '正在分析动作…');
    pollPose(JSON.parse(request.responseText).job_id, 0);
  };
  request.onerror = () => {
    $('pose-start').disabled = false;
    setProgress(0, '网络错误，请重试');
  };
  request.send(form);
});

function setProgress(percent, text) {
  $('pose-progress-fill').style.width = percent + '%';
  $('pose-progress-value').textContent = percent + '%';
  if (text) $('pose-progress-text').textContent = text;
}

function pollPose(jobId, attempt) {
  if (attempt > 80) {
    setProgress(0, '分析超时，请换一段更短的视频');
    return;
  }
  setTimeout(async () => {
    try {
      const job = await api('/api/pose/jobs/' + jobId);
      if (job.status === 'done') {
        $('pose-progress').hidden = true;
        renderPoseResult(job);
      } else if (job.status === 'failed') {
        setProgress(0, '分析失败：' + (job.error || '未知错误'));
        toast('分析失败，请看提示');
      } else {
        setProgress(92, '正在分析动作…（约 3 至 10 秒）');
        pollPose(jobId, attempt + 1);
      }
    } catch (error) {
      setProgress(0, '查询失败：' + error.message);
    }
  }, 1400);
}

/* ------------------------------------------------ 动作自检 · 纠正教程 + 正确姿势简图 */

function stickSVG(segments, joints, highlights, extra, vb) {
  const seg = segments.map((p) =>
    `<line x1="${p[0]}" y1="${p[1]}" x2="${p[2]}" y2="${p[3]}" stroke="#5eead4" stroke-width="6" stroke-linecap="round"/>`
  ).join('');
  const pts = joints.map(([cx, cy, r]) =>
    `<circle cx="${cx}" cy="${cy}" r="${r || 4.5}" fill="#2dd4bf"/>`
  ).join('');
  const hl = (highlights || []).map(([cx, cy, r]) =>
    `<circle cx="${cx}" cy="${cy}" r="${r || 10}" fill="none" stroke="#4ade80" stroke-width="3"/>`
  ).join('');
  const ex = extra || '';
  return `<svg viewBox="${vb || '0 0 200 220'}" class="posture-svg" xmlns="http://www.w3.org/2000/svg">${seg}${pts}${hl}${ex}</svg>`;
}

const MUTED_LINE = (x1, y1, x2, y2, dash) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#94a3b8" stroke-width="2" ${dash ? 'stroke-dasharray="4 4"' : ''}/>`;
const SVG_TEXT = (x, y, text) =>
  `<text x="${x}" y="${y}" fill="#cbd5e1" font-size="12">${text}</text>`;

const CORRECT_POSTURE = {
  squat: {
    svg: stickSVG(
      [[95, 62, 88, 108], [88, 108, 66, 152], [66, 152, 64, 200], [64, 200, 92, 202], [95, 68, 128, 92], [128, 92, 150, 120]],
      [[100, 44, 13], [95, 62, 4.5], [88, 108, 4.5], [66, 152, 4.5], [64, 200, 4.5], [128, 92, 4.5], [150, 120, 4.5]],
      [[66, 152, 11]],
      MUTED_LINE(88, 108, 40, 108, true) + SVG_TEXT(46, 100, '大腿≈水平') + SVG_TEXT(96, 218, '膝盖对准脚尖'),
      '0 0 200 228'
    ),
    caption: '正确深蹲：大腿蹲到接近平行，膝盖对准脚尖，背部挺直。',
  },
  deadlift: {
    svg: stickSVG(
      [[95, 58, 90, 104], [90, 104, 86, 148], [86, 148, 84, 196], [84, 196, 108, 198], [95, 64, 100, 112], [100, 112, 98, 150]],
      [[100, 42, 13], [95, 58, 4.5], [90, 104, 4.5], [86, 148, 4.5], [84, 196, 4.5], [100, 112, 4.5], [98, 150, 4.5]],
      [[90, 104, 11], [95, 58, 11]],
      MUTED_LINE(66, 150, 132, 150) + SVG_TEXT(96, 218, '背部挺直 · 髋膝同步'),
      '0 0 200 228'
    ),
    caption: '正确硬拉：背部挺直，髋膝同步起身，站直锁定不后仰。',
  },
  bench_press: {
    svg: stickSVG(
      [[80, 70, 125, 70], [80, 70, 52, 92], [52, 92, 28, 92], [28, 92, 14, 92], [125, 70, 124, 16]],
      [[150, 58, 13], [125, 70, 4.5], [80, 70, 4.5], [52, 92, 4.5], [28, 92, 4.5], [124, 16, 4.5]],
      [[124, 16, 10]],
      MUTED_LINE(12, 96, 178, 96) + MUTED_LINE(95, 16, 155, 16) + MUTED_LINE(125, 70, 124, 16, true) + SVG_TEXT(96, 214, '杠铃在胸口正上方'),
      '0 0 200 220'
    ),
    caption: '正确卧推：杠铃在胸口正上方，手肘推直，肩胛收紧贴凳。',
  },
  pushup: {
    svg: stickSVG(
      [[120, 80, 80, 84], [80, 84, 44, 86], [44, 86, 14, 88], [120, 80, 138, 96], [138, 96, 150, 100]],
      [[152, 64, 12], [120, 80, 4.5], [80, 84, 4.5], [44, 86, 4.5], [14, 88, 4.5], [138, 96, 4.5], [150, 100, 4.5]],
      [[80, 84, 11]],
      MUTED_LINE(14, 88, 152, 64, true) + SVG_TEXT(20, 200, '肩·髋·踝成一条直线'),
      '0 0 200 220'
    ),
    caption: '正确俯卧撑：肩、髋、踝成一条直线，核心收紧不下塌。',
  },
  pullup: {
    svg: stickSVG(
      [[50, 52, 60, 104], [60, 104, 70, 136], [150, 52, 140, 104], [140, 104, 130, 136], [70, 136, 80, 186], [130, 136, 120, 186], [80, 186, 82, 218], [120, 186, 118, 218]],
      [[100, 26, 13], [50, 52, 4.5], [150, 52, 4.5], [60, 104, 4.5], [140, 104, 4.5], [70, 136, 4.5], [130, 136, 4.5], [100, 186, 4.5]],
      [[100, 26, 11]],
      MUTED_LINE(36, 52, 164, 52) + SVG_TEXT(60, 218, '下巴过杠 · 下放伸直手臂'),
      '0 0 200 228'
    ),
    caption: '正确引体向上：下巴拉过杠，下放时手臂完全伸直。',
  },
};

function openTutorial(issue, exercise) {
  if (!issue) return;
  const figure = CORRECT_POSTURE[exercise] || null;
  const steps = (issue.fixes || []).map((f) =>
    `<li><strong>${escapeHtml(f.name)}</strong>　${escapeHtml(f.how)}</li>`
  ).join('');
  $('tutorial-modal-title').textContent = `纠正教程 · ${issue.short || issue.name || ''}`;
  $('tutorial-body').innerHTML = `
    <div class="tutorial-head">
      <span class="chip chip-danger">${escapeHtml(issue.short || issue.name || '')}</span>
      <h3>${escapeHtml(issue.name || '')}</h3>
    </div>
    ${issue.why ? `<p class="tutorial-why">${escapeHtml(issue.why)}</p>` : ''}
    ${figure ? `<div class="tutorial-figure">${figure.svg}<div class="tutorial-caption">${figure.caption}</div></div>` : ''}
    <div class="tutorial-steps"><h4>怎么改</h4><ol>${steps}</ol></div>`;
  $('tutorial-modal').hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeTutorial() {
  if ($('tutorial-modal').hidden) return;
  $('tutorial-modal').hidden = true;
  document.body.style.overflow = '';
}

function renderTimeline(job) {
  const track = $('pose-timeline');
  const errors = job.error_frames || [];
  track.innerHTML = '';
  if (!errors.length) { track.hidden = true; return; }
  const video = $('pose-video');
  const dur = video.duration;
  if (!isFinite(dur) || dur <= 0) { track.hidden = true; return; }
  track.hidden = false;
  errors.forEach((err) => {
    const dot = document.createElement('button');
    dot.type = 'button';
    dot.className = 'timeline-dot';
    dot.style.left = `calc(${Math.min(99, (err.time_seconds / dur) * 100)}% - 6px)`;
    dot.title = `第${err.rep}次 · ${err.label}`;
    dot.addEventListener('click', () => {
      video.currentTime = err.time_seconds;
      video.play().catch(() => {});
    });
    track.appendChild(dot);
  });
}

function renderPoseResult(job) {
  const issues = job.issues || [];
  const exercise = job.exercise || 'squat';
  const exerciseLabel = job.exercise_label || '深蹲';
  const viewLabel = job.view === 'front' ? '正面' : '侧面';
  $('pose-result').hidden = false;
  $('pose-advice-panel').hidden = false;
  $('pose-issues-panel').hidden = false;
  $('pose-metrics-panel').hidden = false;
  $('pose-empty').hidden = true;

  $('pose-video').src = job.overlay_video;
  document.querySelector('#pose-result .panel-head h2').textContent = `分析画面 · ${exerciseLabel}（${viewLabel}）`;
  $('advice-source').textContent = job.advice_source === '大模型' ? '大模型生成' : '规则生成';
  $('pose-advice').textContent = job.advice || '';

  const pill = $('pose-status-pill');
  const firstHigh = issues.find((item) => item.severity === 'high');
  pill.textContent = issues.length
    ? (firstHigh ? `优先纠正：${firstHigh.name}` : `建议改进 ${issues.length} 处`)
    : '动作整体规范';
  pill.className = 'chip ' + (issues.length ? 'chip-danger chip-clickable' : 'chip-ok');
  $('pose-card-link').href = job.report_card || '#';

  // 记录当前结果，供「查看示范 / 红点 / 逐次点评」点击时查找问题详情
  state.poseExercise = exercise;
  state.poseIssues = issues;
  const issueLookup = {};
  issues.forEach((item) => { if (item.key) issueLookup[item.key] = item; });
  (job.per_rep_issues || []).forEach((rep) => {
    (rep.issues || []).forEach((item) => { if (item.key && !issueLookup[item.key]) issueLookup[item.key] = item; });
  });
  state.poseIssueLookup = issueLookup;

  $('pose-issues').innerHTML = issues.length
    ? issues.map((item) => {
        const fixes = (item.fixes || [])
          .map((fix) => `<div class="fix-item"><strong>${escapeHtml(fix.name)}</strong>　${escapeHtml(fix.how)}</div>`)
          .join('');
        return `<div class="issue ${item.severity === 'high' ? 'high' : ''}">
            <div class="issue-head">
              <span class="chip ${item.severity === 'high' ? 'chip-danger' : 'chip-warn'}">
                ${item.severity === 'high' ? '优先纠正' : '建议改进'}
              </span>
              <span class="issue-name">${escapeHtml(item.name)}</span>
              <button type="button" class="btn btn-ghost btn-sm" data-issue-key="${escapeHtml(item.key || '')}">查看示范</button>
            </div>
            ${item.why ? `<p>${escapeHtml(item.why)}</p>` : ''}
            ${fixes ? `<div class="fix-block"><div class="fix-label">怎么改</div>${fixes}</div>` : ''}
            <p class="evidence">角度依据：${escapeHtml(item.evidence)}</p>
          </div>`;
      }).join('')
    : `<div class="issue ok"><div class="issue-head">动作整体规范</div><p>${escapeHtml(job.summary || '')}</p></div>`;

  // 逐次点评
  const perRep = job.per_rep_issues || [];
  $('pose-per-rep-panel').hidden = !perRep.length;
  $('pose-per-rep').innerHTML = perRep.map((rep) => {
    const chips = (rep.issues && rep.issues.length)
      ? rep.issues.map((it) => `<button type="button" class="rep-chip" data-issue-key="${escapeHtml(it.key || '')}">${escapeHtml(it.short || it.name)}</button>`).join('')
      : `<span class="rep-chip ok">规范</span>`;
    return `<div class="rep-row"><span class="rep-no">第${rep.rep}次</span><span class="rep-chips">${chips}</span></div>`;
  }).join('');

  // 体态联动提示
  const note = job.posture_note;
  $('posture-note').hidden = !(note && note.text);
  if (note && note.text) {
    $('posture-note-text').textContent = note.text;
    const warm = note.warmup || [];
    $('posture-note-warmup').textContent = warm.length ? `推荐热身：${warm.join(' · ')}` : '';
  }

  // 时间轴红点（拿到视频真实时长后定位）
  const video = $('pose-video');
  video.onloadedmetadata = () => renderTimeline(job);
  renderTimeline(job);

  const metrics = job.metrics || {};
  const rows = [];
  if (exercise === 'squat') {
    rows.push(['最低点膝关节角度', metrics.min_knee_angle, '度', 1]);
    rows.push(['最低点躯干前倾角', metrics.torso_lean_at_bottom, '度', 1]);
    // 膝内扣只在正面视角下有意义，侧面视频两条腿重叠，这个数值没有参考价值
    if (job.view === 'front') {
      rows.push(['膝内移比例', metrics.valgus_ratio_at_bottom == null ? null : metrics.valgus_ratio_at_bottom * 100, '%', 0]);
    }
    rows.push(['起身髋膝速率比', metrics.hip_first_ratio, '', 2]);
  } else if (exercise === 'deadlift') {
    rows.push(['起始躯干前倾角', metrics.start_lean, '度', 1]);
    rows.push(['锁定位置躯干角度', metrics.lockout_lean, '度', 1]);
    rows.push(['全程最大膝关节角度', metrics.max_knee_angle, '度', 1]);
    rows.push(['起身髋膝速率比', metrics.hip_first_ratio, '', 2]);
  } else if (exercise === 'bench_press') {
    rows.push(['最低点肘角', metrics.min_elbow_angle, '度', 1]);
    rows.push(['最高点肘角', metrics.max_elbow_angle, '度', 1]);
    rows.push(['肩髋偏离水平最大角度', metrics.hip_lift_angle_max, '度', 1]);
    rows.push(['两侧肘角最大差异', metrics.elbow_asymmetry_max, '度', 1]);
  } else if (exercise === 'pullup') {
    rows.push(['最低点肘角', metrics.min_elbow_angle, '度', 1]);
    rows.push(['最高点肘角', metrics.max_elbow_angle, '度', 1]);
    rows.push(['顶部下巴过杠量', metrics.chin_margin_at_top, '', 2]);
    rows.push(['身体摆动幅度', metrics.swing_ratio == null ? null : metrics.swing_ratio * 100, '%', 0]);
  } else if (exercise === 'custom') {
    // 自由动作：几何层只负责量程统计（大模型给结论），这里展示主关节角度范围
    const span = (key) => {
      const nested = metrics[key];
      if (nested && typeof nested === 'object' && nested.min != null) return [nested.min, nested.max];
      return [metrics[key + '_min'], metrics[key + '_max']];
    };
    [['knee_angle', '膝关节'], ['elbow_angle', '肘关节'], ['hip_angle', '髋关节']].forEach(([key, label]) => {
      const pair = span(key);
      if (pair[0] == null && pair[1] == null) return;
      const lo = pair[0] == null ? pair[1] : pair[0];
      const hi = pair[1] == null ? pair[0] : pair[1];
      rows.push([`${label}范围`, `${Number(lo).toFixed(0)} ~ ${Number(hi).toFixed(0)} 度`, '', 2]);
    });
    const lean = span('torso_lean');
    if (lean[0] != null || lean[1] != null) {
      rows.push(['躯干前倾范围', `${Number(lean[0] ?? lean[1]).toFixed(0)} ~ ${Number(lean[1] ?? lean[0]).toFixed(0)} 度`, '', 2]);
    }
  } else {
    rows.push(['最低点肘角', metrics.min_elbow_angle, '度', 1]);
    rows.push(['肩髋踝最小夹角', metrics.min_body_line_angle, '度', 1]);
  }
  rows.push(['有效帧数', metrics.valid_frames, '帧', 0]);
  $('pose-metrics').innerHTML = rows.map(([label, value, unit, digits]) => {
    const text = (value === null || value === undefined) ? '未计算'
      : (typeof value === 'string' ? value : Number(value).toFixed(digits) + unit);
    return `<div class="stat"><span>${label}</span><strong>${text}</strong></div>`;
  }).join('');
}

/* ------------------------------------------------ 训练记录 */

async function loadExercises() {
  if (state.exercises.length) return state.exercises;
  state.exercises = await api('/api/exercises');
  $('log-exercise').innerHTML = state.exercises.map((item) => `<option>${item.name}</option>`).join('');
  refreshSelect('log-exercise');
  return state.exercises;
}

/* ------------------------------------------------ 训练日历与当日明细 */

const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

function todayISO() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function formatVolume(kg) {
  if (!kg) return '0';
  return kg >= 1000 ? `${(kg / 1000).toFixed(1)}t` : `${Math.round(kg)}kg`;
}

function formatDayTitle(iso) {
  const target = new Date(`${iso}T00:00:00`);
  const label = `${target.getMonth() + 1} 月 ${target.getDate()} 日 周${WEEKDAY_LABELS[target.getDay()]}`;
  return iso === todayISO() ? `${label}（今天）` : label;
}

async function loadCalendar(year, month, keepSelection = true) {
  const data = await api(`/api/records/calendar?year=${year}&month=${month}`);
  state.calYear = data.year;
  state.calMonth = data.month;
  $('cal-title').textContent = `${data.year} 年 ${data.month} 月`;

  const first = new Date(`${data.start}T00:00:00`);
  const daysInMonth = Number(data.end.slice(8, 10));
  const offset = (first.getDay() + 6) % 7;   // 把周一放到第一列
  const cells = [];
  for (let index = 0; index < offset; index += 1) cells.push('<div class="cal-day is-blank"></div>');
  for (let day = 1; day <= daysInMonth; day += 1) {
    const iso = `${data.year}-${String(data.month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const info = data.days[iso];
    const classes = ['cal-day'];
    if (info) classes.push('has-data');
    if (iso === data.today) classes.push('is-today');
    if (iso === state.selectedDay) classes.push('is-selected');
    cells.push(`<div class="${classes.join(' ')}" data-day="${iso}" ${info ? 'role="button" tabindex="0"' : ''}>
        <span>${day}</span>
        ${info ? `<span class="cal-volume">${formatVolume(info.volume)}</span>` : ''}
      </div>`);
  }
  $('cal-grid').innerHTML = cells.join('');
  $('cal-grid').querySelectorAll('.cal-day.has-data').forEach((cell) => {
    const open = () => selectDay(cell.dataset.day);
    cell.addEventListener('click', open);
    cell.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });
  });

  $('cal-summary').textContent = data.train_days
    ? `本月练了 ${data.train_days} 天，总容量 ${formatVolume(data.month_volume)}。点有颜色的日期看当天明细。`
    : '本月还没有训练记录。在左边记录一次训练，日期会自动落到日历上。';

  if (!keepSelection || !state.selectedDay) {
    const fallback = data.today.startsWith(`${year}-${String(month).padStart(2, '0')}`) ? data.today : null;
    if (fallback) await selectDay(fallback);
  } else {
    // 保留当前选中的日期，但明细要重新拉一次，否则新增或删除记录后面板还是旧数据
    await selectDay(state.selectedDay);
  }
  return data;
}

async function selectDay(iso) {
  state.selectedDay = iso;
  $('cal-grid').querySelectorAll('.cal-day').forEach((cell) => {
    cell.classList.toggle('is-selected', cell.dataset.day === iso);
  });
  const data = await api('/api/records/day?day=' + iso);
  const summary = data.summary;
  $('day-title').textContent = formatDayTitle(iso);
  $('day-chip').textContent = summary.count ? `${summary.count} 条记录` : '没有记录';
  $('day-stats').innerHTML = [
    ['动作数', summary.exercises.length ? `${summary.exercises.length} 个` : '—'],
    ['总组数', summary.count ? `${summary.sets} 组` : '—'],
    ['总次数', summary.count ? `${summary.reps} 次` : '—'],
    ['总容量', summary.count ? formatVolume(summary.volume) : '—'],
  ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`).join('');

  $('day-tbody').innerHTML = summary.count
    ? data.records.map((item) => `<tr>
        <td class="strong">${escapeHtml(item.exercise_name || '未命名动作')}</td>
        <td class="num">${item.weight_kg} kg</td>
        <td class="num">${item.sets} × ${item.reps}</td>
        <td class="num">${formatVolume(item.volume)}</td>
      </tr>`).join('')
    : `<tr><td colspan="4">${empty('这一天没有训练记录')}</td></tr>`;

  // 同一天的多次训练合并显示，方便一眼看到当天练了什么
  if (summary.exercises.length > 1) {
    $('day-chip').textContent = `${summary.count} 条记录 · ${summary.exercises.length} 个动作`;
  }
  return data;
}

$('cal-prev').addEventListener('click', () => {
  const month = state.calMonth - 1;
  loadCalendar(month < 1 ? state.calYear - 1 : state.calYear, month < 1 ? 12 : month, false);
});
$('cal-next').addEventListener('click', () => {
  const month = state.calMonth + 1;
  loadCalendar(month > 12 ? state.calYear + 1 : state.calYear, month > 12 ? 1 : month, false);
});

/* ------------------------------------------------ AI 训练建议 */

$('advice-btn').addEventListener('click', async () => {
  const button = $('advice-btn');
  button.disabled = true;
  $('advice-result').innerHTML = `<p class="muted">正在读取最近 30 天的记录并分析…</p>`;
  try {
    const data = await api('/api/coach/advice', { method: 'POST' });
    const chip = $('advice-source-chip');
    chip.textContent = data.source === '大模型' ? '大模型生成' : '规则生成';
    chip.className = 'chip ' + (data.source === '大模型' ? 'chip-ok' : 'chip-muted');
    const insight = data.insight || {};
    $('advice-result').innerHTML = `
      <div class="advice">
        <div class="advice-head">
          <span>训练建议</span>
          <span class="chip chip-muted">近 30 天 ${data.range?.records ?? 0} 条记录</span>
        </div>
        <p class="advice-text">${escapeHtml(data.advice)}</p>
      </div>
      <div class="stat-grid">
        <div class="stat"><span>近 7 天训练</span><strong>${insight.train_days_last_7 ?? 0} 天</strong></div>
        <div class="stat"><span>近 7 天容量</span><strong>${formatVolume(insight.volume_last_7 ?? 0)}</strong></div>
        <div class="stat"><span>容量环比</span><strong>${insight.volume_trend_percent == null ? '—' : insight.volume_trend_percent + '%'}</strong></div>
      </div>`;
  } catch (error) {
    $('advice-result').innerHTML = `<div class="issue high"><div class="issue-head">生成失败</div><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    button.disabled = false;
  }
});

$('log-demo').addEventListener('click', () => {
  $('log-text').value = '今天深蹲 60 公斤 5 组 5 次，最后一组有点吃力';
  $('log-text').focus();
});

$('log-parse').addEventListener('click', async () => {
  const text = $('log-text').value.trim();
  if (!text) return toast('先写一句训练记录');
  await loadExercises();
  const parsed = await api('/api/records/parse', { method: 'POST', body: JSON.stringify({ text }) });
  $('log-parsed').hidden = false;
  const confidence = $('log-confidence');
  const source = parsed.parser === '大模型' ? '大模型解析' : '规则解析';
  confidence.textContent = parsed.missing.length
    ? `${source} · 缺少：${parsed.missing.join('、')}`
    : `${source} · 完整`;
  confidence.className = 'chip ' + (parsed.missing.length ? 'chip-warn' : 'chip-ok');
  if (parsed.exercise) {
    const select = $('log-exercise');
    // 解析出来的动作不在清单里时临时补一个选项，否则选择框会静默停在别的动作上，保存后就记错了
    const exists = [...select.options].some((option) => option.value === parsed.exercise);
    if (!exists) select.insertAdjacentHTML('beforeend', `<option>${escapeHtml(parsed.exercise)}</option>`);
    select.value = parsed.exercise;
    refreshSelect('log-exercise');
  }
  $('log-weight').value = parsed.weight_kg ?? '';
  $('log-sets').value = parsed.sets ?? '';
  $('log-reps').value = parsed.reps ?? '';
  $('log-rpe').value = parsed.rpe ?? 8;
  $('log-completed').checked = !!parsed.completed;
  $('log-date').value = parsed.trained_on || todayISO();
  updateLogVolume();
  if (parsed.trained_on) {
    toast(`识别到训练日期：${parsed.trained_on}`);
  }
});

/* 输入重量/组数/次数时就实时算出容量，保存前就能看到这次练了多少 */
function updateLogVolume() {
  const weight = Number($('log-weight').value || 0);
  const sets = Number($('log-sets').value || 0);
  const reps = Number($('log-reps').value || 0);
  const volume = weight * sets * reps;
  $('log-volume').value = volume > 0
    ? `${volume} kg（${weight} × ${sets} × ${reps}）`
    : (sets && reps ? `自重动作，共 ${sets * reps} 次` : '—');
}
['log-weight', 'log-sets', 'log-reps'].forEach((id) => {
  $(id).addEventListener('input', updateLogVolume);
});

$('log-save').addEventListener('click', async () => {
  const payload = {
    exercise: $('log-exercise').value,
    weight_kg: Number($('log-weight').value || 0),
    sets: Number($('log-sets').value || 0),
    reps: Number($('log-reps').value || 0),
    rpe: Number($('log-rpe').value || 8),
    completed: $('log-completed').checked,
    feeling: $('log-text').value.trim(),
    trained_on: $('log-date').value || todayISO(),
  };
  if (!payload.sets || !payload.reps) return toast('组数和次数要填');
  const result = await api('/api/records', { method: 'POST', body: JSON.stringify(payload) });
  const decision = result.decision;
  $('log-decision').hidden = false;
  const map = {
    increase: ['加重', 'chip-ok'], hold: ['保持', 'chip-warn'],
    deload: ['减重', 'chip-danger'], increase_reps: ['加次数', 'chip-ok'],
  };
  const [label, cls] = map[decision.action] || ['建议', 'chip-muted'];
  const pill = $('decision-pill');
  pill.textContent = label;
  pill.className = 'chip ' + cls;
  $('decision-reason').textContent = decision.reason;
  $('decision-next').textContent = decision.next_reps
    ? `下次目标：每组 ${decision.next_reps} 次`
    : `下次重量：${decision.next_weight_kg} 公斤`;
  const savedDay = payload.trained_on;
  const volume = payload.weight_kg * payload.sets * payload.reps;
  toast(`已记入 ${savedDay} 的训练${volume > 0 ? `，容量 ${Math.round(volume)} kg` : ''}`);
  await loadHistory();
  // 跳到记录所在月份并选中那一天，日历上立刻能看到
  const [year, month] = savedDay.split('-').map(Number);
  state.selectedDay = savedDay;
  await loadCalendar(year, month);
});

async function loadHistory() {
  const data = await api('/api/records');
  const records = data.records || [];
  state.records = records;
  $('log-count').textContent = `共 ${records.length} 条`;
  $('log-tbody').innerHTML = records.length
    ? records.map((item) => `<tr>
        <td class="strong">${escapeHtml(item.exercise_name)}</td>
        <td class="num">${item.weight_kg} kg</td>
        <td class="num">${item.sets} × ${item.reps}</td>
        <td class="num">${item.rpe ?? '—'}</td>
        <td>${item.completed ? '<span class="chip chip-ok">完成</span>' : '<span class="chip chip-danger">未完成</span>'}</td>
        <td>${item.trained_on}</td>
        <td class="row-action">
          <button class="icon-btn" type="button" data-record="${item.id}"
            title="删除这条记录" aria-label="删除 ${escapeHtml(item.exercise_name)} 的这条记录">✕</button>
        </td>
      </tr>`).join('')
    : `<tr><td colspan="7">${empty('还没有训练记录')}</td></tr>`;
  $('log-tbody').querySelectorAll('button[data-record]').forEach((button) => {
    button.addEventListener('click', () => deleteRecord(Number(button.dataset.record)));
  });
  renderChart(records);
}

/* 删除训练记录：不弹系统确认框，直接删掉并给 6 秒撤销窗口 */
async function deleteRecord(recordId) {
  const record = (state.records || []).find((item) => Number(item.id) === recordId);
  try {
    const result = await api('/api/records/' + recordId, { method: 'DELETE' });
    const deleted = result.deleted || record;
    await loadHistory();
    await refreshCalendar();
    const label = record ? `${record.exercise_name} ${record.weight_kg} 公斤` : '这条';
    toast(`已删除 ${label}的记录`, {
      actionLabel: '撤销',
      duration: 6000,
      onAction: async () => {
        try {
          await api('/api/records/restore', { method: 'POST', body: JSON.stringify(deleted) });
          await loadHistory();
          await refreshCalendar();
          toast('已恢复这条记录');
        } catch (error) {
          toast('恢复失败：' + error.message);
        }
      },
    });
  } catch (error) {
    toast('删除失败：' + error.message);
  }
}

function renderChart(records) {
  const box = $('log-chart');
  const withWeight = records.filter((item) => Number(item.weight_kg) > 0);
  if (withWeight.length < 2) {
    box.innerHTML = `<p class="muted">至少需要两条有重量的记录才能画出走势。</p>`;
    return;
  }
  const name = withWeight[0].exercise_name;
  const points = withWeight.filter((item) => item.exercise_name === name).reverse();
  if (points.length < 2) {
    box.innerHTML = `<p class="muted">「${escapeHtml(name)}」目前只有一条记录，再练一次就能看到走势。</p>`;
    return;
  }
  const width = 560, height = 170, padX = 44, padY = 22;
  const weights = points.map((item) => Number(item.weight_kg));
  const min = Math.min(...weights), max = Math.max(...weights);
  const flat = max === min;
  const span = flat ? 1 : max - min;
  const stepX = (width - padX * 2) / (points.length - 1);
  const coords = points.map((item, index) => ({
    x: padX + index * stepX,
    y: flat
      ? height / 2
      : height - padY - ((Number(item.weight_kg) - min) / span) * (height - padY * 2),
    item,
  }));
  const line = coords.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
  const area = `${padX},${height - padY} ${line} ${coords[coords.length - 1].x.toFixed(1)},${height - padY}`;
  box.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(name)}重量走势">
      <line x1="${padX}" y1="${height - padY}" x2="${width - padX}" y2="${height - padY}" stroke="rgba(255,255,255,.14)" stroke-width="1"/>
      <line x1="${padX}" y1="${padY}" x2="${padX}" y2="${height - padY}" stroke="rgba(255,255,255,.14)" stroke-width="1"/>
      <polygon points="${area}" fill="rgba(10,132,255,.16)"/>
      <polyline points="${line}" fill="none" stroke="#0a84ff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
      ${coords.map((point) => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4" fill="#0b0f16" stroke="#0a84ff" stroke-width="2.5">
          <title>${escapeHtml(point.item.trained_on)}　${point.item.weight_kg} 公斤</title></circle>`).join('')}
      <text x="8" y="${padY + 4}" font-size="11" fill="rgba(236,240,248,.5)">${max} kg</text>
      <text x="8" y="${height - padY + 4}" font-size="11" fill="rgba(236,240,248,.5)">${min} kg</text>
      <text x="${padX}" y="${height - 6}" font-size="11" fill="rgba(236,240,248,.5)">${escapeHtml(points[0].trained_on)}</text>
      <text x="${width - padX}" y="${height - 6}" font-size="11" fill="rgba(236,240,248,.5)" text-anchor="end">${escapeHtml(points[points.length - 1].trained_on)}</text>
    </svg>
    <p class="fineprint">当前显示「${escapeHtml(name)}」的最近 ${points.length} 次记录。</p>`;
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
$('log-voice').addEventListener('click', () => {
  if (!SpeechRecognition) return toast('这个浏览器不支持语音输入，直接用文字也行');
  const recognition = new SpeechRecognition();
  recognition.lang = 'zh-CN';
  recognition.onresult = (event) => {
    $('log-text').value = event.results[0][0].transcript;
    toast('已识别，点解析继续');
  };
  recognition.onerror = () => toast('没听清，再说一次或直接打字');
  recognition.start();
  toast('请开始说话…');
});

/* ------------------------------------------------ 食堂营养 */

/* 拍照识别：视觉模型在服务器端统一接入，用户不需要自己配密钥 */
state.photoFile = null;
state.photoItems = [];

async function loadVisionStatus() {
  try {
    const data = await api('/api/nutrition/vision');
    const chip = $('photo-chip');
    if (data.available) {
      chip.textContent = `已接入 ${data.provider}`;
      chip.className = 'chip chip-ok';
      $('photo-btn').disabled = !state.photoFile;
    } else {
      chip.textContent = '未接入视觉模型';
      chip.className = 'chip chip-warn';
      $('photo-btn').disabled = true;
      $('photo-drop').classList.add('is-disabled');
      $('photo-result').innerHTML = `<div class="issue">
          <div class="issue-head"><span class="chip chip-warn">暂不可用</span>拍照识别</div>
          <p>${escapeHtml(data.hint)}</p>
          <div class="fix"><strong>怎么开启</strong>：服务器上填一个支持看图的平台密钥即可，推荐智谱 <code>glm-4v-flash</code>（免费）。填好之后所有打开这个网站的人都能直接用，不需要各自绑定。</div>
        </div>`;
    }
  } catch (error) {
    $('photo-chip').textContent = '状态读取失败';
  }
}

async function pickPhoto(file) {
  if (!file) return;
  state.photoFile = file;
  $('photo-name').textContent = `已选择：${file.name}（${formatSize(file.size)}）`;
  $('photo-btn').disabled = false;
  const preview = $('photo-preview');
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;

  // 手机（尤其 iPhone）默认拍出来是 HEIC，部分视觉接口不认这种格式；
  // 在浏览器里先统一转成 JPEG 再上传，顺带把很大的照片压小。
  try {
    const converted = await convertToJpeg(file);
    if (converted) {
      state.photoFile = converted;
      $('photo-name').textContent
        = `已选择：${file.name}（已转为 JPEG，${formatSize(converted.size)}）`;
    }
  } catch (error) {
    // 转换不了就用原图，服务端还会再压缩一次
  }
}

function convertToJpeg(file, maxSide = 1600) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      try {
        const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {
          URL.revokeObjectURL(url);
          if (!blob) {
            reject(new Error('转换失败'));
            return;
          }
          const name = `${file.name.replace(/\.[^.]+$/, '') || 'photo'}.jpg`;
          resolve(new File([blob], name, { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      } catch (error) {
        URL.revokeObjectURL(url);
        reject(error);
      }
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('这个浏览器解不开这张图'));
    };
    image.src = url;
  });
}

$('photo-file').addEventListener('change', (event) => pickPhoto(event.target.files[0]));

const photoDrop = $('photo-drop');
['dragenter', 'dragover'].forEach((type) => photoDrop.addEventListener(type, (event) => {
  event.preventDefault();
  photoDrop.classList.add('is-over');
}));
['dragleave', 'drop'].forEach((type) => photoDrop.addEventListener(type, (event) => {
  event.preventDefault();
  photoDrop.classList.remove('is-over');
}));
photoDrop.addEventListener('drop', (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) pickPhoto(file);
});

$('photo-btn').addEventListener('click', async () => {
  if (!state.photoFile) return;
  const button = $('photo-btn');
  button.disabled = true;
  $('photo-result').innerHTML = '<p class="muted">正在识别照片，大约需要 3 到 10 秒…</p>';
  try {
    const form = new FormData();
    form.append('file', state.photoFile);
    const data = await api('/api/nutrition/photo', { method: 'POST', body: form });
    state.photoItems = data.items || [];
    renderPhotoResult(data);
  } catch (error) {
    $('photo-result').innerHTML = `<div class="issue high">
        <div class="issue-head">识别失败</div>
        <p>${escapeHtml(error.message)}</p>
        <div class="fix">可以先在下面手动选菜，或者换一张光线更好、菜品更清楚的照片。</div>
      </div>`;
  } finally {
    button.disabled = false;
  }
});

function renderPhotoResult(data) {
  const box = $('photo-result');
  if (!state.photoItems.length) {
    box.innerHTML = `<div class="issue"><div class="issue-head">没有识别到菜品</div>
        <p>${escapeHtml(data.scene || '画面里没有看出食物')}，换一张更清楚的照片试试。</p></div>`;
    return;
  }
  box.innerHTML = `
    <div class="advice">
      <div class="advice-head">
        <span>识别结果</span>
        <span class="chip chip-muted">${escapeHtml(data.model)}</span>
      </div>
      <p class="muted">${escapeHtml(data.scene || '')}　份数可以调，热量与蛋白质随之变化。</p>
    </div>
    <div id="photo-items"></div>
    <div class="stat-grid" id="photo-totals"></div>
    <div class="row-actions">
      <button class="btn btn-primary" id="photo-add-all">全部加入今日摄入</button>
    </div>
    <p class="fineprint">${escapeHtml(data.note || '')}</p>`;
  renderPhotoItems();
  $('photo-add-all').addEventListener('click', addPhotoItemsToMeals);
}

function renderPhotoItems() {
  const box = $('photo-items');
  if (!box) return;
  box.innerHTML = state.photoItems.map((item, index) => `
    <div class="item">
      <div class="item-main">
        <div class="item-title">${escapeHtml(item.name)}
          <span class="chip chip-muted">可信度 ${Math.round((item.confidence || 0) * 100)}%</span>
        </div>
        <div class="item-sub">${Math.round(item.kcal)} 千卡　蛋白 ${item.protein} 克　${item.price} 元　${escapeHtml(item.canteen || '')}${escapeHtml(item.window || '')}</div>
        ${item.reason ? `<div class="item-sub">依据：${escapeHtml(item.reason)}</div>` : ''}
      </div>
      <div class="portion">
        <button type="button" data-step="-1" data-index="${index}" aria-label="减少份数">−</button>
        <span>${item.portions} 份</span>
        <button type="button" data-step="1" data-index="${index}" aria-label="增加份数">＋</button>
      </div>
    </div>`).join('');

  box.querySelectorAll('button[data-step]').forEach((button) => {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.index);
      const step = Number(button.dataset.step);
      const item = state.photoItems[index];
      const next = Math.max(0.5, Math.min(3, Math.round((item.portions + step * 0.5) * 2) / 2));
      const ratio = next / item.portions;
      item.portions = next;
      item.kcal = Math.round(item.kcal * ratio * 10) / 10;
      item.protein = Math.round(item.protein * ratio * 10) / 10;
      item.price = Math.round(item.price * ratio * 10) / 10;
      renderPhotoItems();
    });
  });

  const totals = state.photoItems.reduce(
    (acc, item) => ({
      kcal: acc.kcal + item.kcal,
      protein: acc.protein + item.protein,
      price: acc.price + item.price,
    }),
    { kcal: 0, protein: 0, price: 0 },
  );
  const totalsBox = $('photo-totals');
  if (totalsBox) {
    totalsBox.innerHTML = [
      ['这顿热量', `${Math.round(totals.kcal)} 千卡`],
      ['这顿蛋白质', `${totals.protein.toFixed(1)} 克`],
      ['预计花费', `${totals.price.toFixed(1)} 元`],
    ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`).join('');
  }
}

async function addPhotoItemsToMeals() {
  if (!state.photoItems.length) return;
  const button = $('photo-add-all');
  button.disabled = true;
  let added = 0;
  for (const item of state.photoItems) {
    try {
      await api('/api/meals', {
        method: 'POST',
        body: JSON.stringify({ food_id: item.food_id, servings: item.portions, meal_type: '午餐' }),
      });
      added += 1;
    } catch (error) {
      // 单条失败不影响其它菜品
    }
  }
  button.disabled = false;
  toast(`已加入 ${added} 道菜到今日摄入`);
  await loadNutrition();
}

async function loadNutrition() {
  const data = await api('/api/nutrition');
  const { targets, totals, gap, meals } = data;
  $('eat-spend').textContent = `今日花费 ${totals.price} 元`;
  $('eat-bars').innerHTML = bar('热量', totals.kcal, targets.kcal, '')
    + bar('蛋白质', totals.protein, targets.protein, 'protein');
  $('eat-meals').innerHTML = meals.length
    ? meals.map((item) => `<tr>
        <td class="strong">${escapeHtml(item.food_name)}</td>
        <td class="num">${Math.round(item.kcal)} 千卡</td>
        <td class="num">${item.protein.toFixed(1)} 克</td>
        <td class="num">${item.price} 元</td>
        <td class="num"><button class="btn btn-ghost" data-meal="${item.id}" style="padding:3px 8px;font-size:12px">删除</button></td>
      </tr>`).join('')
    : `<tr><td colspan="5">${empty('今天还没有记录，从右侧菜品库添加')}</td></tr>`;
  $('eat-meals').querySelectorAll('button[data-meal]').forEach((button) => {
    button.addEventListener('click', async () => {
      await api('/api/meals/' + button.dataset.meal, { method: 'DELETE' });
      loadNutrition();
    });
  });
  $('suggest-result').innerHTML = gap.protein > 0
    ? `<p class="muted">当前蛋白质缺口 ${gap.protein} 克，热量缺口 ${gap.kcal} 千卡。点上面的按钮生成具体方案。</p>`
    : `<p class="muted">今天蛋白质已达标${gap.kcal >= 0 ? `，热量还有 ${gap.kcal} 千卡空间` : `，热量已超出 ${Math.abs(gap.kcal)} 千卡`}。</p>`;
  loadFoods('');
}

function bar(label, value, target, extraClass) {
  const percent = Math.min(140, Math.round((value / target) * 100));
  const over = value > target ? 'over' : extraClass;
  const unit = label === '热量' ? '千卡' : '克';
  return `<div>
    <div class="bar-label"><span>${label}</span><span>${Math.round(value)} / ${target} ${unit}</span></div>
    <div class="bar"><i class="${over}" style="width:${Math.min(percent, 100)}%"></i></div>
  </div>`;
}

async function loadFoods(keyword) {
  const foods = await api('/api/foods?q=' + encodeURIComponent(keyword || ''));
  const box = $('food-list');
  box.innerHTML = foods.length
    ? foods.map((food) => `<div class="item">
        <div class="item-main">
          <div class="item-title">${escapeHtml(food.name)}</div>
          <div class="item-sub">${food.kcal} 千卡　蛋白 ${food.protein} 克　${food.price} 元</div>
          <div class="item-sub">${escapeHtml(food.canteen)}　${escapeHtml(food.window)}</div>
        </div>
        <button data-food="${food.id}">加入</button>
      </div>`).join('')
    : empty('没有找到这道菜');
  box.querySelectorAll('button[data-food]').forEach((button) => {
    button.addEventListener('click', async () => {
      await api('/api/meals', { method: 'POST', body: JSON.stringify({ food_id: Number(button.dataset.food), servings: 1 }) });
      toast('已加入今天的记录');
      loadNutrition();
    });
  });
}

$('food-search').addEventListener('input', (event) => {
  clearTimeout(state.foodTimer);
  state.foodTimer = setTimeout(() => loadFoods(event.target.value.trim()), 240);
});

$('suggest-btn').addEventListener('click', async () => {
  const result = await api('/api/nutrition/suggest', { method: 'POST', body: JSON.stringify({}) });
  const items = result.items || [];
  $('suggest-price').textContent = result.total_price ? `合计约 ${result.total_price} 元` : '';
  $('suggest-result').innerHTML = `
    <div class="issue ok"><p>${escapeHtml(result.message)}</p></div>
    ${items.map((food) => `<div class="item">
        <div class="item-main">
          <div class="item-title">${escapeHtml(food.name)}</div>
          <div class="item-sub">蛋白 ${food.protein} 克　${food.kcal} 千卡　${food.price} 元　${escapeHtml(food.canteen)}${escapeHtml(food.window)}</div>
        </div>
      </div>`).join('')}`;
});

/* ------------------------------------------------ 我的与计划 */

async function loadProfile() {
  const profile = await api('/api/profile');
  $('me-gender').value = profile.gender || '男';
  $('me-age').value = profile.age;
  $('me-height').value = profile.height_cm;
  $('me-weight').value = profile.weight_kg;
  $('me-goal').value = profile.goal || '增肌';
  $('me-days').value = profile.weekly_days;
  $('me-budget').value = profile.meal_budget;
  $('me-goal-chip').textContent = `目标：${profile.goal}　体重 ${profile.weight_kg} 公斤`;
  refreshSelect('me-gender');
  refreshSelect('me-goal');
}

$('me-save').addEventListener('click', async () => {
  const payload = {
    gender: $('me-gender').value,
    age: Number($('me-age').value),
    height_cm: Number($('me-height').value),
    weight_kg: Number($('me-weight').value),
    goal: $('me-goal').value,
    weekly_days: Number($('me-days').value),
    meal_budget: Number($('me-budget').value),
  };
  const result = await api('/api/profile', { method: 'POST', body: JSON.stringify(payload) });
  const targets = result.targets;
  $('me-targets').innerHTML = [
    ['每日热量目标', `${targets.kcal} 千卡`],
    ['每日蛋白质目标', `${targets.protein} 克`],
    ['碳水参考', `${targets.carb} 克`],
    ['脂肪参考', `${targets.fat} 克`],
  ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`).join('');
  toast('已保存并重新计算目标');
  loadPlanChanges();
});

$('nudge-btn').addEventListener('click', async () => {
  const result = await api('/api/nudge/run', { method: 'POST' });
  $('nudge-result').innerHTML = `
    <div class="issue ok">
      <div class="issue-head"><span class="chip chip-muted">${escapeHtml(result.kind)}</span>督导消息</div>
      <p>${escapeHtml(result.message)}</p>
      ${result.downgrade ? `<div class="fix"><strong>降级方案</strong>：${escapeHtml(result.downgrade)}</div>` : ''}
    </div>`;
  loadPlanChanges();
});

async function loadPlanChanges() {
  const changes = await api('/api/plan/changes');
  $('plan-count').textContent = `共 ${changes.length} 次`;
  $('plan-changes').innerHTML = changes.length
    ? changes.map((item) => `<li>
        <div class="tl-head">第 ${item.plan_version} 次调整<span class="chip chip-muted">${escapeHtml(item.trigger)}</span></div>
        <div class="tl-body">原因：${escapeHtml(item.reason)}</div>
        <div class="tl-body">调整后：${escapeHtml(item.after_text)}</div>
        <div class="tl-time">${escapeHtml(item.created_at.replace('T', ' '))}</div>
      </li>`).join('')
    : `<li class="muted">还没有计划调整记录。记录训练或做一次动作自检后就会自动生成。</li>`;
}

async function loadHealth() {
  const data = await api('/api/health');
  const llm = data.llm ? `已接入 ${data.llm_provider}` : '未配置（建议文本由规则生成）';
  $('health').innerHTML = `
    <div class="kv-row"><span>姿态模型</span><strong>${data.model ? '就绪' : '缺失'}</strong></div>
    <div class="kv-row"><span>大模型</span><strong>${escapeHtml(llm)}${data.llm_model ? `　${escapeHtml(data.llm_model)}` : ''}</strong></div>
    <div class="kv-row"><span>数据库</span><strong>${escapeHtml(data.database.split('\\').pop())}</strong></div>`;

  const chip = $('pose-engine-chip');
  chip.textContent = data.llm ? '视觉与语言模型已接入' : '姿态引擎就绪（建议由规则生成）';
  chip.className = 'chip ' + (data.llm ? 'chip-ok' : 'chip-muted');

  const side = $('side-health');
  side.innerHTML = `<span class="dot${data.model ? '' : ' warn'}"></span><span>${data.model ? '姿态模型已加载' : '姿态模型缺失'}</span>`;
}

/* ------------------------------------------------ 大模型接入 */

let llmProviders = [];

async function loadLlmConfig() {
  const data = await api('/api/llm/config');
  llmProviders = data.providers || [];
  const select = $('llm-provider');
  select.innerHTML = llmProviders.map((item) => `<option value="${item.id}">${escapeHtml(item.label)}${item.vision ? '（支持看图）' : '（仅文字）'}</option>`).join('');
  select.value = data.provider || 'deepseek';
  $('llm-model').value = data.configured ? (data.model || '') : '';
  refreshSelect('llm-provider');

  const chip = $('llm-chip');
  chip.textContent = data.configured ? `已接入 ${data.label}` : '未配置';
  chip.className = 'chip ' + (data.configured ? 'chip-ok' : 'chip-warn');

  updateLlmHint();
  if (data.configured) {
    $('llm-result').innerHTML = `<div class="issue ok">
        <div class="issue-head">当前状态</div>
        <p>平台：${escapeHtml(data.label)}　模型：${escapeHtml(data.model)}　密钥：${escapeHtml(data.masked_key)}</p>
      </div>`;
  }
  return data;
}

function updateLlmHint() {
  const id = $('llm-provider').value;
  const item = llmProviders.find((entry) => entry.id === id);
  if (!item) return;
  $('llm-model').placeholder = `留空使用 ${item.default_model}`;
  const visionNote = item.vision
    ? `　支持拍照识别（视觉模型 ${item.default_vision_model}）`
    : '　只有文字模型，拍照识别需要另外配一个支持看图的平台';
  $('llm-hint').textContent = `申请地址：${item.console}${visionNote}　密钥只保存在本机的 配置\ai配置.txt 里，不会上传。`;
}

$('llm-provider').addEventListener('change', updateLlmHint);

async function saveLlmKey() {
  const provider = $('llm-provider').value;
  const key = $('llm-key').value.trim();
  if (!key) return toast('先粘贴 API Key');
  await api('/api/llm/config', {
    method: 'POST',
    body: JSON.stringify({ provider, key, model: $('llm-model').value.trim() }),
  });
  $('llm-key').value = '';
  return loadLlmConfig();
}

function renderLlmTest(result) {
  if (result.ok) {
    $('llm-result').innerHTML = `<div class="issue ok">
        <div class="issue-head"><span class="chip chip-ok">连接成功</span>${escapeHtml(result.provider)}　${escapeHtml(result.model)}</div>
        <p>模型回复：${escapeHtml(result.reply)}</p>
      </div>`;
    toast('大模型连接成功');
  } else {
    $('llm-result').innerHTML = `<div class="issue high">
        <div class="issue-head"><span class="chip chip-danger">连接失败</span>${escapeHtml(result.provider || '')}</div>
        <p>${escapeHtml(result.error || '未知错误')}</p>
      </div>`;
    toast('连接失败，请看下方原因');
  }
}

$('llm-save').addEventListener('click', async () => {
  try {
    await saveLlmKey();
    renderLlmTest(await api('/api/llm/test', { method: 'POST' }));
    loadHealth();
  } catch (error) {
    toast('保存失败：' + error.message);
  }
});

$('llm-test').addEventListener('click', async () => {
  try {
    renderLlmTest(await api('/api/llm/test', { method: 'POST' }));
  } catch (error) {
    toast('测试失败：' + error.message);
  }
});

/* ------------------------------------------------ 体态评估 */

const POSTURE_SLOTS = [
  { key: 'front', label: '正面' },
  { key: 'side', label: '侧面' },
  { key: 'back', label: '背面' },
];
const posturePhotos = { front: null, side: null, back: null };
let posturePlan = null;

document.querySelectorAll('.photo-slot input[type="file"]').forEach((input) => {
  input.addEventListener('change', () => {
    const key = input.dataset.posture;
    const file = input.files && input.files[0];
    if (!file) return;
    posturePhotos[key] = file;
    const thumb = document.querySelector(`[data-thumb="${key}"]`);
    if (thumb) {
      thumb.src = URL.createObjectURL(file);
      thumb.hidden = false;
    }
    refreshPostureReady();
  });
});

function refreshPostureReady() {
  const ready = POSTURE_SLOTS.every((s) => posturePhotos[s.key]);
  $('posture-start').disabled = !ready;
  const missing = POSTURE_SLOTS.filter((s) => !posturePhotos[s.key]).map((s) => s.label);
  $('posture-status').textContent = ready ? '三张照片已就绪，可以开始评估' : `还差：${missing.join('、')}`;
}

$('posture-start').addEventListener('click', async () => {
  if (!POSTURE_SLOTS.every((s) => posturePhotos[s.key])) return;
  const form = new FormData();
  for (const s of POSTURE_SLOTS) form.append(s.key, posturePhotos[s.key]);

  $('posture-progress').hidden = false;
  $('posture-result').hidden = true;
  $('posture-plan-panel').hidden = true;
  $('posture-start').disabled = true;
  setPostureProgress(30, '正在上传并识别体态…（约 10 至 60 秒）');
  try {
    const data = await api('/api/posture/analyze', { method: 'POST', body: form });
    $('posture-progress').hidden = true;
    renderPostureResult(data);
    loadPostureHistory();
    toast('体态评估完成');
  } catch (error) {
    $('posture-progress').hidden = true;
    refreshPostureReady();
    toast('评估失败：' + error.message);
  }
});

function setPostureProgress(percent, text) {
  $('posture-progress-fill').style.width = percent + '%';
  $('posture-progress-value').textContent = percent + '%';
  if (text) $('posture-progress-text').textContent = text;
}

function riskMeta(score) {
  if (score >= 60) return { text: '高风险', cls: 'chip-danger', color: '#ff6b5f' };
  if (score >= 30) return { text: '中风险', cls: 'chip-warn', color: '#ffb020' };
  return { text: '低风险', cls: 'chip-ok', color: '#3ecf8e' };
}

const LEVEL_CLASS = { 轻度: 'chip-ok', 中度: 'chip-warn', 重度: 'chip-danger' };

function renderPostureResult(data) {
  const issues = data.issues || [];
  const angles = data.angles || {};
  const score = data.risk_score || 0;
  const risk = riskMeta(score);

  $('posture-result').hidden = false;
  $('posture-risk-chip').textContent = risk.text;
  $('posture-risk-chip').className = 'chip ' + risk.cls;
  $('posture-risk-score').textContent = score;
  $('posture-risk-ring').style.background =
    `conic-gradient(${risk.color} ${score * 3.6}deg, rgba(255,255,255,.08) 0deg)`;
  $('posture-provider').textContent = data.provider
    ? `识别模型：${data.provider} · ${data.model || ''}`
    : '';

  $('posture-issues').innerHTML = issues.length
    ? issues.map((it) => `
        <div class="issue">
          <div class="issue-head">
            <span class="chip ${LEVEL_CLASS[it.level] || 'chip-muted'}">${escapeHtml(it.level)}</span>
            ${escapeHtml(it.type)}
            <span class="muted">${escapeHtml(it.normalRange)}</span>
          </div>
          <p>偏离约 ${it.angle}°，建议：${escapeHtml(it.advice || '—')}</p>
        </div>`).join('')
    : `<div class="issue ok"><div class="issue-head">体态整体良好</div><p>未检出明显问题，继续保持。</p></div>`;

  const angleRows = Object.keys(angles).map((name) =>
    `<div class="kv-row"><span>${escapeHtml(name)}</span><span>${angles[name].value}° <em class="muted">正常 ${escapeHtml(angles[name].normal)}</em></span></div>`
  ).join('');
  $('posture-angles').innerHTML = angleRows || '<div class="muted">未提供测量角度</div>';

  renderPosturePlan(data.plan || {});
}

function renderPosturePlan(plan) {
  posturePlan = plan;
  const exercises = plan.exercises || [];
  const versions = plan.versions || {};
  const warnings = plan.warnings || [];
  if (!exercises.length) {
    $('posture-plan-panel').hidden = true;
    return;
  }
  $('posture-plan-panel').hidden = false;
  $('posture-rest-days').textContent = plan.rest_days && plan.rest_days.length
    ? `建议休息：周${plan.rest_days.join('、周')}`
    : '';

  const versionKeys = Object.keys(versions);
  $('posture-plan-tabs').innerHTML = versionKeys.map((v, i) =>
    `<button class="plan-tab ${i === 0 ? 'is-active' : ''}" data-version="${escapeHtml(v)}">${escapeHtml(v)}</button>`
  ).join('');
  document.querySelectorAll('.plan-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.plan-tab').forEach((t) => t.classList.remove('is-active'));
      tab.classList.add('is-active');
      renderPostureExercises(tab.dataset.version);
    });
  });

  $('posture-plan-warnings').innerHTML = warnings.map((w) =>
    `<div class="warn-note">⚠ ${escapeHtml(w)}</div>`).join('');

  renderPostureExercises(versionKeys[0] || '');
}

function renderPostureExercises(version) {
  const plan = posturePlan || {};
  const ids = (plan.versions && plan.versions[version]) || [];
  const map = {};
  (plan.exercises || []).forEach((e) => (map[e.id] = e));
  const list = ids.map((id) => map[id]).filter(Boolean);
  $('posture-plan-exercises').innerHTML = list.length
    ? list.map((e) => `
        <div class="ex-card">
          <div class="ex-head">
            <strong>${escapeHtml(e.name)}</strong>
            <span class="chip chip-muted">${escapeHtml(e.category)} · ${escapeHtml(e.level)}</span>
          </div>
          <div class="ex-meta muted">${escapeHtml(e.equipment ? e.equipment + ' · ' : '')}${escapeHtml(e.duration)} × ${escapeHtml(e.sets)}</div>
          <ol class="ex-steps">${(e.steps || []).map((s) => `<li>${escapeHtml(s)}</li>`).join('')}</ol>
          ${(e.tips || []).length ? `<p class="ex-tips">要点：${e.tips.map((t) => escapeHtml(t)).join('；')}</p>` : ''}
          ${(e.avoid || []).length ? `<p class="ex-avoid">避坑：${e.avoid.map((a) => escapeHtml(a)).join('；')}</p>` : ''}
        </div>`).join('')
    : '<div class="empty">这个版本暂时没有安排动作</div>';
}

async function loadPostureHistory() {
  try {
    const vision = await api('/api/nutrition/vision');
    const chip = $('posture-engine-chip');
    if (vision.available) {
      chip.textContent = '体态识别就绪';
      chip.className = 'chip chip-ok';
    } else {
      chip.textContent = '体态识别未配置';
      chip.className = 'chip chip-warn';
      chip.title = vision.hint || '';
    }
  } catch (error) { /* 忽略状态检查失败 */ }

  try {
    const items = await api('/api/posture/history');
    $('posture-history').innerHTML = items.length
      ? items.map((it) => {
          const risk = riskMeta(it.risk_score || 0);
          const count = (it.issues || []).length;
          return `<div class="history-row">
            <span class="chip ${risk.cls}">${it.risk_score} 分</span>
            <span>${count ? `检出 ${count} 个问题` : '体态良好'}</span>
            <span class="muted">${escapeHtml((it.created_at || '').replace('T', ' '))}</span>
          </div>`;
        }).join('')
      : '<div class="empty">还没有评估记录</div>';
  } catch (error) {
    $('posture-history').innerHTML = '<div class="empty">历史加载失败</div>';
  }
}

/* ------------------------------------------------ 结果预览弹层 */

function openResultPreview(url, title) {
  if (!url || url === '#') return;
  const img = $('result-modal-img');
  $('result-modal-title').textContent = title || '预览';
  img.hidden = false;
  img.src = url;
  $('result-modal').hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeResultPreview() {
  if ($('result-modal').hidden) return;
  $('result-modal').hidden = true;
  $('result-modal-img').removeAttribute('src');
  document.body.style.overflow = '';
}

$('pose-card-link').addEventListener('click', (event) => {
  event.preventDefault();
  openResultPreview($('pose-card-link').getAttribute('href'), '结论卡');
});
$('result-modal-close').addEventListener('click', closeResultPreview);
$('result-modal-backdrop').addEventListener('click', closeResultPreview);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeResultPreview();
});

$('tutorial-modal-close').addEventListener('click', closeTutorial);
$('tutorial-modal-backdrop').addEventListener('click', closeTutorial);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeTutorial();
});

// 右上角状态徽章：点开当前第一个问题的纠正教程
$('pose-status-pill').addEventListener('click', () => {
  const issue = (state.poseIssues || [])[0];
  if (issue) openTutorial(issue, state.poseExercise || 'squat');
});

// 「查看示范 / 逐次点评短标签」：委托到对应问题的纠正教程
document.addEventListener('click', (event) => {
  const trigger = event.target.closest('[data-issue-key]');
  if (!trigger) return;
  const issue = (state.poseIssueLookup || {})[trigger.getAttribute('data-issue-key')];
  if (issue) openTutorial(issue, state.poseExercise || 'squat');
});

/* ------------------------------------------------ 登录 / 注册 */

function showAuthScreen() {
  $('auth-screen').hidden = false;
  $('shell').hidden = true;
}

function hideAuthScreen() {
  $('auth-screen').hidden = true;
  $('shell').hidden = false;
}

let authMode = 'login'; // login | register

function setAuthMode(mode) {
  authMode = mode;
  const registering = mode === 'register';
  $('auth-tab-login').classList.toggle('is-active', !registering);
  $('auth-tab-register').classList.toggle('is-active', registering);
  $('auth-confirm-field').hidden = !registering;
  $('auth-submit').textContent = registering ? '注册并登录' : '登录';
  $('auth-error').hidden = true;
}

function showAuthError(message) {
  $('auth-error').textContent = message;
  $('auth-error').hidden = false;
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  if (!username || !password) {
    showAuthError('请填写用户名和密码');
    return;
  }
  if (authMode === 'register') {
    if (password.length < 6) {
      showAuthError('密码至少 6 位');
      return;
    }
    if (password !== $('auth-confirm').value) {
      showAuthError('两次输入的密码不一致');
      return;
    }
  }
  const button = $('auth-submit');
  button.disabled = true;
  button.textContent = authMode === 'register' ? '注册中…' : '登录中…';
  try {
    const data = await api(`/api/auth/${authMode === 'register' ? 'register' : 'login'}`, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    await enterApp(data.user);
  } catch (err) {
    showAuthError(err.message || '操作失败');
  } finally {
    button.disabled = false;
    button.textContent = authMode === 'register' ? '注册并登录' : '登录';
  }
}

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (err) { /* 忽略 */ }
  location.reload();
}

function renderUser(user) {
  $('side-user').hidden = false;
  $('side-user-name').textContent = user.username || user.name;
  const isAdmin = user.role === 'admin';
  $('side-user-role').hidden = !isAdmin;
  $('llm-panel').hidden = !isAdmin;
  if (isAdmin) loadLlmConfig();
}

async function enterApp(user) {
  state.user = user;
  renderUser(user);
  hideAuthScreen();
  loadHistory();
  loadCalendar(state.calYear, state.calMonth, false);
  loadNutrition();
  loadVisionStatus();
  loadHealth();
  loadPoseExercises();
}

async function bootstrap() {
  try {
    const data = await api('/api/auth/me');
    await enterApp(data.user);
  } catch (err) {
    showAuthScreen();
  }
}

/* ------------------------------------------------ 初始化 */

/* 把所有原生下拉框换成暗色玻璃弹层（原生弹层是系统白底，与暗色界面冲突） */
document.querySelectorAll('select').forEach(enhanceSelect);

async function refreshCalendar() {
  await loadCalendar(state.calYear, state.calMonth, true);
  if (state.selectedDay) await selectDay(state.selectedDay);
}

$('log-date').value = todayISO();
state.selectedDay = todayISO();

$('auth-form').addEventListener('submit', handleAuthSubmit);
$('auth-tab-login').addEventListener('click', () => setAuthMode('login'));
$('auth-tab-register').addEventListener('click', () => setAuthMode('register'));
$('logout-btn').addEventListener('click', logout);

bootstrap();
