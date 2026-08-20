const { useEffect, useRef, useState } = React;
const html = htm.bind(React.createElement);

const API = {
  async request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  },
  config: () => API.request("/api/config"),
  saveConfig: (config) => API.request("/api/config", { method: "PUT", body: JSON.stringify(config) }),
  status: () => API.request("/api/bot/status"),
  start: () => API.request("/api/bot/start", { method: "POST" }),
  stop: () => API.request("/api/bot/stop", { method: "POST" }),
  input: (value) => API.request("/api/bot/input", { method: "POST", body: JSON.stringify({ value }) }),
};

function Icon({ name }) {
  const icons = {
    control: "M8 5v14l11-7z",
    settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.4-3.5a7.3 7.3 0 0 0-.1-1l2-1.6-2-3.4-2.5 1a8 8 0 0 0-1.8-1L14.6 3h-4L10 6a8 8 0 0 0-1.8 1L5.7 6 3.8 9.4l2 1.6a7.3 7.3 0 0 0 0 2l-2 1.6L5.7 18l2.5-1a8 8 0 0 0 1.8 1l.6 3h4l.5-3a8 8 0 0 0 1.8-1l2.5 1 2-3.4-2-1.6a7.3 7.3 0 0 0 0-1z",
    sources: "M16 11c1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3 1.3 3 3 3zM8 13c2.2 0 4-1.8 4-4S10.2 5 8 5 4 6.8 4 9s1.8 4 4 4zm8 0c-2 0-6 1-6 3v3h12v-3c0-2-4-3-6-3zM8 15c-2.7 0-8 1.3-8 4v2h8v-5c0-.4.1-.7.3-1H8z",
    save: "M5 3h12l2 2v16H5V3zm3 2v5h8V5H8zm4 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    plus: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z",
    trash: "M7 6h10l-1 15H8L7 6zm2-3h6l1 2H8l1-2z",
  };
  return html`<svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d=${icons[name]} /></svg>`;
}

function Field({ label, hint, children, wide = false }) {
  return html`<label className=${`field ${wide ? "field-wide" : ""}`}>
    <span className="field-label">${label}</span>${children}${hint && html`<small>${hint}</small>`}
  </label>`;
}

function Section({ title, description, children }) {
  return html`<section className="card settings-card">
    <div className="card-heading"><div><h2>${title}</h2><p>${description}</p></div></div>
    <div className="form-grid">${children}</div>
  </section>`;
}

function Settings({ config, setConfig }) {
  const set = (section, field, value) => setConfig((current) => ({
    ...current,
    [section]: { ...current[section], [field]: value },
  }));
  const secretFocus = (section, field, value) => {
    if (value === "••••••••") set(section, field, "");
  };
  return html`<div className="stack">
    <${Section} title="Telegram" description="Аккаунт, сессия и канал для готовых публикаций">
      <${Field} label="API ID"><input type="number" value=${config.telegram.api_id || ""} onChange=${e => set("telegram", "api_id", Number(e.target.value))} /></${Field}>
      <${Field} label="API Hash"><input type="password" value=${config.telegram.api_hash} onFocus=${e => secretFocus("telegram", "api_hash", e.target.value)} onChange=${e => set("telegram", "api_hash", e.target.value)} /></${Field}>
      <${Field} label="Имя сессии"><input value=${config.telegram.session} onChange=${e => set("telegram", "session", e.target.value)} /></${Field}>
      <${Field} label="Целевой канал" hint="Например, @my_channel. Оставьте пустым для локального сохранения."><input value=${config.telegram.destination || ""} onChange=${e => set("telegram", "destination", e.target.value || null)} /></${Field}>
    </${Section}>
    <${Section} title="LLM-провайдер" description="OpenAI-совместимый API: GigaChat, DeepSeek или другой сервис">
      <${Field} label="API / Access Token"><input type="password" value=${config.llm.api_key} onFocus=${e => secretFocus("llm", "api_key", e.target.value)} onChange=${e => set("llm", "api_key", e.target.value)} /></${Field}>
      <${Field} label="Base URL"><input value=${config.llm.base_url || ""} onChange=${e => set("llm", "base_url", e.target.value || null)} /></${Field}>
      <${Field} label="Текстовая модель"><input value=${config.llm.model} onChange=${e => set("llm", "model", e.target.value)} /></${Field}>
      <${Field} label="Vision-модель" hint="Необязательно"><input value=${config.llm.vision_model || ""} onChange=${e => set("llm", "vision_model", e.target.value || null)} /></${Field}>
      <${Field} label="Температура"><input type="number" min="0" max="2" step="0.1" value=${config.llm.temperature} onChange=${e => set("llm", "temperature", Number(e.target.value))} /></${Field}>
      <${Field} label="Максимум токенов"><input type="number" min="1" value=${config.llm.max_tokens} onChange=${e => set("llm", "max_tokens", Number(e.target.value))} /></${Field}>
    </${Section}>
    <${Section} title="Анализ и фильтрация" description="Проверка рекламы и смысловых дублей перед рерайтом">
      <label className="switch-row setting-switch"><span><strong>Предварительный анализ включён</strong><small>Реклама и полные дубли не будут опубликованы</small></span><input type="checkbox" checked=${config.analysis.enabled} onChange=${e => set("analysis", "enabled", e.target.checked)} /><i></i></label>
      <${Field} label="Глубина истории, часов" hint="Опубликованные посты за этот период передаются LLM для сравнения"><input type="number" min="1" step="1" disabled=${!config.analysis.enabled} value=${config.analysis.history_hours} onChange=${e => set("analysis", "history_hours", Number(e.target.value))} /></${Field}>
    </${Section}>
    <${Section} title="Хранилище" description="Локальные результаты, база обработанных постов и лимиты">
      <${Field} label="Файл состояния"><input value=${config.storage.database} onChange=${e => set("storage", "database", e.target.value)} /></${Field}>
      <${Field} label="Каталог результатов"><input value=${config.storage.output_dir} onChange=${e => set("storage", "output_dir", e.target.value)} /></${Field}>
      <${Field} label="Лимит вложений, МБ" hint="Суммарно на один пост или альбом"><input type="number" min="0.1" step="0.1" value=${config.storage.max_post_download_mb} onChange=${e => set("storage", "max_post_download_mb", Number(e.target.value))} /></${Field}>
    </${Section}>
  </div>`;
}

