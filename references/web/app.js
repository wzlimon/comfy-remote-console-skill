/* 手机端逻辑：提交表单、轮询进度、看片、查历史 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  mode: 't2v',
  opts: null,
  pick: { workflow: '', steps: null, duration: null, resolution: '', ratio: '' },
  gallery: {},   // 图库复用：槽位 -> {name, url} 或 null（本地新传）
  project: '',   // 项目专库名（空=默认库）
  page: 'new',
  hisOffset: 0,
  hisTotal: 0,
  keyword: '',
  timer: null,
};

/* 把「项目/子目录/文件名」这类含斜杠的相对路径拼成可访问 URL：
   逐段 encode，避免整串 encode 把 '/' 变成 %2F 导致 Flask <path:> 路由 404。 */
function assetUrl(base, rel) {
  return base + String(rel).split('/').map(encodeURIComponent).join('/');
}

const STATUS_TEXT = {
  queued: '排队中', running: '生成中', upscaling: '超分中',
  delivering: '投递中', done: '已完成', failed: '失败', canceled: '已取消',
};
const MODE_TEXT = { t2v: '文字生成', i2v: '图片生成', flf: '首尾帧', r2v: '万能参考' };

function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

/* 复制文本到剪贴板：优先 Clipboard API（HTTPS / localhost 安全上下文），
   失败或不支持时回退到临时 textarea + execCommand。 */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) { /* 落到兜底 */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) { return false; }
}

async function api(url, opt = {}) {
  const res = await fetch(url, { credentials: 'same-origin', ...opt });
  if (res.status === 401) { showLogin(); throw new Error('need_login'); }
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || '请求失败');
  return data;
}

/* ---------------- 登录 ---------------- */
function showLogin() {
  $('#login').classList.remove('hidden');
  $('#app').classList.add('hidden');
}

async function boot() {
  const me = await api('/api/me');
  if (me.need_password && !me.logged_in) { showLogin(); return; }
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  await loadOptions();
  checkHealth();
  loadHistory(true);
  startPolling();
}

$('#loginBtn').onclick = async () => {
  try {
    await api('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: $('#pwd').value }),
    });
    location.reload();
  } catch (e) { $('#loginErr').textContent = '密码不对'; }
};
$('#pwd').onkeydown = (e) => { if (e.key === 'Enter') $('#loginBtn').click(); };

/* 退出登录：清掉网页 cookie 登录态，回到登录页（适合在别人电脑上演示完一键退出） */
$('#logoutBtn').onclick = async () => {
  if (!confirm('退出登录后需要重新输入密码才能进入，确定退出？')) return;
  try {
    // 即使服务端返回 401 也按已退出处理
    await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
  } catch (_) { /* 忽略网络错误，本地直接清 UI */ }
  clearInterval(S.timer);
  showLogin();
  toast('已退出登录');
};

/* ---------------- 选项 ---------------- */
function chips(box, items, cur, onPick, labelFn) {
  box.innerHTML = '';
  items.forEach((it) => {
    const b = document.createElement('button');
    b.className = 'chip' + (String(it) === String(cur) ? ' on' : '');
    b.textContent = labelFn ? labelFn(it) : it;
    b.onclick = () => {
      Array.from(box.children).forEach((c) => c.classList.remove('on'));
      b.classList.add('on');
      onPick(it);
      updateSummary();
    };
    box.appendChild(b);
  });
}

async function loadOptions() {
  const o = await api('/api/options');
  S.opts = o;

  S.pick.workflow = o.workflow_default;
  S.pick.duration = o.duration_default;
  S.pick.resolution = o.resolution_default;
  S.pick.ratio = o.ratios.includes(o.ratio_default) ? o.ratio_default : o.ratios[0];

  refreshWfChips();

  chips($('#durChips'), o.durations, S.pick.duration,
    (v) => { S.pick.duration = v; }, (v) => v + '秒');

  chips($('#resChips'), o.resolutions, S.pick.resolution,
    (v) => { S.pick.resolution = v; });

  chips($('#ratioChips'), o.ratios, S.pick.ratio, (v) => { S.pick.ratio = v; });

  if (!o.upscale_enabled) {
    $('#upscale').checked = false;
    $('#upscale').disabled = true;
  }
  renderSteps();
  updateSummary();
}

