/**
 * WebRAG 豆包风格前端 — React18 + Ant Design v5 单页应用
 * 通过 CDN 加载，Babel Standalone 在浏览器内编译 JSX
 * ====================================================================
 */

// ===== Destructure from globals =====
const { useState, useEffect, useRef, useCallback, useMemo } = React;
const {
  ConfigProvider, Button, Slider, Switch, Input, InputNumber,
  Tooltip, Spin, message, Tag, Empty, Divider, Modal, Tabs,
} = antd;
const { TextArea } = Input;

// ===== Constants =====
const PRIMARY = '#1666FF';
const SK_TOKEN = 'webrag_token';
const SK_USER = 'webrag_user';
const SK_CURRENT = 'webrag_current_conv';
const SK_SETTINGS = 'webrag_settings';
const SK_LEFT = 'webrag.left_collapsed';
const SK_RIGHT = 'webrag.right_collapsed';

const ERROR_MSG = {
  VALIDATION_ERROR: '请求参数不合法',
  SEARCH_FAILED: '检索失败，请稍后重试',
  TIMEOUT: '请求超时，请稍后重试',
  LLM_FAILED: '生成回答失败，请稍后重试',
  EMPTY_RESULT: '知识库中未检索到相关内容',
  UNAUTHORIZED: '请先登录后再提问',
  FORBIDDEN: '无权限访问',
  INTERNAL_ERROR: '服务内部错误，请稍后重试',
};

const QUICK_EXAMPLES = [
  { icon: 'search', text: '什么是RAG？' },
  { icon: 'doc', text: '什么是BGE-M3？' },
  { icon: 'search', text: '什么是大模型ReAct？' },
];

