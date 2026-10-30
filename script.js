const desktop = document.getElementById('desktop');
const assistant = document.getElementById('assistant');
const island = document.getElementById('island');
const chatPanel = document.getElementById('chatPanel');
const dropHint = document.getElementById('dropHint');
const windows = [...document.querySelectorAll('.app-window')];
const chatMessages = document.getElementById('chatMessages');
const suggestions = document.getElementById('suggestions');
const chatInput = document.getElementById('chatInput');
const fileGrid = document.getElementById('fileGrid');
const originalFiles = fileGrid.innerHTML;
const tetherLayer = document.getElementById('tetherLayer');
const tetherPath = document.getElementById('tetherPath');
const tetherStartFragment = document.getElementById('tetherStartFragment');
const tetherEndFragment = document.getElementById('tetherEndFragment');
const tetherDroplets = [...document.querySelectorAll('#tetherDroplets circle')];
const notchFace = document.getElementById('notchFace');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const avatarMenu = document.getElementById('avatarMenu');
const settingsPanel = document.getElementById('settingsPanel');
const settingsBackdrop = document.getElementById('settingsBackdrop');
const settingsStatus = document.getElementById('settingsStatus');
const menuButton = document.getElementById('openAvatarMenu');
const idlePrompts = ['聊聊今天的计划', '给我一个灵感', '你能做什么？'];
const idleHistory = [{ type: 'ai', text: '你好！点击我可以随时聊天，也可以把我拖到窗口上一起工作。' }];
const defaultSettings = { mode: 'demo', endpoint: '', model: '', autoOpen: true, hints: true };
function loadSettings() {
  try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem('orbit-settings') || '{}') }; }
  catch { return { ...defaultSettings }; }
}
let settings = loadSettings();
let apiToken = '';
let replyController = null;

const contexts = {
  finder: {
    title: '设计资料', path: '~/项目/设计资料', icon: '▰', accent: '#7869d9', light: '#eeeaff',
    action: '整理、查找和概览文件',
    description: '当前路径及 6 个文件已就绪',
    greeting: '我已经看到「设计资料」中的 6 个文件了。可以帮你整理、查找或概览这些内容。',
    prompts: ['整理这些文件', '这里有什么内容？', '找出设计源文件']
  },
  editor: {
    title: 'orbit-app', path: '~/Projects/orbit-app/src/island.ts', icon: '⌘', accent: '#6684dc', light: '#eaf0ff',
    action: '查看代码并提出修改建议',
    description: '已识别项目结构与当前 island.ts 文件',
    greeting: '已连接到 orbit-app 项目，当前打开 island.ts。你可以让我概览代码或提出改进建议。',
    prompts: ['概览当前代码', '给出改进建议', '这个项目有哪些文件？']
  },
  browser: {
    title: 'Orbit Docs', path: 'docs.orbit.design/getting-started', icon: '◎', accent: '#d8966f', light: '#fff1e8',
    action: '理解页面并提炼重点',
    description: '已识别页面标题与可见内容',
    greeting: '我正在查看 Orbit Docs 的入门页面。需要我总结页面内容，还是帮你提炼要点？',
    prompts: ['总结这个页面', '提炼三个要点', '这个页面讲什么？']
  }
};

let attached = null;
let hoverTarget = null;
let dragging = false;
let pointerStart = null;
let dragOffset = null;
let topZ = 6;
let windowOrder = windows.slice();
let panelWasOpen = false;
let toastTimer;
let busy = false;
let connectionVersion = 0;
let activityVersion = 0;
let tetherOrigin = null;
let draggingWithTether = false;
let tetherBroken = false;
let tetherFrame = 0;
let tetherToken = 0;
let tetherLiveEnd = null;
let tetherLastEnd = null;
let tetherBend = 0;
let tetherWaist = 14;
let tetherFlowFrame = 0;
const TETHER_BREAK_DISTANCE = 220;