function renderSteps() {
  const wf = (S.opts.workflows || []).find((w) => w.name === S.pick.workflow) || {};
  const opts = wf.steps_options || [];
  if (opts.length <= 1) {
    $('#stepsField').classList.add('hidden');
    S.pick.steps = wf.steps_default || null;
    return;
  }
  $('#stepsField').classList.remove('hidden');
  S.pick.steps = wf.steps_default || opts[0];
  chips($('#stepChips'), opts, S.pick.steps, (v) => { S.pick.steps = v; });
}

/* 根据当前模式，只展示该模式可用的「流程」档位（标准 / Turbo 等）。
   例如 r2v 模式只显示「万能参考 / 参考加速」，视频模式只显示「标准流程 / Turbo加速」。 */
function refreshWfChips() {
  const wfs = (S.opts && S.opts.mode_workflows && S.opts.mode_workflows[S.mode]) || [];
  const valid = wfs.filter((n) => (S.opts.workflows || []).some((w) => w.name === n));
  const box = $('#wfChips');
  if (!valid.length) {
    box.innerHTML = '';
    $('#wfField').classList.add('hidden');
    return;
  }
  $('#wfField').classList.remove('hidden');
  // 若当前选中的流程不在本模式可用列表里，回退到第一个
  if (!valid.includes(S.pick.workflow)) S.pick.workflow = valid[0];
  chips(box, valid, S.pick.workflow,
    (v) => { S.pick.workflow = v; renderSteps(); },
    (v) => (S.opts.workflows.find((w) => w.name === v) || {}).label || v);
  renderSteps();
}

function updateSummary() {
  const wf = (S.opts?.workflows || []).find((w) => w.name === S.pick.workflow);
  const parts = [wf ? wf.label : '', S.pick.duration + '秒', S.pick.resolution];
  if (S.mode === 't2v' || S.mode === 't2i' || S.mode === 'r2v') parts.push(S.pick.ratio);
  if ($('#stepsField') && !$('#stepsField').classList.contains('hidden')) {
    parts.push(S.pick.steps + '步');
  }
  $('#paramSummary').textContent = parts.filter(Boolean).join(' / ');
}

/* ---------------- 模式 ---------------- */
$$('#modeSeg .seg-item').forEach((b) => {
  b.onclick = () => {
    $$('#modeSeg .seg-item').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    S.mode = b.dataset.mode;
    const needImg = S.mode === 'i2v' || S.mode === 'flf';
    const needRef = S.mode === 'r2v';
    const isImage = S.mode === 't2i';
    $('#durField').classList.toggle('hidden', isImage);
    $('#resField').classList.toggle('hidden', isImage);
    $('#upscaleField').classList.toggle('hidden', isImage);
    $('#imgRow').classList.toggle('hidden', !needImg);
    $('#imgHint').classList.toggle('hidden', !needImg);
    $('#pickLast').classList.toggle('hidden', S.mode !== 'flf');
    $('#imgGalRow').classList.toggle('hidden', !needImg);
    $('#pickGalleryLast').classList.toggle('hidden', S.mode !== 'flf');
    $('#refRow').classList.toggle('hidden', !needRef);
    $('#refHint').classList.toggle('hidden', !needRef);
    $('#refGalRow').classList.toggle('hidden', !needRef);
    // 比例：图生 / 首尾帧跟随图片，隐藏；万能参考仍由 ResolutionSelector 控制比例，显示
    $('#ratioField').classList.toggle('hidden', needImg);
    // 流程芯片按当前模式过滤（r2v 模式只显示 万能参考 / 参考加速）
    refreshWfChips();
    updateSummary();
  };
});

const SLOT_META = {
  first:  { img: '#prevFirst', label: '#labelFirst', text: '首帧',   fileSel: '#fileFirst' },
  last:   { img: '#prevLast',  label: '#labelLast',  text: '尾帧',   fileSel: '#fileLast' },
  'ref_0':{ img: '#prevRef0',  label: '#labelRef0',  text: '参考图1', fileSel: '#fileRef0' },
  'ref_1':{ img: '#prevRef1',  label: '#labelRef1',  text: '参考图2', fileSel: '#fileRef1' },
  'ref_2':{ img: '#prevRef2',  label: '#labelRef2',  text: '参考图3', fileSel: '#fileRef2' },
};

function bindPick(inputSel, imgSel, labelSel, text, key) {
  $(inputSel).onchange = (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (key) S.gallery[key] = null;  // 改用本地文件，放弃图库选择
    const url = URL.createObjectURL(f);
    $(imgSel).src = url;
    $(imgSel).classList.remove('hidden');
    $(labelSel).textContent = text + '（点击更换）';
    if (key) showClear(key, true);   // 选了图就露出右上角清除叉
  };
}