// ===== Icon Components (inline SVG) =====
const Icon = {
  Logo: ({ size = 20 }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M12 2L2 7v6c0 5 4 9 10 9s10-4 10-9V7L12 2z" fill="white" opacity="0.9"/>
      <path d="M8 12l2 2 6-6" stroke={PRIMARY} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Plus: ({ size = 18, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M12 5v14M5 12h14" stroke={color} strokeWidth="2" strokeLinecap="round"/>
    </svg>
  ),
  Send: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M12 19V5M5 12l7-7 7 7" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Settings: ({ size = 18, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="3" stroke={color} strokeWidth="2"/>
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" stroke={color} strokeWidth="1.5"/>
    </svg>
  ),
  ChevronLeft: ({ size = 18, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M15 18l-6-6 6-6" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  ChevronRight: ({ size = 18, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M9 18l6-6-6-6" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Chat: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" stroke={color} strokeWidth="1.5"/>
    </svg>
  ),
  ThumbsUp: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3zM7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  ThumbsDown: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3zM17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Thermometer: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M14 14.76V3.5a2.5 2.5 0 00-5 0v11.26a4 4 0 105 0z" stroke={color} strokeWidth="1.5"/>
    </svg>
  ),
  Globe: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke={color} strokeWidth="1.5"/>
      <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" stroke={color} strokeWidth="1.5"/>
    </svg>
  ),
  FileText: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke={color} strokeWidth="1.5"/>
      <path d="M14 2v6h6M8 13h8M8 17h5" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  Stop: ({ size = 14, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="6" y="6" width="12" height="12" rx="2" fill={color}/>
    </svg>
  ),
  Copy: ({ size = 14, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="9" y="9" width="13" height="13" rx="2" stroke={color} strokeWidth="1.5"/>
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" stroke={color} strokeWidth="1.5"/>
    </svg>
  ),
  Check: ({ size = 14, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M20 6L9 17l-5-5" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Search: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="11" cy="11" r="8" stroke={color} strokeWidth="2"/>
      <path d="M21 21l-4.35-4.35" stroke={color} strokeWidth="2" strokeLinecap="round"/>
    </svg>
  ),
  Robot: ({ size = 16, color = 'white' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="4" y="8" width="16" height="12" rx="2" stroke={color} strokeWidth="1.5"/>
      <path d="M12 8V4M9 4h6" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
      <circle cx="9" cy="13" r="1" fill={color}/>
      <circle cx="15" cy="13" r="1" fill={color}/>
      <path d="M9 17h6" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  User: ({ size = 16, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="8" r="4" stroke={color} strokeWidth="1.5"/>
      <path d="M4 21v-1a6 6 0 016-6h4a6 6 0 016 6v1" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  Trash: ({ size = 14, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Logout: ({ size = 14, color = 'currentColor' }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
};

// ===== Utility Functions =====
function clientTimeIso() {
  return new Date().toISOString();
}

// 追问业务：历史消息归一化 → [{role, content}]（滤掉流式中/加载中的占位与空内容，取最近 20 条；空历史返回 undefined）
function normalizeHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return undefined;
  const msgs = history
    .filter(m => (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .filter(m => m.status !== 'loading' && m.status !== 'streaming')
    .slice(-20)
    .map(m => ({ role: m.role, content: m.content }));
  return msgs.length > 0 ? msgs : undefined;
}

function buildPayload(question, params, history) {
  const payload = { question, client_time: clientTimeIso() };
  if (typeof params.temperature === 'number' && isFinite(params.temperature)) {
    payload.temperature = params.temperature;
  }
  if (typeof params.useWebSearch === 'boolean') {
    payload.use_web_search = params.useWebSearch;
  }
  if (Number.isInteger(params.webTopN) && params.webTopN >= 1 && params.webTopN <= 20) {
    payload.web_top_n = params.webTopN;
  }
  const hist = normalizeHistory(history);
  if (hist) payload.history = hist;  // 追问业务：服务端据此判定追问并改写
  return payload;
}

// ===== API Layer =====
async function askStream(question, params, history, callbacks, controllerRef) {
  const controller = new AbortController();
  if (controllerRef) controllerRef.current = controller;
  const timeout = setTimeout(() => controller.abort(), 200000);

  try {
    // /ask/stream 需登录（401 未登录）——必须携带 Bearer token（与 apiFetch 一致）
    const token = localStorage.getItem(SK_TOKEN);
    const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const resp = await fetch('/ask/stream', {
      method: 'POST',
      headers,
      body: JSON.stringify(buildPayload(question, params, history)),
      signal: controller.signal,
    });

    if (!resp.ok) {
      let errBody;
      try { errBody = await resp.json(); } catch { errBody = {}; }
      callbacks.onError(errBody.error || { code: 'INTERNAL_ERROR', message: 'HTTP ' + resp.status });
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';

      for (const frame of frames) {
        if (!frame.trim()) continue;
        const lines = frame.split('\n');
        let event = null;
        let dataParts = [];
        for (const line of lines) {
          if (line.startsWith('event:')) {
            event = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            dataParts.push(line.slice(5));
          }
        }
        const data = dataParts.join('\n').trim();
        if (!event || !data) continue;

        if (event === 'status') {
          callbacks.onStatus(data);
        } else if (event === 'delta') {
          callbacks.onDelta(data);
        } else if (event === 'done') {
          try {
            callbacks.onDone(JSON.parse(data));
          } catch {
            callbacks.onDone({ answer: data });
          }
        } else if (event === 'error') {
          try {
            const err = JSON.parse(data);
            callbacks.onError(err);
          } catch {
            callbacks.onError({ code: 'INTERNAL_ERROR', message: data });
          }
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      callbacks.onAborted && callbacks.onAborted();
    } else {
      callbacks.onError({ code: 'INTERNAL_ERROR', message: err.message });
    }
  } finally {
    clearTimeout(timeout);
    if (controllerRef) controllerRef.current = null;
  }
}

async function submitFeedbackApi(payload) {
  try {
    await fetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return true;
  } catch { return false; }
}

async function fetchHealth() {
  try {
    const resp = await fetch('/health');
    if (!resp.ok) return null;
    return await resp.json();
  } catch { return null; }
}

// ===== Auth / Chat API Layer（统一错误信封，携带 Bearer token）=====
async function apiFetch(path, options = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  const token = localStorage.getItem(SK_TOKEN);
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const resp = await fetch(path, Object.assign({}, options, { headers }));
  let data = {};
  try { data = await resp.json(); } catch { /* 空响应 */ }
  if (!resp.ok || data.error) {
    const err = new Error((data.error && data.error.message) || '请求失败，请稍后重试');
    err.code = (data.error && data.error.code) || 'INTERNAL_ERROR';
    err.status = resp.status;
    throw err;
  }
  return data;
}

async function authApi(mode, username, password) {
  return apiFetch('/auth/' + mode, {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

async function fetchMe() { return apiFetch('/auth/me'); }

async function listConversationsApi() {
  const data = await apiFetch('/chat/conversations');
  return data.conversations || [];
}
async function createConversationApi(title, messages) {
  const data = await apiFetch('/chat/conversations', {
    method: 'POST',
    body: JSON.stringify({ title, messages }),
  });
  return data.conversation;
}
async function getConversationApi(id) {
  const data = await apiFetch('/chat/conversations/' + id);
  return data.conversation;
}
async function saveConversationApi(id, title, messages) {
  await apiFetch('/chat/conversations/' + id, {
    method: 'PUT',
    body: JSON.stringify({ title, messages }),
  });
}
async function deleteConversationApi(id) {
  await apiFetch('/chat/conversations/' + id, { method: 'DELETE' });
}

// ===== Message normalization（服务端往返后归位中断残留状态）=====
function normalizeMessages(msgs) {
  return (msgs || []).map(m => {
    if (m.status === 'loading' || m.status === 'streaming') {
      return { ...m, status: 'done' };
    }
    return m;
  });
}

// ===== Storage Layer =====
function loadSettings() {
  try {
    const data = localStorage.getItem(SK_SETTINGS);
    return data ? JSON.parse(data) : {};
  } catch { return {}; }
}
function saveSettings(s) {
  try { localStorage.setItem(SK_SETTINGS, JSON.stringify(s)); } catch {}
}

// ===== Date Grouping for Conversation List =====
function groupConversations(convs) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const oneDay = 86400000;
  const groups = { today: [], yesterday: [], week: [], earlier: [] };
  for (const c of convs) {
    const d = new Date(c.createdAt || c.updatedAt || Date.now()).getTime();
    if (d >= today) groups.today.push(c);
    else if (d >= today - oneDay) groups.yesterday.push(c);
    else if (d >= today - 7 * oneDay) groups.week.push(c);
    else groups.earlier.push(c);
  }
  return groups;
}

// ===== Markdown Rendering =====
function renderAnswer(answer, sources) {
  if (!answer) return '';
  let html;
  try {
    html = marked.parse(answer, { breaks: true, gfm: true });
  } catch {
    html = '<p>' + answer.replace(/</g, '&lt;') + '</p>';
  }
  html = DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'rel', 'data-index'] });
  html = html.replace(/\[(\d+)\]/g, (match, num) => {
    const idx = parseInt(num, 10);
    const src = (sources || []).find(s => s.index === idx);
    if (src && src.url && !src.url.startsWith('offline://')) {
      return '<a class="cite-link" href="' + src.url + '" target="_blank" rel="noopener noreferrer" data-index="' + idx + '">[' + idx + ']</a>';
    }
    return '<span class="cite-tag">[' + idx + ']</span>';
  });
  return html;
}

// ===== LeftSidebar Component =====
function LeftSidebar({ collapsed, onToggle, conversations, currentId, onSelect, onNew, onDelete, onAccount, user }) {
  const groups = useMemo(() => groupConversations(conversations), [conversations]);
  const groupLabels = { today: '今天', yesterday: '昨天', week: '7天内', earlier: '更早' };
  const isAdmin = user && user.role === 'admin';

  if (collapsed) {
    return (
      <div className="left-sidebar collapsed">
        <div className="sidebar-logo">
          <button className="sidebar-logo-expand" onClick={onToggle} title="展开侧边栏">
            <Icon.ChevronRight size={20} />
          </button>
        </div>
        <button className="new-chat-btn" onClick={onNew} title="新建对话">
          <Icon.Plus size={18} color="white" />
        </button>
        <div className="conv-list"></div>
        <div className="sidebar-footer">
          <button className="sidebar-footer-btn" onClick={onAccount} title={user ? user.username : '账户'}>
            <Icon.User size={18} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="left-sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon"><Icon.Logo size={18} /></div>
        <span className="sidebar-logo-text">WebRAG</span>
        <button className="sidebar-collapse-btn" onClick={onToggle} title="收起侧边栏">
          <Icon.ChevronLeft size={18} />
        </button>
      </div>
      <button className="new-chat-btn" onClick={onNew}>
        <Icon.Plus size={16} color="white" />
        <span>新建对话</span>
      </button>
      <div className="conv-list">
        {conversations.length === 0 ? (
          <div style={{ padding: '20px 10px', textAlign: 'center', color: 'var(--text-quaternary)', fontSize: 13 }}>
            {user ? '暂无对话记录' : '登录后开始提问'}
          </div>
        ) : (
          Object.entries(groups).map(([key, items]) => {
            if (items.length === 0) return null;
            return (
              <div key={key}>
                <div className="conv-group-label">{groupLabels[key]}</div>
                {items.map(conv => (
                  <div key={conv.id} className="conv-item-row">
                    <div
                      className={'conv-item' + (String(conv.id) === String(currentId) ? ' active' : '')}
                      onClick={() => onSelect(conv.id)}
                    >
                      <span className="conv-item-icon"><Icon.Chat size={15} /></span>
                      <span className="conv-item-title">{conv.title || '新对话'}</span>
                    </div>
                    <button
                      className="conv-item-del"
                      title="删除对话"
                      onClick={(e) => { e.stopPropagation(); onDelete(conv.id); }}
                    >
                      <Icon.Trash size={14} />
                    </button>
                  </div>
                ))}
              </div>
            );
          })
        )}
      </div>
      <div className="sidebar-footer">
        <button className="sidebar-footer-btn" onClick={onAccount} title="账户">
          <Icon.User size={18} />
          <span>{user ? (user.username + (isAdmin ? '（管理员）' : '')) : '账户'}</span>
        </button>
      </div>
    </div>
  );
}

// ===== AuthModal（账户：登录 / 注册 / 账户信息）=====
function AuthModal({ open, user, onClose, onLogin, onRegister, onLogout }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setMode(user ? 'info' : 'login');
      setUsername('');
      setPassword('');
      setConfirmPwd('');
    }
  }, [open, user]);

  const submit = async () => {
    if (mode === 'info') return;
    const uname = username.trim();
    if (!uname || !password) { message.warning('请输入用户名和密码'); return; }
    if (mode === 'register') {
      if (password.length < 6) { message.warning('密码至少 6 位'); return; }
      if (password !== confirmPwd) { message.warning('两次输入的密码不一致'); return; }
    }
    setSubmitting(true);
    try {
      if (mode === 'login') await onLogin(uname, password);
      else await onRegister(uname, password);
    } catch (e) {
      message.error(e.message || '操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  const onKeyDown = (e) => { if (e.key === 'Enter' && !submitting) submit(); };

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={380} title={user ? '账户' : '登录 WebRAG'} centered>
      {user ? (
        <div className="account-info">
          <div className="account-avatar"><Icon.User size={22} color="#fff" /></div>
          <div className="account-name">{user.username}</div>
          <div className="account-role">
            <Tag color={user.role === 'admin' ? 'gold' : 'blue'}>
              {user.role === 'admin' ? '管理员' : '普通用户'}
            </Tag>
          </div>
          {user.role === 'admin' && (
            <a href="/admin/" className="account-admin-link" onClick={onClose}>进入后台管理 →</a>
          )}
          <Button type="primary" danger block onClick={onLogout} style={{ marginTop: 16 }}>
            <Icon.Logout size={14} /> 退出登录
          </Button>
        </div>
      ) : (
        <div className="account-form">
          <Tabs
            activeKey={mode}
            onChange={setMode}
            centered
            items={[
              { key: 'login', label: '登录' },
              { key: 'register', label: '注册' },
            ]}
          />
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="用户名"
            maxLength={64}
            autoFocus
            style={{ marginBottom: 12 }}
          />
          <Input.Password
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={mode === 'register' ? '密码（至少 6 位）' : '密码'}
            maxLength={128}
            style={{ marginBottom: 12 }}
          />
          {mode === 'register' && (
            <Input.Password
              value={confirmPwd}
              onChange={(e) => setConfirmPwd(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="确认密码"
              maxLength={128}
              style={{ marginBottom: 12 }}
            />
          )}
          <Button type="primary" block loading={submitting} onClick={submit}>
            {mode === 'login' ? '登 录' : '注 册'}
          </Button>
          <p className="account-hint">
            {mode === 'login' ? '还没有账号？切换到「注册」页签免费创建' : '注册后即可提问；管理员账号由后台创建'}
          </p>
        </div>
      )}
    </Modal>
  );
}

// ===== MessageBubble Component =====
function MessageBubble({ msg, msgIndex, onFeedback, onRetry }) {
  const [copied, setCopied] = useState(false);
  const isUser = msg.role === 'user';
  const isError = msg.status === 'error';
  const isLoading = msg.status === 'loading' || msg.status === 'streaming';

  const handleCopy = () => {
    navigator.clipboard.writeText(msg.content || '').then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (isUser) {
    return (
      <div className="msg-row user">
        <div className="msg-avatar user"><Icon.User size={16} color="#525252" /></div>
        <div className="msg-content">
          <div className="msg-bubble user">{msg.content}</div>
        </div>
      </div>
    );
  }

  // AI message
  const html = msg.content ? renderAnswer(msg.content, msg.sources) : '';

  return (
    <div className="msg-row">
      <div className="msg-avatar ai"><Icon.Robot size={16} /></div>
      <div className="msg-content">
        {isLoading && !msg.content ? (
          <div className="msg-status">
            <span className="msg-status-dot"></span>
            <span>{msg.statusText || '正在思考…'}</span>
          </div>
        ) : isError ? (
          <div className="msg-bubble error">
            {msg.content}
            {onRetry && (
              <button onClick={() => onRetry(msgIndex)} style={{ marginTop: 8, padding: '4px 12px', border: '1px solid #FFCCC7', background: 'white', borderRadius: 6, cursor: 'pointer', color: '#CF1322', fontSize: 12 }}>
                重试
              </button>
            )}
          </div>
        ) : (
          <>
            <div className={'msg-bubble ai' + (isLoading ? ' typing-cursor' : '')} dangerouslySetInnerHTML={{ __html: html }} />
            {!isLoading && msg.content && (
              <div className="msg-meta">
                {msg.cached && msg.cacheTime != null && (
                  <span className="meta-badge cached">⚡ 命中缓存 · {(msg.cacheTime / 1000).toFixed(1)}s</span>
                )}
                {!msg.cached && msg.searchTime != null && (
                  <span className="meta-badge search">🌐 联网搜索 · {(msg.searchTime / 1000).toFixed(1)}s</span>
                )}
                {!msg.cached && msg.genTime != null && (
                  <span className="meta-badge gen">✍ 生成 · {(msg.genTime / 1000).toFixed(1)}s</span>
                )}
                {msg.totalTime != null && (
                  <span className="meta-badge total">⏱ 总计 · {(msg.totalTime / 1000).toFixed(1)}s</span>
                )}
              </div>
            )}
            {msg.sources && msg.sources.length > 0 && (
              <div className="msg-sources">
                <div className="msg-sources-title">参考来源</div>
                <div className="msg-sources-list">
                  {msg.sources.map(src => (
                    <a key={src.index} className="source-card" href={src.url || '#'} target="_blank" rel="noopener noreferrer">
                      <span className="source-card-num">{src.index}</span>
                      <span className="source-card-title">{src.title || src.url}</span>
                    </a>
                  ))}
                </div>
              </div>
            )}
            {!isLoading && msg.content && (
              <div className="msg-feedback">
                <span className="fb-label">回答有帮助吗？</span>
                <button className={'fb-btn' + (msg.feedback === 'good' ? ' active' : '')} onClick={() => onFeedback(msgIndex, 'good')} title="有用">
                  <Icon.ThumbsUp size={15} />
                </button>
                <button className={'fb-btn' + (msg.feedback === 'bad' ? ' active' : '')} onClick={() => onFeedback(msgIndex, 'bad')} title="没用">
                  <Icon.ThumbsDown size={15} />
                </button>
                <button className="fb-btn" onClick={handleCopy} title="复制">
                  {copied ? <Icon.Check size={14} color="#1666FF" /> : <Icon.Copy size={14} />}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ===== EmptyGuide Component =====
function EmptyGuide({ onExample }) {
  return (
    <div className="empty-guide">
      <div className="empty-guide-icon"><Icon.Robot size={28} color={PRIMARY} /></div>
      <div className="empty-guide-title">WebRAG 智能问答</div>
      <div className="empty-guide-sub">
        检索真实网页后生成回答 —— 每个 [n] 引用都可点击溯源。
        默认检索本地知识库，开启联网搜索后可获取实时信息。
      </div>
      <div className="empty-guide-examples">
        {QUICK_EXAMPLES.map((ex, i) => (
          <button key={i} className="example-chip" onClick={() => onExample(ex.text)}>
            {ex.text}
          </button>
        ))}
      </div>
    </div>
  );
}

// ===== InputArea Component =====
function InputArea({ value, onChange, onSend, loading, onStop, useWebSearch }) {
  const textareaRef = useRef(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, []);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!loading && value.trim()) onSend();
    }
  };

  useEffect(() => { adjustHeight(); }, [value, adjustHeight]);

  return (
    <div className="input-area">
      <div className="input-area-inner">
        <div className="input-wrapper">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题… (Enter 发送，Shift+Enter 换行)"
            rows={1}
            maxLength={2000}
            disabled={loading}
          />
          <div className="input-bottom">
            <div className="input-bottom-left">
              <span className={'input-mode-tag' + (useWebSearch ? ' online' : '')}>
                {useWebSearch ? (
                  <><Icon.Globe size={14} /> 联网搜索模式</>
                ) : (
                  <><Icon.Search size={14} /> 本地知识库</>
                )}
              </span>
            </div>
            <div className="input-bottom-right">
              {loading ? (
                <button className="stop-btn" onClick={onStop} title="停止生成">
                  <Icon.Stop size={14} color="white" />
                </button>
              ) : (
                <button className="send-btn" onClick={() => value.trim() && onSend()} disabled={!value.trim()} title="发送">
                  <Icon.Send size={16} color="white" />
                </button>
              )}
            </div>
          </div>
        </div>
        <div className="disclaimer">回答由大模型基于检索到的网页内容生成，仅供参考，请以原始来源为准。</div>
      </div>
    </div>
  );
}

// ===== RightSidebar Component =====
function RightSidebar({ collapsed, onToggle, temperature, onTempChange, useWebSearch, onSearchChange, webTopN, onTopNChange, loading }) {
  return (
    <div className={'right-sidebar' + (collapsed ? ' collapsed' : '')}>
        <div className="right-panel-header">
          <span className="right-panel-title">
            <Icon.Settings size={18} /> 生成参数
          </span>
          <button className="right-panel-toggle" onClick={onToggle} title="收起参数面板">
            <Icon.ChevronRight size={18} />
          </button>
        </div>
        <div className="right-panel-body">
          {/* Temperature Module */}
          <div className="param-module">
            <div className="param-module-header">
              <div className="param-module-icon temp"><Icon.Thermometer size={16} color="#FA8C16" /></div>
              <span className="param-module-label">温度（随机性）</span>
            </div>
            <div className="param-slider-row">
              <div className="param-slider">
                <Slider
                  min={0} max={2} step={0.1}
                  value={temperature}
                  onChange={onTempChange}
                  disabled={loading}
                  tooltip={{ formatter: (v) => v.toFixed(1) }}
                />
              </div>
              <InputNumber
                className="param-number-input"
                min={0} max={2} step={0.1}
                value={parseFloat(temperature.toFixed(1))}
                onChange={(v) => onTempChange(v ?? 0.3)}
                disabled={loading}
                size="small"
                precision={1}
              />
            </div>
            <p className="param-hint">
              控制回答的发散程度：越低越严谨稳定，越高越有创意（0-2）。
              命中历史问答缓存时直接返回缓存回答，本参数不生效。
            </p>
          </div>

          {/* Web Search Module */}
          <div className="param-module">
            <div className="param-module-header">
              <div className="param-module-icon search"><Icon.Globe size={16} color="#1890FF" /></div>
              <span className="param-module-label">联网搜索</span>
            </div>
            <div className="param-switch-row">
              <Switch
                checked={useWebSearch}
                onChange={onSearchChange}
                disabled={loading}
              />
              <span className="param-switch-label">{useWebSearch ? '已开启' : '已关闭'}</span>
            </div>
            <p className="param-hint">
              默认关闭：仅检索本地知识库（问答缓存 + 离线知识库），未查到内容时返回「信息不足」；
              开启后才允许联网搜索补充资料。
            </p>
          </div>

          {/* Web Pages Module */}
          <div className={'param-module' + (!useWebSearch ? ' disabled' : '')}>
            <div className="param-module-header">
              <div className="param-module-icon pages"><Icon.FileText size={16} color={PRIMARY} /></div>
              <span className="param-module-label">搜索网页数量</span>
            </div>
            <div className="param-slider-row">
              <div className="param-slider">
                <Slider
                  min={1} max={20} step={1}
                  value={webTopN}
                  onChange={onTopNChange}
                  disabled={loading || !useWebSearch}
                  tooltip={{ formatter: (v) => String(v) }}
                />
              </div>
              <InputNumber
                className="param-number-input"
                min={1} max={20} step={1}
                value={webTopN}
                onChange={(v) => onTopNChange(v ?? 5)}
                disabled={loading || !useWebSearch}
                size="small"
              />
            </div>
            <p className="param-hint">
              联网搜索时抓取参考的网页数量（1-20）。数量越多资料越全、耗时越长；
              仅在「联网搜索」开启时生效。
            </p>
          </div>
        </div>
    </div>
  );
}

// ===== Main App Component =====
function App() {
  // --- State ---
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(SK_USER)); } catch { return null; }
  });
  const [authOpen, setAuthOpen] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [currentId, setCurrentId] = useState(() => localStorage.getItem(SK_CURRENT) || null);
  // 会话消息以服务端为权威数据源：登录后按需拉取（不再读 localStorage）
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [temperature, setTemperature] = useState(0.3);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [webTopN, setWebTopN] = useState(5);
  const [leftCollapsed, setLeftCollapsed] = useState(() => localStorage.getItem(SK_LEFT) === 'true');
  const [rightCollapsed, setRightCollapsed] = useState(() => localStorage.getItem(SK_RIGHT) === 'true');

  const controllerRef = useRef(null);
  const scrollRef = useRef(null);
  const scrollAnchorRef = useRef(null);
  const messagesRef = useRef(messages); // always holds latest messages for save-on-complete

  // Keep messagesRef in sync (passive, does not trigger re-render)
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  // --- Session restore: 校验 token（/auth/me）→ 拉取该用户会话历史 ---
  useEffect(() => {
    (async () => {
      if (!localStorage.getItem(SK_TOKEN)) return;
      try {
        const me = await fetchMe();
        const info = { username: me.username, role: me.role, uid: me.uid };
        setUser(info);
        localStorage.setItem(SK_USER, JSON.stringify(info));
        const convs = await refreshConversations();
        const id = localStorage.getItem(SK_CURRENT);
        const conv = id ? convs.find(c => String(c.id) === String(id)) : null;
        if (conv) {
          setCurrentId(conv.id);
          const full = await getConversationApi(conv.id);
          const msgs = normalizeMessages(full.messages || []);
          setMessages(msgs);
          messagesRef.current = msgs;
        } else {
          localStorage.removeItem(SK_CURRENT);
        }
      } catch (e) {
        if (e.status === 401) logoutLocal();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Load health defaults ---
  useEffect(() => {
    fetchHealth().then(health => {
      if (health) {
        setTemperature(health.llm_temperature ?? 0.3);
        setWebTopN(health.web_top_n ?? 5);
      }
    });
    const s = loadSettings();
    if (s.temperature !== undefined) setTemperature(s.temperature);
    if (s.useWebSearch !== undefined) setUseWebSearch(s.useWebSearch);
    if (s.webTopN !== undefined) setWebTopN(s.webTopN);
  }, []);

  // --- Persist settings ---
  useEffect(() => { saveSettings({ temperature, useWebSearch, webTopN }); }, [temperature, useWebSearch, webTopN]);
  useEffect(() => { localStorage.setItem(SK_LEFT, String(leftCollapsed)); }, [leftCollapsed]);
  useEffect(() => { localStorage.setItem(SK_RIGHT, String(rightCollapsed)); }, [rightCollapsed]);

  // --- 账户：登录 / 注册 / 登出 / 会话管理（必须声明在引用它们的 useCallback 之前，避免 TDZ）---
  const logoutLocal = useCallback(() => {
    localStorage.removeItem(SK_TOKEN);
    localStorage.removeItem(SK_USER);
    localStorage.removeItem(SK_CURRENT);
    setUser(null);
    setConversations([]);
    setCurrentId(null);
    setMessages([]);
    messagesRef.current = [];
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      const convs = await listConversationsApi();
      const normalized = convs.map(c => ({
        id: String(c.id),
        title: c.title || '新对话',
        createdAt: c.created_at,
        updatedAt: c.updated_at,
        messages: [],
      }));
      setConversations(normalized);
      return normalized;
    } catch (e) {
      if (e.status === 401) { logoutLocal(); setAuthOpen(true); }
      else message.error('会话列表加载失败：' + e.message);
      return [];
    }
  }, [logoutLocal]);

  const applyAuth = useCallback((data) => {
    const info = { username: data.username, role: data.role, uid: data.uid };
    localStorage.setItem(SK_TOKEN, data.token);
    localStorage.setItem(SK_USER, JSON.stringify(info));
    setUser(info);
    setAuthOpen(false);
    message.success((info.role === 'admin' ? '欢迎回来，管理员 ' : '欢迎，') + info.username);
    refreshConversations();
  }, [refreshConversations]);

  const handleLogin = useCallback(async (username, password) => {
    applyAuth(await authApi('login', username, password));
  }, [applyAuth]);

  const handleRegister = useCallback(async (username, password) => {
    applyAuth(await authApi('register', username, password));
  }, [applyAuth]);

  const handleLogout = useCallback(() => {
    logoutLocal();
    message.success('已退出登录');
  }, [logoutLocal]);

  // --- 删除会话（同步删 MySQL）---
  const handleDeleteConv = useCallback(async (id) => {
    if (!window.confirm('确认删除该对话？删除后不可恢复。')) return;
    try {
      await deleteConversationApi(id);
    } catch (e) {
      if (e.status === 401) { logoutLocal(); setAuthOpen(true); }
      else message.error('删除失败：' + e.message);
      return;
    }
    setConversations(prev => prev.filter(c => String(c.id) !== String(id)));
    if (String(currentId) === String(id)) {
      setCurrentId(null);
      setMessages([]);
      messagesRef.current = [];
      localStorage.removeItem(SK_CURRENT);
    }
    message.success('已删除该对话');
  }, [currentId, logoutLocal]);

  // --- Save conversation when loading completes (NOT on every delta — avoids cascade) ---
  // 保存 = 写服务端（PUT /chat/conversations/{id}）+ 同步本地列表；失败提示、401 弹登录
  const saveCurrentConv = useCallback(async () => {
    const id = currentId;
    if (!id) return;
    const msgs = messagesRef.current;
    if (!msgs || msgs.length === 0) return;
    const firstUser = msgs.find(m => m.role === 'user');
    const title = ((firstUser && firstUser.content) || '新对话').slice(0, 30);
    setConversations(prev => prev.map(c =>
      String(c.id) === String(id)
        ? { ...c, messages: msgs, updatedAt: new Date().toISOString(), title }
        : c
    ));
    try {
      await saveConversationApi(id, title, msgs);
    } catch (e) {
      if (e.status === 401) { logoutLocal(); setAuthOpen(true); }
      else message.error('保存对话失败：' + e.message);
    }
  }, [currentId, logoutLocal]);

  // Trigger save when loading turns false
  useEffect(() => {
    if (!loading) saveCurrentConv();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  // --- Auto-scroll to bottom ---
  useEffect(() => {
    if (scrollAnchorRef.current) {
      scrollAnchorRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // --- Send message（需登录：未登录先弹「账户」登录框）---
  const handleSend = useCallback(async (textOverride) => {
    const text = (textOverride ?? input).trim();
    if (!text || loading) return;
    if (!user) {
      message.info('请先登录后再提问');
      setAuthOpen(true);
      return;
    }

    const userMsg = { role: 'user', content: text, time: new Date().toISOString() };
    let convId = currentId;
    if (!convId) {
      // 服务端建会话（首问即建），本地立即插入列表
      try {
        const conv = await createConversationApi(text.slice(0, 30), [userMsg]);
        convId = String(conv.id);
        const newConv = {
          id: convId,
          title: text.slice(0, 30),
          messages: [userMsg],
          createdAt: conv.created_at,
          updatedAt: conv.updated_at,
        };
        setConversations(prev => [newConv, ...prev]);
        setCurrentId(convId);
        localStorage.setItem(SK_CURRENT, convId);
      } catch (e) {
        if (e.status === 401) { logoutLocal(); setAuthOpen(true); }
        else message.error('创建对话失败：' + e.message);
        return;
      }
    }

    setInput('');
    // 追问业务：当前问题之前的完整历史快照（不含本轮 userMsg/assistantMsg，服务端据此判定追问并改写）
    const historyMsgs = messagesRef.current;
    const assistantMsg = { role: 'assistant', content: '', sources: [], status: 'loading', statusText: '正在思考…', cached: false, feedback: null };
    const initMsgs = [...messagesRef.current, userMsg, assistantMsg];
    messagesRef.current = initMsgs;
    setMessages(initMsgs);
    setLoading(true);

    // Phase timing trackers
    const tStart = Date.now();
    let tCacheHit = null;     // timestamp when cache-hit status arrived
    let tSearchStart = null;  // timestamp when web search phase started
    let tSearchEnd = null;    // timestamp when web search phase ended (generate status arrived)
    let tGenerateStart = null; // timestamp when generation started

    let answerText = '';
    let sources = [];
    let cached = false;
    let direct = false;
    let hallucinationRisk = null;

    await askStream(text, { temperature, useWebSearch, webTopN }, historyMsgs, {
      onStatus: (msg) => {
        cached = cached || msg.includes('缓存');
        // Track phase timestamps
        if (msg.includes('命中') && msg.includes('缓存')) {
          tCacheHit = Date.now();
        }
        if (msg.includes('联网搜索') || msg.includes('抓取网页') || msg.includes('清洗') || msg.includes('检索临时')) {
          if (!tSearchStart) tSearchStart = Date.now();
        }
        if (msg.includes('正在生成回答')) {
          tGenerateStart = Date.now();
          if (tSearchStart && !tSearchEnd) tSearchEnd = Date.now();
        }
        setMessages(prev => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = { ...last, statusText: msg };
          }
          return next;
        });
      },
      onDelta: (delta) => {
        answerText += delta;
        setMessages(prev => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = { ...last, content: answerText, status: 'streaming' };
          }
          return next;
        });
      },
      onDone: (data) => {
        answerText = data.answer || answerText;
        sources = data.sources || [];
        cached = data.cached || cached;
        direct = data.direct || false;
        hallucinationRisk = data.hallucination_risk || null;

        // Calculate phase durations
        const tEnd = Date.now();
        const totalTime = tEnd - tStart;
        let cacheTime = null;   // cache hit: time from request start to done
        let searchTime = null;  // web search: time from search start to search end
        let genTime = null;     // generation: time from generate start to done
        if (cached && tCacheHit) {
          cacheTime = tEnd - tStart; // entire round-trip for cache hit
        }
        if (tSearchStart && tSearchEnd) {
          searchTime = tSearchEnd - tSearchStart;
        }
        if (tGenerateStart) {
          genTime = tEnd - tGenerateStart;
        }

        setMessages(prev => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = {
              ...last,
              content: answerText,
              sources,
              status: 'done',
              cached,
              direct,
              hallucinationRisk,
              feedback: null,
              totalTime,
              cacheTime,
              searchTime,
              genTime,
            };
          }
          return next;
        });
      },
      onError: (err) => {
        if (err.code === 'UNAUTHORIZED') {
          message.warning('登录已过期，请重新登录');
          logoutLocal(); // 清掉失效会话（token+user），弹窗回登录表单，不再残留已登录账户信息
          setAuthOpen(true);
        }
        const errMsg = ERROR_MSG[err.code] || err.message || '未知错误';
        setMessages(prev => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = {
              ...last,
              content: errMsg,
              status: 'error',
            };
          }
          return next;
        });
      },
      onAborted: () => {
        const tEnd = Date.now();
        setMessages(prev => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = {
              ...last,
              content: answerText || '(已停止生成)',
              status: 'done',
              sources,
              cached,
              partial: true,
              totalTime: tEnd - tStart,
              cacheTime: cached ? (tEnd - tStart) : null,
              searchTime: (tSearchStart && tSearchEnd) ? (tSearchEnd - tSearchStart) : null,
              genTime: tGenerateStart ? (tEnd - tGenerateStart) : null,
            };
          }
          return next;
        });
      },
    }, controllerRef);

    setLoading(false);
  }, [currentId, loading, temperature, useWebSearch, webTopN, input, user, logoutLocal]);

  // --- Stop generation ---
  const handleStop = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
    }
  }, []);

  // --- Select conversation (save current, load target；消息不足时从服务端拉取) ---
  const handleSelectConv = useCallback((id) => {
    if (loading) handleStop();
    // Save current conversation before switching
    saveCurrentConv();
    const conv = conversations.find(c => String(c.id) === String(id));
    setCurrentId(id);
    localStorage.setItem(SK_CURRENT, id);
    if (conv && conv.messages && conv.messages.length > 0) {
      setMessages([...conv.messages]);
      messagesRef.current = [...conv.messages];
    } else {
      setMessages([]);
      messagesRef.current = [];
      getConversationApi(id).then(full => {
        const msgs = normalizeMessages(full.messages || []);
        setMessages(msgs);
        messagesRef.current = msgs;
        setConversations(prev => prev.map(c =>
          String(c.id) === String(id) ? { ...c, messages: msgs, title: full.title || c.title } : c
        ));
      }).catch(e => {
        if (e.status === 401) { logoutLocal(); setAuthOpen(true); }
        else message.error('会话加载失败：' + e.message);
      });
    }
  }, [loading, conversations, saveCurrentConv, logoutLocal]);

  // --- New conversation ---
  const handleNew = useCallback(() => {
    if (loading) handleStop();
    saveCurrentConv();
    setCurrentId(null);
    setMessages([]);
    messagesRef.current = [];
    setInput('');
    localStorage.removeItem(SK_CURRENT);
  }, [loading, saveCurrentConv]);

  // --- Feedback ---
  const handleFeedback = useCallback((msgIndex, type) => {
    const msg = messagesRef.current[msgIndex];
    if (!msg || msg.role !== 'assistant') return;
    const question = messagesRef.current[msgIndex - 1]?.content || '';
    submitFeedbackApi({
      question,
      answer: msg.content,
      sources: msg.sources || [],
      feedback_type: type,
      cached: msg.cached || false,
      direct: msg.direct || false,
      hallucination_risk: msg.hallucinationRisk || null,
    });
    setMessages(prev => {
      const next = [...prev];
      next[msgIndex] = { ...next[msgIndex], feedback: type };
      return next;
    });
    message.success(type === 'good' ? '感谢反馈！' : '已记录，我们会改进');
  }, []);

  // --- Retry ---
  const handleRetry = useCallback((msgIndex) => {
    const question = messagesRef.current[msgIndex - 1]?.content;
    if (!question) return;
    // Remove error + user message, sync ref, then re-send
    const newMsgs = messagesRef.current.slice(0, msgIndex - 1);
    messagesRef.current = newMsgs;
    setMessages(newMsgs);
    setTimeout(() => handleSend(question), 50);
  }, [handleSend]);

  // --- Example click ---
  const handleExample = useCallback((text) => {
    handleSend(text);
  }, [handleSend]);

  // --- Render ---
  const theme = {
    token: {
      colorPrimary: PRIMARY,
      borderRadius: 8,
      fontFamily: 'inherit',
      fontSize: 14,
    },
  };

  return (
    <ConfigProvider theme={theme}>
      <div className="app-layout">
        <LeftSidebar
          collapsed={leftCollapsed}
          onToggle={() => setLeftCollapsed(!leftCollapsed)}
          conversations={conversations}
          currentId={currentId}
          onSelect={handleSelectConv}
          onNew={handleNew}
          onDelete={handleDeleteConv}
          onAccount={() => setAuthOpen(true)}
          user={user}
        />
        <div className="chat-area">
          <div className="chat-topbar">
            <span className="chat-topbar-title">WebRAG 智能问答</span>
            <span className="chat-topbar-sub">检索增强生成 · 逐段引用溯源</span>
            {rightCollapsed && (
              <button
                className="topbar-expand-btn"
                onClick={() => setRightCollapsed(false)}
                title="展开参数面板"
              >
                <Icon.Settings size={16} />
                <span className="topbar-expand-label">参数</span>
              </button>
            )}
          </div>
          {messages.length === 0 ? (
            <EmptyGuide onExample={handleExample} />
          ) : (
            <div className="message-list" ref={scrollRef}>
              <div className="message-list-inner">
                {messages.map((msg, i) => (
                  <MessageBubble
                    key={i}
                    msg={msg}
                    msgIndex={i}
                    onFeedback={handleFeedback}
                    onRetry={handleRetry}
                  />
                ))}
                <div ref={scrollAnchorRef} />
              </div>
            </div>
          )}
          <InputArea
            value={input}
            onChange={setInput}
            onSend={handleSend}
            loading={loading}
            onStop={handleStop}
            useWebSearch={useWebSearch}
          />
        </div>
        <RightSidebar
          collapsed={rightCollapsed}
          onToggle={() => setRightCollapsed(!rightCollapsed)}
          temperature={temperature}
          onTempChange={setTemperature}
          useWebSearch={useWebSearch}
          onSearchChange={setUseWebSearch}
          webTopN={webTopN}
          onTopNChange={setWebTopN}
          loading={loading}
        />
      </div>
      <AuthModal
        open={authOpen}
        user={user}
        onClose={() => setAuthOpen(false)}
        onLogin={handleLogin}
        onRegister={handleRegister}
        onLogout={handleLogout}
      />
    </ConfigProvider>
  );
}

// ===== Mount =====
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);

// Hide loading screen once React has mounted
const _loadingEl = document.getElementById('app-loading');
if (_loadingEl) _loadingEl.style.display = 'none';