function Sources({ config, setConfig }) {
  const update = (index, field, value) => setConfig((current) => ({
    ...current,
    sources: current.sources.map((source, i) => i === index ? { ...source, [field]: value } : source),
  }));
  const add = () => setConfig((current) => ({
    ...current,
    sources: [...current.sources, { chat: "", enabled: true, prompt_addition: "" }],
  }));
  const remove = (index) => setConfig((current) => ({
    ...current,
    sources: current.sources.filter((_, i) => i !== index),
  }));
  return html`<div className="stack">
    <div className="page-intro"><div><h1>Источники</h1><p>Каналы и группы, за которыми следит мониторинг</p></div>
      <button className="button button-secondary" onClick=${add}><${Icon} name="plus" />Добавить источник</button>
    </div>
    ${config.sources.length === 0 && html`<div className="empty card"><div className="empty-symbol">＋</div><h2>Источников пока нет</h2><p>Добавьте Telegram-канал или группу, чтобы начать мониторинг.</p><button className="button button-primary" onClick=${add}>Добавить первый источник</button></div>`}
    <div className="source-grid">${config.sources.map((source, index) => html`
      <article className="card source-card" key=${index}>
        <div className="source-number">${String(index + 1).padStart(2, "0")}</div>
        <button className="icon-button danger" title="Удалить" onClick=${() => remove(index)}><${Icon} name="trash" /></button>
        <${Field} label="Ссылка или username" wide=${true}><input placeholder="https://t.me/channel" value=${source.chat} onChange=${e => update(index, "chat", e.target.value)} /></${Field}>
        <${Field} label="Дополнение к промпту" hint="Эти инструкции применяются только к данному источнику" wide=${true}><textarea rows="5" placeholder="Например: сохраняй деловой тон и все числовые данные" value=${source.prompt_addition || ""} onChange=${e => update(index, "prompt_addition", e.target.value)} /></${Field}>
        <label className="switch-row"><span><strong>Мониторинг включён</strong><small>Новые посты будут обрабатываться</small></span><input type="checkbox" checked=${source.enabled} onChange=${e => update(index, "enabled", e.target.checked)} /><i></i></label>
      </article>`)}
    </div>
  </div>`;
}