/* 显示/隐藏某个图片槽右上角的清除叉 */
function showClear(key, show) {
  const btn = document.querySelector(`.imgclear[data-slot="${key}"]`);
  if (btn) btn.classList.toggle('hidden', !show);
}

/* 清除某槽位已选图片（仅清选择，不物理删除文件）：
   清掉图库选择、清空文件输入框、复位预览与文案，并隐藏清除叉。 */
function clearSlot(key) {
  if (!SLOT_META[key]) return;
  S.gallery[key] = null;
  $(SLOT_META[key].fileSel).value = '';
  showSlotFromGallery(key);  // 复用其复位逻辑（图片隐藏 + 文案复位）
  showClear(key, false);
}
bindPick('#fileFirst', '#prevFirst', '#labelFirst', '首帧', 'first');
bindPick('#fileLast', '#prevLast', '#labelLast', '尾帧', 'last');
bindPick('#fileRef0', '#prevRef0', '#labelRef0', '参考图1', 'ref_0');
bindPick('#fileRef1', '#prevRef1', '#labelRef1', '参考图2', 'ref_1');
bindPick('#fileRef2', '#prevRef2', '#labelRef2', '参考图3', 'ref_2');

/* ---------------- 图库复用 ---------------- */
let GAL = { target: null, max: 1, keys: [], sel: new Set() };

async function openGallery(target) {
  let max, keys, title;
  if (target === 'refs') { max = 3; keys = ['ref_0', 'ref_1', 'ref_2']; title = '选择参考图（最多 3 张）'; }
  else if (target === 'first') { max = 1; keys = ['first']; title = '选择首帧'; }
  else { max = 1; keys = ['last']; title = '选择尾帧'; }
  GAL = { target, max, keys, sel: [] };
  keys.forEach((k) => {
    if (S.gallery[k]) {
      // 把之前选中的（存的是相对路径 name + 展示名 disp）还原进待选集，便于再次打开时保持勾选
      GAL.sel.push({ rel_path: S.gallery[k].name, name: S.gallery[k].disp || S.gallery[k].name, url: S.gallery[k].url });
    }
  });
  // 项目专库：带了 project 就只拉该项目目录下的素材，避免跨项目误选
  const url = S.project ? `/api/refs?project=${encodeURIComponent(S.project)}` : '/api/refs';
  const d = await api(url);
  renderGallery(d.refs || []);
  $('#galleryTitle').textContent = S.project ? `${title}（项目：${S.project}）` : title;
  updateGalCount();
  $('#galleryModal').classList.remove('hidden');
}

function renderGallery(items) {
  const grid = $('#galleryGrid');
  grid.innerHTML = '';
  if (!items.length) {
    grid.innerHTML = '<p class="empty" style="width:100%">该项目下还没有图片，先在图库目录放一些参考图吧</p>';
    return;
  }
  // 已选集合（用 rel_path 作为身份，兼容根目录平铺与项目子目录两种情形）
  const selSet = new Set(GAL.sel.map((x) => x.rel_path));
  items.forEach((it) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'gal-item' + (selSet.has(it.rel_path) ? ' on' : '');
    const typeTag = (it.type && it.type !== 'other') ? `<i class="gtype">${esc(it.type)}</i>` : '';
    b.innerHTML = `<img src="${esc(it.url)}" alt=""><span>${esc(it.name.slice(0, 20))}</span>${typeTag}`;
    b.onclick = () => toggleGal(b, it);
    grid.appendChild(b);
  });
}

function toggleGal(el, it) {
  const idx = GAL.sel.findIndex((x) => x.rel_path === it.rel_path);
  if (idx >= 0) {
    GAL.sel.splice(idx, 1);
    el.classList.remove('on');
  } else {
    if (GAL.sel.length >= GAL.max) { toast(`最多选 ${GAL.max} 张`); return; }
    GAL.sel.push(it);
    el.classList.add('on');
  }
  updateGalCount();
}

function updateGalCount() {
  $('#galleryCount').textContent = `已选 ${GAL.sel.length}/${GAL.max}`;
}