function bounds() { return desktop.getBoundingClientRect(); }
function clamp(value, min, max) { return Math.max(min, Math.min(value, max)); }
function setPosition(left, top) {
  assistant.style.left = `${clamp(left, 8, desktop.clientWidth - assistant.offsetWidth - 8)}px`;
  assistant.style.top = `${clamp(top, -28, desktop.clientHeight - 70)}px`;
}
function snapHome() {
  assistant.classList.add('returning');
  setPosition(desktop.clientWidth / 2 - assistant.offsetWidth / 2, 55);
  setTimeout(() => assistant.classList.remove('returning'), 750);
}
function setMood(mood) {
  assistant.dataset.mood = mood;
  const captions = {
    idle: attached ? `已连接 · ${contexts[attached].title}` : '点击聊天 · 拖到窗口 ✦',
    dragging: '正在前往目标窗口…',
    thinking: '让我想一想…',
    working: '正在处理…',
    done: '完成啦 ✦'
  };
  document.getElementById('petCaption').textContent = captions[mood];
}
function cancelReply() {
  activityVersion += 1;
  busy = false;
  replyController?.abort();
  replyController = null;
  chatMessages.querySelectorAll('.typing-dots').forEach(bubble => bubble.remove());
}
function setMenuOpen(open) {
  avatarMenu.hidden = !open;
  menuButton.setAttribute('aria-expanded', String(open));
}
function updateReplyNote() {
  document.getElementById('chatFootnote').textContent = settings.mode === 'api'
    ? '✦ 自定义接口 · 窗口内容为演示数据'
    : '✦ 交互演示 · 操作仅作用于当前原型';
}
function assistantCenter() {
  const r = island.getBoundingClientRect();
  const d = bounds();
  return { x: r.left - d.left + r.width / 2, y: r.top - d.top + r.height / 2 };
}
function ribbonPath(start, end, startRadius, endRadius, bend = 0, waistRadius = Math.min(startRadius, endRadius) * .62) {
  const dx = end.x - start.x, dy = end.y - start.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / length, uy = dy / length;
  const nx = -uy, ny = ux;
  const pull = Math.min(length * .22, 40);
  const s1 = { x: start.x + nx * startRadius, y: start.y + ny * startRadius };
  const s2 = { x: start.x - nx * startRadius, y: start.y - ny * startRadius };
  const e1 = { x: end.x + nx * endRadius, y: end.y + ny * endRadius };
  const e2 = { x: end.x - nx * endRadius, y: end.y - ny * endRadius };
  const tip = Math.min(8, length * .12);
  const bx = nx * bend, by = ny * bend;
  const mid = { x: (start.x + end.x) / 2 + bx, y: (start.y + end.y) / 2 + by };
  const m1 = { x: mid.x + nx * waistRadius, y: mid.y + ny * waistRadius };
  const m2 = { x: mid.x - nx * waistRadius, y: mid.y - ny * waistRadius };
  return `M ${s1.x} ${s1.y} C ${s1.x + ux * pull} ${s1.y + uy * pull}, ${m1.x - ux * pull} ${m1.y - uy * pull}, ${m1.x} ${m1.y} C ${m1.x + ux * pull} ${m1.y + uy * pull}, ${e1.x - ux * pull} ${e1.y - uy * pull}, ${e1.x} ${e1.y} Q ${end.x + ux * tip} ${end.y + uy * tip}, ${e2.x} ${e2.y} C ${e2.x - ux * pull} ${e2.y - uy * pull}, ${m2.x + ux * pull} ${m2.y + uy * pull}, ${m2.x} ${m2.y} C ${m2.x - ux * pull} ${m2.y - uy * pull}, ${s2.x + ux * pull} ${s2.y + uy * pull}, ${s2.x} ${s2.y} Q ${start.x - ux * tip} ${start.y - uy * tip}, ${s1.x} ${s1.y} Z`;
}
function drawTether(end, now = performance.now()) {
  if (!tetherOrigin) return;
  tetherLiveEnd = end;
  const dx = end.x - tetherOrigin.x, dy = end.y - tetherOrigin.y;
  const distance = Math.hypot(dx, dy);
  const tension = clamp((distance - 12) / (TETHER_BREAK_DISTANCE - 12), 0, 1);
  if (tetherLastEnd && distance > 1) {
    const sideSpeed = (-dy * (end.x - tetherLastEnd.x) + dx * (end.y - tetherLastEnd.y)) / distance;
    tetherBend = tetherBend * .55 + clamp(sideSpeed * 1.2, -22, 22) * .45;
  }
  tetherLastEnd = end;
  const ripple = reducedMotion.matches ? 0 : Math.sin(now * .018) * .55 * Math.sin(Math.PI * tension);
  tetherWaist = Math.max(2.2, 14 - 11.5 * Math.pow(tension, .58) + ripple);
  tetherPath.setAttribute('d', ribbonPath(tetherOrigin, end, 16 - tension * 5, 18 - tension * 4, tetherBend, tetherWaist));
}
function flowTether(now) {
  if (!dragging || !draggingWithTether || tetherBroken || reducedMotion.matches) return;
  tetherBend *= .96;
  drawTether(assistantCenter(), now);
  tetherFlowFrame = requestAnimationFrame(flowTether);
}
function clearTether() {
  cancelAnimationFrame(tetherFrame);
  cancelAnimationFrame(tetherFlowFrame);
  tetherToken += 1;
  tetherLiveEnd = null;
  tetherLastEnd = null;
  tetherBend = 0;
  tetherWaist = 14;
  tetherPath.setAttribute('d', '');
  tetherPath.style.opacity = '';
  tetherStartFragment.setAttribute('d', '');
  tetherEndFragment.setAttribute('d', '');
  tetherStartFragment.style.opacity = '';
  tetherEndFragment.style.opacity = '';
  tetherDroplets.forEach(drop => { drop.style.opacity = '0'; });
  tetherLayer.classList.remove('visible', 'breaking');
  notchFace.classList.remove('visible');
}
function releaseTether(duration = 260) {
  if (!tetherOrigin || !tetherLiveEnd || !tetherLayer.classList.contains('visible')) return;
  cancelAnimationFrame(tetherFlowFrame);
  if (reducedMotion.matches) duration = 60;
  const token = ++tetherToken;
  const origin = { ...tetherOrigin };
  const startTime = performance.now();
  tetherLayer.classList.add('breaking');
  const animate = now => {
    if (token !== tetherToken) return;
    const progress = clamp((now - startTime) / duration, 0, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const end = assistantCenter();
    tetherLiveEnd = end;
    const dx = end.x - origin.x, dy = end.y - origin.y;
    const length = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / length, uy = dy / length;
    const reach = .5 - .47 * eased;
    const startTip = { x: origin.x + dx * reach, y: origin.y + dy * reach };
    const endTip = { x: end.x - dx * reach, y: end.y - dy * reach };
    tetherStartFragment.setAttribute('d', ribbonPath(origin, startTip, 13 * (1 - eased * .5), Math.max(.3, 8 * (1 - eased))));
    tetherEndFragment.setAttribute('d', ribbonPath(end, endTip, 16 * (1 - eased * .5), Math.max(.3, 8 * (1 - eased))));
    const crossfade = clamp(progress / .16, 0, 1);
    if (crossfade < 1) tetherPath.setAttribute('d', ribbonPath(origin, end, 12, 15, tetherBend * (1 - crossfade), tetherWaist * (1 - crossfade * .25)));
    tetherPath.style.opacity = String(1 - crossfade);
    const opacity = crossfade * Math.min(1, (1 - progress) * 1.5);
    tetherStartFragment.style.opacity = String(opacity);
    tetherEndFragment.style.opacity = String(opacity);
    const midX = (origin.x + end.x) / 2, midY = (origin.y + end.y) / 2;
    const normalX = -uy, normalY = ux;
    tetherDroplets.forEach((drop, index) => {
      const spread = (index - 1) * (9 + 22 * eased);
      drop.setAttribute('cx', String(midX + normalX * spread + ux * (index - 1) * 12 * eased));
      drop.setAttribute('cy', String(midY + normalY * spread + uy * (index - 1) * 12 * eased));
      drop.style.opacity = String(Math.sin(progress * Math.PI) * .8);
    });
    if (progress < 1) tetherFrame = requestAnimationFrame(animate);
    else clearTether();
  };
  tetherFrame = requestAnimationFrame(animate);
}
function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2700);
}
function windowAt(x, y) {
  return windows.filter(win => {
    const r = win.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }).sort((a, b) => Number(getComputedStyle(b).zIndex) - Number(getComputedStyle(a).zIndex))[0] || null;
}
function setHover(win) {
  if (hoverTarget === win) return;
  windows.forEach(w => w.classList.toggle('drop-target', w === win));
  hoverTarget = win;
  if (win) {
    const context = contexts[win.dataset.window];
    dropHint.style.setProperty('--hint-accent', context.accent);
    document.getElementById('dropHintIcon').textContent = context.icon;
    document.getElementById('dropHintTitle').textContent = `松开以接管 · ${context.title}`;
    document.getElementById('dropHintDetail').textContent = context.action;
    const r = win.getBoundingClientRect();
    const d = bounds();
    dropHint.style.left = `${clamp(r.left - d.left + r.width / 2, 120, desktop.clientWidth - 120)}px`;
    dropHint.style.top = `${clamp(r.top - d.top + 72, 100, desktop.clientHeight - 100)}px`;
    dropHint.classList.toggle('visible', settings.hints);
  } else {
    dropHint.classList.remove('visible');
  }
}
function addMessage(text, type = 'ai') {
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${type === 'user' ? 'user-message' : 'ai-message'}`;
  bubble.textContent = text;
  chatMessages.appendChild(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  if (attached) requestAnimationFrame(() => positionAtWindow(document.querySelector(`[data-window="${attached}"]`)));
  else if (assistant.classList.contains('panel-open')) requestAnimationFrame(positionIdlePanel);
  return bubble;
}
function renderSuggestions() {
  suggestions.replaceChildren();
  const prompts = attached ? contexts[attached].prompts : idlePrompts;
  prompts.forEach(label => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => sendMessage(label));
    suggestions.appendChild(button);
  });
}
function positionIdlePanel() {
  const width = Math.min(350, desktop.clientWidth - 24);
  chatPanel.style.width = `${width}px`;
  const r = assistant.getBoundingClientRect();
  const d = bounds();
  const left = clamp(r.left - d.left + r.width / 2 - width / 2, 12, desktop.clientWidth - width - 12);
  const below = r.bottom - d.top + 15;
  const height = chatPanel.offsetHeight || 400;
  const top = below + height < desktop.clientHeight - 15 ? below : clamp(r.top - d.top - height - 14, 46, desktop.clientHeight - height - 12);
  chatPanel.style.left = `${left - (r.left - d.left)}px`;
  chatPanel.style.top = `${top - (r.top - d.top)}px`;
}
function openIdlePanel() {
  document.getElementById('chatAppIcon').textContent = '✦';
  document.getElementById('chatTitle').textContent = 'Orbit AI';
  document.getElementById('chatPath').textContent = '待命中 · 可以直接聊天';
  document.getElementById('contextTitle').textContent = '随时可以聊聊';
  document.getElementById('contextDescription').textContent = '把我拖到窗口，或直接问我问题';
  document.getElementById('detachButton').title = '收起对话';
  chatMessages.replaceChildren();
  idleHistory.forEach(entry => addMessage(entry.text, entry.type));
  renderSuggestions();
  positionIdlePanel();
  chatPanel.setAttribute('aria-hidden', 'false');
  assistant.classList.add('panel-open');
  chatInput.focus();
}
function closePanel() {
  assistant.classList.remove('panel-open');
  chatPanel.setAttribute('aria-hidden', 'true');
}
function positionAtWindow(win) {
  const r = win.getBoundingClientRect();
  const d = bounds();
  setPosition(r.left - d.left + r.width / 2 - assistant.offsetWidth / 2, r.top - d.top - 54);
  const windowLeft = r.left - d.left;
  const windowRight = r.right - d.left;
  const gap = 16;
  const leftSpace = windowLeft - gap - 12;
  const rightSpace = desktop.clientWidth - windowRight - gap - 12;
  const useRight = rightSpace >= 330 || rightSpace > leftSpace;
  const available = useRight ? rightSpace : leftSpace;
  const panelWidth = Math.min(370, Math.max(200, available));
  const panelLeft = clamp(useRight ? windowRight + gap : windowLeft - gap - panelWidth, 12, desktop.clientWidth - panelWidth - 12);
  chatPanel.style.width = `${panelWidth}px`;
  const panelTop = clamp(r.top - d.top + 62, 48, Math.max(48, desktop.clientHeight - chatPanel.offsetHeight - 14));
  chatPanel.style.left = `${panelLeft - parseFloat(assistant.style.left)}px`;
  chatPanel.style.top = `${panelTop - parseFloat(assistant.style.top)}px`;
  chatPanel.dataset.side = useRight ? 'right' : 'left';
}
function attach(win) {
  if (!win) return;
  const kind = win.dataset.window;
  const context = contexts[kind];
  connectionVersion += 1;
  cancelReply();
  windows.forEach(w => w.classList.toggle('attached-window', w === win));
  attached = kind;
  raiseWindow(win);
  assistant.style.setProperty('--accent', context.accent);
  assistant.style.setProperty('--accent-light', context.light);
  assistant.classList.remove('pet-mode');
  assistant.classList.remove('tether-broken');
  assistant.classList.add('attached', 'snap-burst');
  setMood('idle');
  const version = connectionVersion;
  setTimeout(() => { if (version === connectionVersion) assistant.classList.remove('snap-burst'); }, reducedMotion.matches ? 70 : 960);
  positionAtWindow(win);
  document.getElementById('islandTitle').textContent = `Orbit · ${context.title}`;
  document.getElementById('islandSubtitle').textContent = '已连接到当前窗口';
  document.getElementById('petCaption').textContent = `已连接 · ${context.title}`;
  document.getElementById('chatAppIcon').textContent = context.icon;
  document.getElementById('chatTitle').textContent = `已连接 · ${context.title}`;
  document.getElementById('chatPath').textContent = context.path;
  document.getElementById('contextTitle').textContent = '已读取窗口上下文';
  document.getElementById('contextDescription').textContent = context.description;
  document.getElementById('detachButton').title = '断开连接';
  chatMessages.replaceChildren();
  addMessage(context.greeting);
  renderSuggestions();
  positionAtWindow(win);
  chatPanel.setAttribute('aria-hidden', String(!settings.autoOpen));
  if (settings.autoOpen) setTimeout(() => { if (version === connectionVersion && !dragging) assistant.classList.add('panel-open'); }, reducedMotion.matches ? 70 : 560);
  document.getElementById('guideCard').classList.add('hidden');
  showToast(`Orbit 已接管「${context.title}」演示窗口`);
}
function detach() {
  connectionVersion += 1;
  cancelReply();
  attached = null;
  assistant.classList.remove('attached', 'panel-open');
  assistant.classList.remove('tether-broken');
  assistant.classList.add('pet-mode');
  assistant.style.width = '';
  assistant.style.setProperty('--accent', '#8370da');
  assistant.style.setProperty('--accent-light', '#eeeaff');
  windows.forEach(w => w.classList.remove('attached-window'));
  document.getElementById('islandTitle').textContent = 'Orbit AI';
  document.getElementById('islandSubtitle').textContent = '拖到一个窗口，开始协作';
  setMood('idle');
  chatPanel.setAttribute('aria-hidden', 'true');
  snapHome();
  showToast('连接已断开，拖动 Orbit 可以选择其他窗口');
}

island.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  setMenuOpen(false);
  event.preventDefault();
  island.setPointerCapture(event.pointerId);
  const rect = assistant.getBoundingClientRect();
  pointerStart = { x: event.clientX, y: event.clientY };
  dragOffset = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  draggingWithTether = true;
  clearTether();
  tetherBroken = false;
  assistant.classList.remove('returning', 'tether-broken', 'snap-burst');
  tetherOrigin = assistantCenter();
  if (tetherOrigin) {
    notchFace.style.left = `${tetherOrigin.x - 26}px`;
    notchFace.style.top = `${tetherOrigin.y - 26}px`;
  }
  dragging = false;
  panelWasOpen = assistant.classList.contains('panel-open');
});
island.addEventListener('pointermove', event => {
  if (!pointerStart) return;
  if (!dragging && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 6) {
    dragging = true;
    cancelReply();
    assistant.classList.add('dragging');
    setMood('dragging');
    closePanel();
    if (draggingWithTether) {
      tetherLayer.classList.add('visible');
      notchFace.classList.add('visible');
      if (!reducedMotion.matches) tetherFlowFrame = requestAnimationFrame(flowTether);
    }
  }
  if (!dragging) return;
  const d = bounds();
  setPosition(event.clientX - d.left - dragOffset.x, event.clientY - d.top - dragOffset.y);
  if (draggingWithTether) {
    const end = assistantCenter();
    tetherLiveEnd = end;
    if (!tetherBroken && Math.hypot(end.x - tetherOrigin.x, end.y - tetherOrigin.y) > TETHER_BREAK_DISTANCE) {
      drawTether(end);
      tetherBroken = true;
      assistant.classList.add('tether-broken');
      releaseTether(280);
    } else if (!tetherBroken) drawTether(end);
  }
  setHover(windowAt(event.clientX, event.clientY));
});
function endDrag(event) {
  if (!pointerStart) return;
  if (dragging) {
    const target = windowAt(event.clientX, event.clientY);
    assistant.classList.remove('dragging');
    if (draggingWithTether && !tetherBroken) releaseTether(190);
    setHover(null);
    if (event.type === 'pointercancel') {
      if (attached) {
        assistant.classList.toggle('panel-open', panelWasOpen);
        chatPanel.setAttribute('aria-hidden', String(!panelWasOpen));
        positionAtWindow(document.querySelector(`[data-window="${attached}"]`));
        setMood('idle');
      } else { snapHome(); setMood('idle'); }
    } else if (target && attached === target.dataset.window) {
      positionAtWindow(target);
      assistant.classList.toggle('panel-open', panelWasOpen || settings.autoOpen);
      chatPanel.setAttribute('aria-hidden', String(!assistant.classList.contains('panel-open')));
      setMood('idle');
    } else if (target) attach(target);
    else if (attached) detach();
    else { snapHome(); setMood('idle'); }
  } else if (attached) {
    const open = assistant.classList.toggle('panel-open');
    chatPanel.setAttribute('aria-hidden', String(!open));
  } else if (assistant.classList.contains('panel-open')) closePanel();
  else openIdlePanel();
  pointerStart = null;
  dragOffset = null;
  dragging = false;
  tetherOrigin = null;
  draggingWithTether = false;
  tetherBroken = false;
}
island.addEventListener('pointerup', endDrag);
island.addEventListener('pointercancel', endDrag);
island.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (assistant.classList.contains('panel-open')) closePanel(); else if (attached) { assistant.classList.add('panel-open'); chatPanel.setAttribute('aria-hidden', 'false'); } else openIdlePanel(); }
});
document.getElementById('detachButton').addEventListener('click', () => attached ? detach() : closePanel());
document.getElementById('guideClose').addEventListener('click', () => document.getElementById('guideCard').classList.add('hidden'));

function setSettingsStatus(message, error = false) {
  settingsStatus.textContent = message;
  settingsStatus.classList.toggle('error', error);
}
function updateApiFields() {
  const apiMode = document.getElementById('replyMode').value === 'api';
  document.getElementById('apiFields').classList.toggle('visible', apiMode);
  for (const id of ['apiEndpoint', 'apiModel', 'apiToken']) document.getElementById(id).disabled = !apiMode;
  document.getElementById('testApi').disabled = !apiMode;
}
function openSettings() {
  setMenuOpen(false);
  document.getElementById('replyMode').value = settings.mode;
  document.getElementById('apiEndpoint').value = settings.endpoint;
  document.getElementById('apiModel').value = settings.model;
  document.getElementById('apiToken').value = apiToken;
  document.getElementById('autoOpenPanel').checked = settings.autoOpen;
  document.getElementById('showDragHints').checked = settings.hints;
  setSettingsStatus('');
  updateApiFields();
  settingsBackdrop.hidden = false;
  settingsPanel.hidden = false;
  document.getElementById('replyMode').focus();
}
function closeSettings() {
  settingsBackdrop.hidden = true;
  settingsPanel.hidden = true;
  island.focus();
}
function validEndpoint(value) {
  try { const url = new URL(value); return url.protocol === 'http:' || url.protocol === 'https:'; }
  catch { return false; }
}
async function requestApi(message, context, history, endpoint, model, token, signal) {
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ message, model, context, history }),
    signal
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  if (typeof data.reply !== 'string' || !data.reply.trim()) throw new Error('接口未返回 reply 文本');
  return data.reply.trim();
}
island.addEventListener('contextmenu', event => {
  event.preventDefault();
  const d = bounds();
  avatarMenu.style.left = `${clamp(event.clientX - d.left, 8, desktop.clientWidth - 185)}px`;
  avatarMenu.style.top = `${clamp(event.clientY - d.top, 40, desktop.clientHeight - 135)}px`;
  setMenuOpen(true);
});
menuButton.addEventListener('click', () => {
  const opening = avatarMenu.hidden;
  avatarMenu.style.left = '28px';
  avatarMenu.style.top = '38px';
  setMenuOpen(opening);
});
avatarMenu.addEventListener('click', event => {
  const action = event.target.closest('button')?.dataset.action;
  setMenuOpen(false);
  if (action === 'settings') openSettings();
  if (action === 'reset') { if (attached) detach(); else { cancelReply(); closePanel(); snapHome(); setMood('idle'); } }
  if (action === 'clear') {
    cancelReply();
    idleHistory.splice(1);
    if (attached) { chatMessages.replaceChildren(); addMessage('对话已清空。'); }
    else if (assistant.classList.contains('panel-open')) openIdlePanel();
    setMood('idle');
    showToast('对话已清空');
  }
});
document.addEventListener('pointerdown', event => { if (!avatarMenu.hidden && !avatarMenu.contains(event.target) && !island.contains(event.target) && !menuButton.contains(event.target)) setMenuOpen(false); });
document.getElementById('replyMode').addEventListener('change', updateApiFields);
document.getElementById('closeSettings').addEventListener('click', closeSettings);
settingsBackdrop.addEventListener('click', closeSettings);
document.getElementById('settingsForm').addEventListener('submit', event => {
  event.preventDefault();
  const mode = document.getElementById('replyMode').value;
  const endpoint = document.getElementById('apiEndpoint').value.trim();
  if (mode === 'api' && !validEndpoint(endpoint)) return setSettingsStatus('请输入完整的 http:// 或 https:// 接口地址。', true);
  settings = {
    mode, endpoint, model: document.getElementById('apiModel').value.trim(),
    autoOpen: document.getElementById('autoOpenPanel').checked,
    hints: document.getElementById('showDragHints').checked
  };
  apiToken = document.getElementById('apiToken').value.trim();
  try { localStorage.setItem('orbit-settings', JSON.stringify(settings)); }
  catch { /* Private browsing may disable storage; the current page still uses these settings. */ }
  document.getElementById('guideCard').classList.toggle('hidden', !settings.hints);
  if (!settings.hints) dropHint.classList.remove('visible');
  updateReplyNote();
  closeSettings();
  showToast('设置已保存');
});
document.getElementById('testApi').addEventListener('click', async () => {
  const endpoint = document.getElementById('apiEndpoint').value.trim();
  if (!validEndpoint(endpoint)) return setSettingsStatus('请输入完整的接口地址。', true);
  setSettingsStatus('正在连接接口…');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    await requestApi('ping', null, [], endpoint, document.getElementById('apiModel').value.trim(), document.getElementById('apiToken').value.trim(), controller.signal);
    setSettingsStatus('连接成功，接口返回了 reply 文本。');
  } catch (error) { setSettingsStatus(`连接失败：${error.name === 'AbortError' ? '请求超时' : error.message}`, true); }
  finally { clearTimeout(timeout); }
});
if (!settings.hints) document.getElementById('guideCard').classList.add('hidden');
updateReplyNote();
window.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (!settingsPanel.hidden) closeSettings();
  else if (!avatarMenu.hidden) setMenuOpen(false);
  else if (assistant.classList.contains('panel-open')) { if (attached) detach(); else closePanel(); }
});

function raiseWindow(win) {
  windowOrder = windowOrder.filter(item => item !== win).concat(win);
  windowOrder.forEach((item, index) => { item.style.zIndex = String(index + 1); });
  topZ = windowOrder.length;
}
windows.forEach(win => win.addEventListener('pointerdown', () => raiseWindow(win)));
bindFiles();
document.querySelectorAll('.dock-icon').forEach((icon, index) => icon.addEventListener('click', () => {
  const kind = ['finder', 'browser', 'editor'][index];
  if (!kind) return showToast('这是桌面交互原型');
  const win = document.querySelector(`[data-window="${kind}"]`);
  raiseWindow(win);
  win.animate([{transform:'translateY(5px)',opacity:.8},{transform:'translateY(0)',opacity:1}],{duration:320,easing:'ease-out'});
}));

function bindFiles() {
  fileGrid.querySelectorAll('.file-item').forEach(file => {
    file.addEventListener('click', () => {
      const name = file.dataset.file || file.querySelector('span')?.textContent || '项目';
      showToast(`已选中 ${name}`);
    });
  });
}
function organizeFiles() {
  fileGrid.classList.add('organized');
  fileGrid.innerHTML = [
    ['视觉设计', '3 个文件', '#d9d1f8'],
    ['品牌文档', '2 个文件', '#f3d8c6'],
    ['视频素材', '1 个文件', '#c9d9ec']
  ].map(([name,count,color], index) => `<div class="file-item organized-file" style="animation-delay:${index * 70}ms"><div class="file-art" style="display:grid;place-items:center;background:${color};font-size:39px;color:#786f9b;box-shadow:0 6px 12px #4f557d1d">▰</div><span>${name}</span><small>${count}</small></div>`).join('');
  document.getElementById('fileCount').textContent = '3 个文件夹 · 6 个文件';
  contexts.finder.prompts = ['恢复原始视图', '这些文件怎么分类的？', '还有什么建议？'];
  contexts.finder.description = '已按类型整理为 3 个文件夹';
  bindFiles();
  renderSuggestions();
}
function restoreFiles() {
  fileGrid.classList.remove('organized');
  fileGrid.innerHTML = originalFiles;
  document.getElementById('fileCount').textContent = '6 个项目';
  contexts.finder.prompts = ['整理这些文件', '这里有什么内容？', '找出设计源文件'];
  contexts.finder.description = '当前路径及 6 个文件已就绪';
  bindFiles();
  renderSuggestions();
}
function answerFor(message) {
  const q = message.toLowerCase();
  if (attached === 'finder') {
    if (q.includes('恢复') || q.includes('还原')) { restoreFiles(); return '已在原型中恢复原始文件视图。'; }
    if (q.includes('整理') || q.includes('分类')) { organizeFiles(); return '已在原型中按视觉设计、品牌文档和视频素材整理了 6 个文件。你可以点击“恢复原始视图”撤销。'; }
    if (q.includes('源文件') || q.includes('fig') || q.includes('ai')) return '找到 2 个设计源文件：首页视觉.fig（8.6 MB）和 Logo 源文件.ai（3.2 MB）。';
    return '这个文件夹位于 ~/项目/设计资料，包含 6 个项目：品牌手册、首页视觉、产品截图、动效参考、文案草稿和 Logo 源文件。';
  }
  if (attached === 'editor') {
    if (q.includes('建议') || q.includes('改进')) return '当前 attachToWindow 把目标窗口与读取到的上下文传给 agent.connect。建议补充空窗口处理、权限状态和连接失败时的反馈。';
    if (q.includes('文件') || q.includes('项目')) return '项目包含 src/App.tsx、src/island.ts、src/styles.css、public 文件夹和 package.json。当前查看的是 island.ts。';
    return '当前代码定义 attachToWindow(target)：读取目标窗口上下文后，以 assist 模式连接 agent。这里展示的是原型中的示例代码。';
  }
  if (attached === 'browser') {
    if (q.includes('三个') || q.includes('要点')) return '三个要点：① 页面介绍 Orbit 的设计理念；② 重点是让灵感保持流动；③ 提供快速开始和组件指南两个入口。';
    return '这个入门页面介绍 Orbit 的产品理念：“把灵感留在当下，让工作自然发生”，并提供快速开始与组件指南入口。';
  }
  return '';
}
function idleAnswerFor(message) {
  const q = message.toLowerCase();
  if (q.includes('计划') || q.includes('今天')) return '可以先列出今天最重要的三件事。我建议先完成最费脑力的一件，再处理沟通和琐事。';
  if (q.includes('灵感') || q.includes('创意')) return '试着把手头的问题改写成一句“如果没有现有限制，我会怎么做？”也许能找到一个新角度。';
  if (q.includes('能做什么') || q.includes('帮助')) return '待命时可以直接和我聊天；拖到文件、代码或浏览器窗口后，我会根据那个演示窗口提供建议。右键头像可以设置自定义后端接口。';
  return '我在这里。你可以和我聊想法、梳理计划，或者把我拖到一个窗口上一起处理其中的内容。';
}
async function sendMessage(message) {
  if (busy || !message.trim()) return;
  const value = message.trim();
  const target = attached;
  const history = [...chatMessages.querySelectorAll('.chat-bubble:not(.typing-dots)')].slice(-10).map(bubble => ({ role: bubble.classList.contains('user-message') ? 'user' : 'assistant', content: bubble.textContent }));
  addMessage(value, 'user');
  if (!target) idleHistory.push({ type: 'user', text: value });
  chatInput.value = '';
  const typing = document.createElement('div');
  typing.className = 'chat-bubble ai-message typing-dots';
  typing.innerHTML = '<i></i><i></i><i></i>';
  chatMessages.appendChild(typing);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  busy = true;
  const activity = ++activityVersion;
  setMood('thinking');
  const version = connectionVersion;
  const stillCurrent = () => attached === target && version === connectionVersion && activity === activityVersion;
  let completed = false;
  setTimeout(() => { if (!completed && stillCurrent()) setMood('working'); }, reducedMotion.matches ? 100 : 420);
  try {
    let reply;
    if (settings.mode === 'api') {
      if (!validEndpoint(settings.endpoint)) throw new Error('请先在右键设置中填写接口地址');
      const controller = new AbortController();
      replyController = controller;
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const context = target ? { kind: target, title: contexts[target].title, path: contexts[target].path, description: contexts[target].description } : null;
        reply = await requestApi(value, context, history, settings.endpoint, settings.model, apiToken, controller.signal);
      } finally {
        clearTimeout(timeout);
        if (replyController === controller) replyController = null;
      }
    } else {
      await new Promise(resolve => setTimeout(resolve, reducedMotion.matches ? 250 : 1150));
      if (!stillCurrent()) return;
      reply = target ? answerFor(value) : idleAnswerFor(value);
    }
    if (!stillCurrent()) return;
    addMessage(reply);
    if (!target) idleHistory.push({ type: 'ai', text: reply });
    setMood('done');
    setTimeout(() => { if (stillCurrent()) setMood('idle'); }, reducedMotion.matches ? 400 : 1500);
  } catch (error) {
    if (stillCurrent()) {
      const detail = error.name === 'AbortError' ? '请求超时' : error.message;
      const reply = `接口请求失败：${detail}`;
      addMessage(reply);
      if (!target) idleHistory.push({ type: 'ai', text: reply });
      setMood('idle');
    }
  } finally {
    completed = true;
    typing.remove();
    if (stillCurrent()) busy = false;
  }
}
document.getElementById('chatForm').addEventListener('submit', event => {
  event.preventDefault();
  sendMessage(chatInput.value);
});
window.addEventListener('resize', () => {
  if (attached && !dragging) positionAtWindow(document.querySelector(`[data-window="${attached}"]`));
  else if (!attached && assistant.classList.contains('panel-open')) positionIdlePanel();
});

const clock = document.getElementById('clock');
function updateClock() { clock.textContent = new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date()); }
updateClock();
setInterval(updateClock, 60000);