function Control({ status, setStatus, logs, terminalInput, setTerminalInput, notify }) {
  const terminalRef = useRef(null);
  const busy = useRef(false);
  useEffect(() => { if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight; }, [logs]);
  const action = async (kind) => {
    if (busy.current) return;
    busy.current = true;
    try {
      setStatus(kind === "start" ? await API.start() : await API.stop());
      notify(kind === "start" ? "Мониторинг запущен" : "Мониторинг остановлен", "success");
    } catch (error) { notify(error.message, "error"); }
    finally { busy.current = false; }
  };
  const send = async (event) => {
    event.preventDefault();
    if (!terminalInput) return;
    try { await API.input(terminalInput); setTerminalInput(""); }
    catch (error) { notify(error.message, "error"); }
  };
  return html`<div className="stack">
    <div className="hero card">
      <div><div className="eyebrow">Центр управления</div><h1>${status.running ? "Мониторинг активен" : "Мониторинг остановлен"}</h1><p>${status.running ? `Процесс #${status.pid} читает новые публикации` : "Настройте источники и запустите обработку одной кнопкой"}</p></div>
      <div className="hero-actions"><span className=${`status-pill ${status.running ? "online" : "offline"}`}><i></i>${status.running ? "Работает" : "Остановлен"}</span>
        ${status.running
          ? html`<button className="button button-stop" onClick=${() => action("stop")}>Остановить</button>`
          : html`<button className="button button-start" onClick=${() => action("start")}><span className="play">▶</span>Запустить</button>`}
      </div>
    </div>
    <section className="terminal-card card">
      <div className="terminal-bar"><div className="terminal-lights"><i></i><i></i><i></i></div><span>telegram-monitor</span><span className="terminal-state">${status.running ? "LIVE" : "IDLE"}</span></div>
      <div className="terminal" ref=${terminalRef}>${logs.length
        ? logs.map((line, index) => html`<div className=${line.includes("ERROR") ? "log-error" : line.includes("[web]") ? "log-system" : ""} key=${index}>${line}</div>`)
        : html`<div className="terminal-placeholder">Здесь появятся сообщения процесса после запуска…</div>`}</div>
      <form className="terminal-input" onSubmit=${send}><span>›</span><input disabled=${!status.running} value=${terminalInput} onChange=${e => setTerminalInput(e.target.value)} placeholder="Ввод для Telegram: телефон, код или пароль 2FA" autocomplete="off" /><button disabled=${!status.running}>Отправить</button></form>
    </section>
  </div>`;
}

function App() {
  const [tab, setTab] = useState("control");
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState({ running: false, pid: null });
  const [logs, setLogs] = useState([]);
  const [terminalInput, setTerminalInput] = useState("");
  const [toast, setToast] = useState(null);
  const [saving, setSaving] = useState(false);
  const notify = (message, type = "success") => { setToast({ message, type }); setTimeout(() => setToast(null), 4200); };

  useEffect(() => {
    Promise.all([API.config(), API.status()]).then(([loadedConfig, loadedStatus]) => { setConfig(loadedConfig); setStatus(loadedStatus); }).catch(error => notify(error.message, "error"));
    const interval = setInterval(() => API.status().then(setStatus).catch(() => {}), 3000);
    let socket;
    let reconnect;
    const connect = () => {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/logs`);
      socket.onopen = () => setLogs([]);
      socket.onmessage = event => setLogs(current => [...current.slice(-1998), event.data]);
      socket.onclose = () => { reconnect = setTimeout(connect, 1500); };
    };
    connect();
    return () => { clearInterval(interval); clearTimeout(reconnect); if (socket) { socket.onclose = null; socket.close(); } };
  }, []);

  const save = async () => {
    if (status.running) return notify("Сначала остановите мониторинг", "error");
    setSaving(true);
    try { setConfig(await API.saveConfig(config)); notify("Настройки сохранены"); }
    catch (error) { notify(error.message, "error"); }
    finally { setSaving(false); }
  };
  if (!config) return html`<div className="loader"><div className="brand-mark">TP</div><span>Загрузка панели…</span></div>`;
  const tabs = [{ id: "control", label: "Управление" }, { id: "settings", label: "Настройки" }, { id: "sources", label: "Источники", count: config.sources.length }];
  return html`<div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">TP</div><div><strong>Post Parser</strong><span>Telegram monitor</span></div></div>
      <nav>${tabs.map(item => html`<button className=${tab === item.id ? "active" : ""} onClick=${() => setTab(item.id)} key=${item.id}><${Icon} name=${item.id} /><span>${item.label}</span>${item.count !== undefined && html`<b>${item.count}</b>`}</button>`)}</nav>
      <div className="sidebar-foot"><span className=${status.running ? "dot-online" : ""}></span><div><strong>${status.running ? "Сервис активен" : "Сервис остановлен"}</strong><small>${status.running ? `PID ${status.pid}` : "Готов к запуску"}</small></div></div>
    </aside>
    <main><header className="topbar"><div className="mobile-brand">Post Parser</div><div className="topbar-title">${tabs.find(item => item.id === tab).label}</div>${tab !== "control" && html`<button className="button button-primary" disabled=${saving || status.running} onClick=${save}><${Icon} name="save" />${saving ? "Сохранение…" : "Сохранить"}</button>`}</header>
      <div className="content">${tab === "control" && html`<${Control} status=${status} setStatus=${setStatus} logs=${logs} terminalInput=${terminalInput} setTerminalInput=${setTerminalInput} notify=${notify} />`}${tab === "settings" && html`<${Settings} config=${config} setConfig=${setConfig} />`}${tab === "sources" && html`<${Sources} config=${config} setConfig=${setConfig} />`}</div>
    </main>
    ${toast && html`<div className=${`toast ${toast.type}`}>${toast.message}</div>`}
  </div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