function confirmGallery() {
  const arr = GAL.sel;
  GAL.keys.forEach((k) => {
    S.gallery[k] = null;
    $(SLOT_META[k].fileSel).value = '';
  });
  GAL.keys.forEach((k, i) => {
    if (i < arr.length) {
      const it = arr[i];
      // 关键：name 存相对路径 rel_path（服务端按此解析），url 用服务端给的可直链地址
      S.gallery[k] = { name: it.rel_path, disp: it.name, url: it.url };
    }
  });
  GAL.keys.forEach((k) => showSlotFromGallery(k));
  closeGallery();
}

function showSlotFromGallery(key) {
  const m = SLOT_META[key];
  if (!m) return;
  const g = S.gallery[key];
  if (g) {
    $(m.img).src = g.url;
    $(m.img).classList.remove('hidden');
    $(m.label).textContent = '图库：' + (g.disp || g.name).slice(0, 14) + '（点此重选）';
    showClear(key, true);
  } else {
    $(m.img).classList.add('hidden');
    $(m.img).src = '';
    $(m.label).textContent = '+ ' + m.text;
    showClear(key, false);
  }
}

function closeGallery() { $('#galleryModal').classList.add('hidden'); }

$('#pickGalleryFirst').onclick = () => openGallery('first');
$('#pickGalleryLast').onclick = () => openGallery('last');
$('#pickGalleryRefs').onclick = () => openGallery('refs');
$('#galleryOk').onclick = confirmGallery;
$('#galleryCancel').onclick = closeGallery;
$('#galleryClose').onclick = closeGallery;

/* 图片槽右上角清除叉：清掉该槽已选图片（不物理删除）。
   按钮嵌在 <label> 内，mousedown 阶段 preventDefault 防止触发文件选择框。 */
$$('.imgclear').forEach((b) => {
  b.onmousedown = (e) => e.preventDefault();
  b.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    clearSlot(b.dataset.slot);
    toast('已清除该图片');
  };
});

/* ---------------- 提示词 ---------------- */
const pt = $('#prompt');
pt.oninput = () => { $('#charCount').textContent = pt.value.length + ' 字'; };
$('#clearPrompt').onclick = () => { pt.value = ''; pt.oninput(); pt.focus(); };

/* ---------------- 项目专库 ---------------- */
$('#projectInput').oninput = () => { S.project = $('#projectInput').value.trim(); };

/* ---------------- 标签页 ---------------- */
$$('.tab').forEach((t) => {
  t.onclick = () => {
    $$('.tab').forEach((x) => x.classList.remove('active'));
    $$('.page').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    S.page = t.dataset.tab;
    $('#page-' + S.page).classList.add('active');
    if (S.page === 'history') loadHistory(true);
    refresh();
  };
});

/* ---------------- 提交 ---------------- */
$('#submitBtn').onclick = async () => {
  const prompt = pt.value.trim();
  const msg = $('#submitMsg');
  if (!prompt) { msg.className = 'msg bad'; msg.textContent = '先写点提示词'; return; }

  const fd = new FormData();
  fd.append('prompt', prompt);
  fd.append('mode', S.mode);
  fd.append('workflow', S.pick.workflow);
  fd.append('duration', S.pick.duration);
  fd.append('resolution', S.pick.resolution);
  fd.append('ratio', S.pick.ratio);
  if (S.pick.steps) fd.append('steps', S.pick.steps);
  if ($('#seed').value.trim()) fd.append('seed', $('#seed').value.trim());
  fd.append('upscale', $('#upscale').checked ? '1' : '0');
  if (S.project) fd.append('project', S.project);

  if (S.mode === 'r2v') {
    let cnt = 0;
    for (const key of ['ref_0', 'ref_1', 'ref_2']) {
      const g = S.gallery[key];
      if (g && g.name) { fd.append(key + '_name', g.name); cnt++; }
      else {
        const f = $(SLOT_META[key].fileSel).files[0];
        if (f) { fd.append(key, f); cnt++; }
      }
    }
    if (!cnt) {
      msg.className = 'msg bad';
      msg.textContent = '万能参考至少选 1 张参考图（图库或本地）';
      return;
    }
  } else if (S.mode === 'i2v' || S.mode === 'flf') {
    const g1 = S.gallery['first'];
    if (g1 && g1.name) fd.append('first_image_name', g1.name);
    else {
      const f1 = $('#fileFirst').files[0];
      if (!f1) { msg.className = 'msg bad'; msg.textContent = '这个模式要先选首帧图片'; return; }
      fd.append('first_image', f1);
    }
    if (S.mode === 'flf') {
      const g2 = S.gallery['last'];
      if (g2 && g2.name) fd.append('last_image_name', g2.name);
      else {
        const f2 = $('#fileLast').files[0];
        if (!f2) { msg.className = 'msg bad'; msg.textContent = '首尾帧模式还要选尾帧图片'; return; }
        fd.append('last_image', f2);
      }
    }
  }

  const btn = $('#submitBtn');
  btn.disabled = true;
  btn.textContent = '提交中…';
  try {
    const r = await api('/api/submit', { method: 'POST', body: fd });
    msg.className = 'msg ok';
    msg.textContent = r.ahead > 0
      ? `已提交（前面还排 ${r.ahead} 个），进度看「进行中」`
      : '已提交，开始生成了，进度看「进行中」';
    toast('任务 #' + r.id + ' 已提交');
    refresh();
  } catch (e) {
    msg.className = 'msg bad';
    msg.textContent = '提交失败：' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '开始生成';
  }
};

/* ---------------- 卡片渲染 ---------------- */
function metaLine(t) {
  const bits = [MODE_TEXT[t.mode] || t.mode];
  if (t.duration) bits.push(t.duration + '秒');
  if (t.resolution) bits.push(t.resolution);
  if (t.ratio) bits.push(t.ratio);
  if (t.refs && t.refs.length) bits.push('参考' + t.refs.length + '图');
  if (t.steps) bits.push(t.steps + '步');
  if (t.upscale) bits.push('已超分1080P');
  if (t.elapsed) bits.push('耗时' + fmtDur(t.elapsed));
  return bits.map((b) => `<span>${esc(b)}</span>`).join('');
}

function fmtDur(sec) {
  sec = Math.round(sec);
  if (sec < 60) return sec + '秒';
  const m = Math.floor(sec / 60);
  return m + '分' + (sec % 60) + '秒';
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function taskCard(t, opts = {}) {
  const active = ['queued', 'running', 'upscaling', 'delivering'].includes(t.status);
  const el = document.createElement('div');
  el.className = 'card';

  let html = `<div class="card-head">
      <span class="tag ${active ? 'running' : t.status}">${STATUS_TEXT[t.status] || t.status}</span>
      <span class="grow">#${t.id} · ${esc(t.created_text)}</span>
    </div>`;

  if (active) {
    html += `<div class="bar"><i></i></div>
             <div class="stage">${esc(t.stage_text || '处理中…')}</div>`;
  }

  html += `<div class="ptext">${esc(t.prompt)}</div>
           <div class="meta">${metaLine(t)}</div>`;

  if (t.error) html += `<div class="errtext">${esc(t.error)}</div>`;

  if (t.status === 'done' && t.result_file) {
    const isImg = /\.(png|jpe?g|webp|bmp|gif)$/i.test(t.result_file);
    if (isImg) {
      const isrc = assetUrl('/image/', t.result_file);
      html += `<div class="thumb"><img src="${isrc}" alt=""></div>`;
    } else {
      const src = assetUrl('/video/', t.result_file);
      html += `<div class="thumb" data-src="${src}">
          ${t.thumb_file
            ? `<img src="${assetUrl('/thumb/', t.thumb_file)}" alt=""><div class="play">▶</div>`
            : `<video src="${src}" controls preload="metadata"></video>`}
        </div>`;
    }
  }

  if (t.status === 'done' && t.netdisk_path) {
    if (t.netdisk_path.indexOf('投递失败') === 0) {
      html += `<div class="netdisk fail">网盘未同步（${esc(t.netdisk_path)}）</div>`;
    } else {
      html += `<div class="netdisk ok">已同步到百度网盘：${esc(t.netdisk_path)}</div>`;
    }
  }

  const acts = [];
  if (active) acts.push(`<button class="btn danger" data-act="cancel">取消</button>`);
  if (t.status === 'done' && t.result_file) {
    const isImg = /\.(png|jpe?g|webp|bmp|gif)$/i.test(t.result_file);
    const base = isImg ? '/image/' : '/video/';
    acts.push(`<a class="btn primary" href="${assetUrl(base, t.result_file)}?dl=1" download>下载</a>`);
  }
  if (!active) {
    acts.push(`<button class="btn ghost" data-act="copy">提示词复制</button>`);
    acts.push(`<button class="btn ghost" data-act="reuse">复用提示词</button>`);
    acts.push(`<button class="btn ghost" data-act="del">删除</button>`);
  }
  if (acts.length) html += `<div class="acts">${acts.join('')}</div>`;

  el.innerHTML = html;

  const ptEl = el.querySelector('.ptext');
  ptEl.onclick = () => ptEl.classList.toggle('open');

  const th = el.querySelector('.thumb[data-src]');
  if (th && th.querySelector('img')) {
    th.onclick = () => {
      th.innerHTML = `<video src="${th.dataset.src}" controls autoplay playsinline></video>`;
    };
  }

  el.querySelectorAll('[data-act]').forEach((b) => {
    b.onclick = async () => {
      const act = b.dataset.act;
      if (act === 'cancel') {
        const r = await api(`/api/task/${t.id}/cancel`, { method: 'POST' });
        toast(r.message); refresh();
      } else if (act === 'del') {
        if (!confirm('删除这条记录和对应的成品文件？')) return;
        await api(`/api/task/${t.id}/delete`, { method: 'POST' });
        toast('已删除'); loadHistory(true); refresh();
      } else if (act === 'copy') {
        const ok = await copyText(t.prompt);
        toast(ok ? '提示词已复制到剪贴板' : '复制失败，请手动长按选择');
      } else if (act === 'reuse') {
        pt.value = t.prompt; pt.oninput();
        $$('.tab')[0].click();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        toast('提示词已填回表单');
      }
    };
  });
  return el;
}

/* ---------------- 轮询 ---------------- */
async function refresh() {
  try {
    const d = await api('/api/tasks?limit=1');
    const actives = d.actives || [];

    const badge = $('#runBadge');
    badge.textContent = actives.length;
    badge.classList.toggle('hidden', actives.length === 0);

    const list = $('#runList');
    list.innerHTML = '';
    actives.forEach((t) => list.appendChild(taskCard(t)));
    $('#runEmpty').classList.toggle('hidden', actives.length > 0);

    const st = d.stats || {};
    $('#stats').textContent =
      `共 ${st.total || 0} 条 · 成功 ${st.done || 0} · 失败 ${st.failed || 0}` +
      (st.avg_elapsed ? ` · 平均耗时 ${fmtDur(st.avg_elapsed)}` : '');

    // 有任务完成时刷新历史列表
    if (refresh._lastActive && refresh._lastActive > actives.length) {
      loadHistory(true);
    }
    refresh._lastActive = actives.length;
  } catch (e) { /* 网络抖动忽略 */ }
}

function startPolling() {
  clearInterval(S.timer);
  S.timer = setInterval(() => {
    if (document.hidden) return;
    refresh();
  }, 3000);
  refresh();
}

/* ---------------- 历史 ---------------- */
async function loadHistory(reset) {
  if (reset) { S.hisOffset = 0; $('#hisList').innerHTML = ''; }
  const d = await api(
    `/api/tasks?limit=10&offset=${S.hisOffset}&keyword=${encodeURIComponent(S.keyword)}`
  );
  S.hisTotal = d.total;
  const box = $('#hisList');
  (d.tasks || []).forEach((t) => box.appendChild(taskCard(t)));
  S.hisOffset += (d.tasks || []).length;
  $('#moreBtn').classList.toggle('hidden', S.hisOffset >= S.hisTotal);
  $('#hisEmpty').classList.toggle('hidden', S.hisTotal > 0);
}

$('#moreBtn').onclick = () => loadHistory(false);

let kwTimer = null;
$('#kw').oninput = (e) => {
  clearTimeout(kwTimer);
  kwTimer = setTimeout(() => { S.keyword = e.target.value.trim(); loadHistory(true); }, 350);
};

/* ---------------- 自检 ---------------- */
async function checkHealth() {
  const el = $('#health');
  try {
    const h = await api('/api/health');
    const ok = h.comfyui && h.comfyui.ok;
    el.className = 'health ' + (ok ? 'ok' : 'bad');
    if (ok) {
      const bits = [`ComfyUI 在线`];
      if (h.comfyui.vram_free_gb) bits.push(`显存空闲 ${h.comfyui.vram_free_gb}G`);
      if (h.topaz && !h.topaz.ok) bits.push('超分不可用');
      el.textContent = bits.join(' · ');
    } else {
      el.textContent = 'ComfyUI 未启动';
    }
  } catch (e) {
    el.className = 'health bad';
    el.textContent = '服务异常';
  }
}
$('#health').onclick = checkHealth;
setInterval(checkHealth, 30000);

boot().catch(() => showLogin());
