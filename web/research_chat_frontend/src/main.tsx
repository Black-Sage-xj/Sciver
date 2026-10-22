import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Children, isValidElement, type ReactNode } from 'react';
import { ActionBarPrimitive } from '@assistant-ui/react';
import { createRoot } from 'react-dom/client';
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessagePartText,
  type AppendMessage,
  type ThreadMessageLike,
} from '@assistant-ui/react';
import ReactMarkdown from 'react-markdown';
import { normalizeMathDelimiters, rehypePlugins, remarkPlugins } from './markdown.ts';
import 'katex/dist/katex.min.css';
import { ArrowDown, ChevronDown, LoaderCircle, RotateCcw, Send, Square } from 'lucide-react';
import './styles.css';

type ChatRole = 'assistant' | 'user' | 'system';
type ChatMessage = { id: string; role: ChatRole; content: string; created_at?: string };
type ActiveRun = { id?: string; status?: string; content?: string; error_type?: string } | null;
type ChatState = {
  id: string;
  mode?: string;
  status?: string;
  cycle_id?: string;
  trigger_event?: string;
  model?: string;
  reasoning_effort?: string;
  available_models?: string[];
  available_reasoning_efforts?: string[];
  messages: ChatMessage[];
  active_run?: ActiveRun;
  streaming?: boolean;
  stream_status?: string;
  stream_text?: string;
  pending_action?: Record<string, unknown> | null;
};

type ProgressState = { status?: string; streaming?: boolean; text?: string; next_offset?: number; reset?: boolean; error?: string };
type ChatSettings = Pick<ChatState, 'model' | 'reasoning_effort' | 'available_models' | 'available_reasoning_efforts'>;
type PendingImage = { name: string; media_type: string; data: string };

const streamMessageId = '__tm_research_chat_stream__';
const fallbackModels = ['gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna'];
const fallbackEfforts = ['low', 'medium', 'high', 'xhigh'];
const effortLabels: Record<string, string> = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'XHigh' };
const isTerminal = (status?: string) => ['complete', 'stopped', 'error', 'interrupted'].includes(String(status || ''));
const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

function messageText(message: AppendMessage): string {
  const content: unknown = (message as { content: unknown }).content;
  if (typeof content === 'string') return content.trim();
  if (!Array.isArray(content)) return '';
  return content.map((part: unknown) => {
    if (!part || typeof part !== 'object') return '';
    const value = part as { type?: unknown; text?: unknown };
    return value.type === 'text' && typeof value.text === 'string' ? value.text : '';
  }).join('\n').trim();
}

function convertMessage(message: ChatMessage): ThreadMessageLike {
  const content = message.role === 'system' ? `⚠️ ${message.content}` : message.content;
  return {
    id: message.id,
    role: message.role === 'user' ? 'user' : 'assistant',
    content: [{ type: 'text', text: content }],
    createdAt: message.created_at ? new Date(message.created_at) : undefined,
  };
}

function codeText(children: ReactNode): string {
  return Children.toArray(children).map((child) => {
    if (typeof child === 'string' || typeof child === 'number') return String(child);
    if (isValidElement<{ children?: ReactNode }>(child)) return codeText(child.props.children);
    return '';
  }).join('');
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codeText(children));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch { /* The message-level copy action remains available if clipboard access is blocked. */ }
  };
  return (
    <div className="tm-aui-code-shell">
      <button type="button" className="tm-aui-code-copy" onClick={() => void copy()}>{copied ? '已复制' : '复制代码'}</button>
      <pre>{children}</pre>
    </div>
  );
}

function MarkdownPart() {
  const part = useMessagePartText();
  return <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={{ pre: CodeBlock }}>{normalizeMathDelimiters(part.text)}</ReactMarkdown>;
}

function ChatMessageView() {
  const page = document.querySelector<HTMLElement>('.tm-research-chat');
  const isSupervisor = page?.dataset.mode === 'supervisor';
  const isMigration = page?.dataset.sessionMode === 'migration';
  const canEdit = !isSupervisor && !isMigration;
  const canReload = !isSupervisor && !isMigration;
  return (
    <MessagePrimitive.Root className="tm-aui-message">
      <MessagePrimitive.If user>
        <div className="tm-aui-user-row">
          <div className="tm-aui-user-bubble">
            <MessagePrimitive.Parts components={{ Text: MarkdownPart }} />
            <ActionBarPrimitive.Root className="tm-aui-message-actions" hideWhenRunning autohide="never">
              <ActionBarPrimitive.Copy className="tm-aui-message-action">复制</ActionBarPrimitive.Copy>
              {canEdit && <ActionBarPrimitive.Edit className="tm-aui-message-action">编辑并重发</ActionBarPrimitive.Edit>}
            </ActionBarPrimitive.Root>
          </div>
        </div>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <div className="tm-aui-assistant-row">
          <div className="tm-aui-assistant-label">Sciver</div>
          <div className="tm-aui-assistant-content">
            <MessagePrimitive.Parts components={{ Text: MarkdownPart }} />
          </div>
          <ActionBarPrimitive.Root className="tm-aui-message-actions" hideWhenRunning autohide="never">
            <ActionBarPrimitive.Copy className="tm-aui-message-action">复制回答</ActionBarPrimitive.Copy>
            {canReload && <ActionBarPrimitive.Reload className="tm-aui-message-action">重新生成</ActionBarPrimitive.Reload>}
          </ActionBarPrimitive.Root>
        </div>
      </MessagePrimitive.If>
    </MessagePrimitive.Root>
  );
}

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((data as { error?: string }).error || `请求失败 (${response.status})`);
  return data as T;
}

async function postJSON<T>(url: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-TM-Operator-Intent': '1' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((data as { error?: string }).error || `请求失败 (${response.status})`);
  return data as T;
}

function normalizeMessages(messages: ChatMessage[] | undefined): ChatMessage[] {
  return (messages || []).map((message, index) => ({
    id: message.id || `stored-${index}`,
    role: message.role || 'assistant',
    content: String(message.content ?? ''),
    created_at: message.created_at,
  }));
}

function App({ sessionId, mode }: { sessionId: string; mode: string }) {
  const isSupervisor = mode === 'supervisor';
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeRun, setActiveRun] = useState<ActiveRun>(null);
  const [pendingAction, setPendingAction] = useState(false);
  const [selectedModel, setSelectedModel] = useState('gpt-6.1-sol');
  const [selectedEffort, setSelectedEffort] = useState('medium');
  const [availableModels, setAvailableModels] = useState<string[]>(fallbackModels);
  const [availableEfforts, setAvailableEfforts] = useState<string[]>(fallbackEfforts);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [sessionStatus, setSessionStatus] = useState('active');
  const [cycleId, setCycleId] = useState('');
  const [triggerEvent, setTriggerEvent] = useState('');
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const base = isSupervisor
    ? `/api/supervisor-session/${encodeURIComponent(sessionId)}`
    : `/api/research-chat/${encodeURIComponent(sessionId)}`;

  const fetchState = useCallback(async () => {
    if (isSupervisor) return getJSON<ChatState>(`${base}/state`);
    const [state, settings] = await Promise.all([
      getJSON<ChatState>(`${base}/state`),
      getJSON<ChatSettings>(`${base}/settings`),
    ]);
    return { ...state, ...settings };
  }, [base, isSupervisor]);
  const applyState = useCallback((state: ChatState) => {
    setMessages(normalizeMessages(state.messages));
    setSelectedModel(state.model || 'gpt-6.1-sol');
    setSelectedEffort(state.reasoning_effort || 'medium');
    setAvailableModels(state.available_models?.length ? state.available_models : fallbackModels);
    setAvailableEfforts(state.available_reasoning_efforts?.length ? state.available_reasoning_efforts : fallbackEfforts);
    setActiveRun(state.active_run || null);
    setPendingAction(Boolean(state.pending_action));
    setSessionStatus(state.status || 'active');
    setCycleId(state.cycle_id || '');
    setTriggerEvent(state.trigger_event || '');
  }, []);

  const pollRun = useCallback(async () => {
    let consecutiveNetworkFailures = 0;
    if (isSupervisor) {
      for (let attempt = 0; attempt < 900; attempt += 1) {
        if (document.hidden) { await sleep(1000); continue; }
        let progress: ProgressState;
        try {
          progress = await getJSON<ProgressState>(`${base}/progress`);
          consecutiveNetworkFailures = 0;
        } catch (reason) {
          consecutiveNetworkFailures += 1;
          if (consecutiveNetworkFailures > 60) throw reason;
          await sleep(1000);
          continue;
        }
        if (!progress.streaming) {
          applyState(await fetchState());
          setIsRunning(false);
          if (progress.error) setError(String(progress.error));
          return;
        }
        await sleep(1000);
      }
      setIsRunning(false);
      setError('Supervisor 回复仍未完成。已保留会话记录；刷新页面可重新查看状态。');
      return;
    }
    let streamText = '';
    let streamOffset = 0;
    for (let attempt = 0; attempt < 900; attempt += 1) {
      if (document.hidden) { await sleep(1000); continue; }
      let progress: ProgressState;
      try {
        progress = await getJSON<ProgressState>(`${base}/progress?offset=${streamOffset}`);
        consecutiveNetworkFailures = 0;
      } catch (reason) {
        consecutiveNetworkFailures += 1;
        if (consecutiveNetworkFailures > 60) throw reason;
        // A web-service restart must not instantly discard the user's in-flight UI state.
        await sleep(1000);
        continue;
      }
      const delta = String(progress.text || '');
      if (progress.reset) streamText = delta;
      else streamText += delta;
      if (typeof progress.next_offset === 'number' && Number.isFinite(progress.next_offset)) {
        streamOffset = Math.max(0, progress.next_offset);
      } else {
        streamOffset += delta.length;
      }
      if (streamText) {
        setMessages((previous) => {
          const withoutStream = previous.filter((message) => message.id !== streamMessageId);
          return [...withoutStream, {
            id: streamMessageId,
            role: 'assistant',
            content: streamText,
            created_at: new Date().toISOString(),
          }];
        });
      }
      if (isTerminal(progress.status)) {
        const state = await fetchState();
        applyState(state);
        setIsRunning(false);
        if (progress.status === 'error') setError('回复生成失败。查看下方错误提示后，可以重试上一条消息。');
        if (progress.status === 'interrupted') setError('回复在服务重启时中断，问题已保存；可以重试。');
        if (state.pending_action) window.location.reload();
        return;
      }
      await sleep(350);
    }
    setIsRunning(false);
    setError('等待回复超时。当前问题已保存；刷新页面后可查看状态或重试。');
  }, [applyState, base, fetchState, isSupervisor]);

  useEffect(() => {
    let cancelled = false;
    fetchState().then((state) => {
      if (cancelled) return;
      applyState(state);
      setLoading(false);
      if (state.streaming) {
        setIsRunning(true);
        void pollRun().catch((reason: unknown) => {
          if (!cancelled) {
            setIsRunning(false);
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        });
      }
      if (state.stream_text) {
        setMessages((previous) => [...previous, {
          id: streamMessageId, role: 'assistant', content: state.stream_text || '', created_at: new Date().toISOString(),
        }]);
      }
    }).catch((reason: unknown) => {
      if (!cancelled) {
        setLoading(false);
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    });
    return () => { cancelled = true; };
  }, [applyState, fetchState, pollRun]);

  const onNew = useCallback(async (message: AppendMessage) => {
    const content = messageText(message);
    const images = isSupervisor ? pendingImages : [];
    if ((!content && !images.length) || isRunning || settingsBusy || (isSupervisor && sessionStatus !== 'active')) return;
    setError('');
    setIsRunning(true);
    const optimisticContent = content || images.map((image) => `[图片] ${image.name}`).join('\n');
    setMessages((previous) => [...previous.filter((item) => item.id !== streamMessageId), {
      id: `optimistic-${crypto.randomUUID()}`, role: 'user', content: optimisticContent, created_at: new Date().toISOString(),
    }]);
    try {
      if (isSupervisor) {
        await postJSON(`${base}/message`, { content, images });
        setPendingImages([]);
      } else {
        await postJSON(`${base}/message`, { content });
      }
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* keep the original request error visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isRunning, isSupervisor, pendingImages, pollRun, sessionStatus, settingsBusy]);

  const onImageSelect = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = Array.from(input.files || []);
    input.value = '';
    if (!files.length) return;
    if (pendingImages.length + files.length > 4) {
      setError('每条消息最多附加 4 张图片。');
      return;
    }
    const allowed = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
    if (files.some((file) => !allowed.has(file.type) || file.size > 1_500_000)) {
      setError('图片仅支持 PNG/JPEG/GIF/WebP，且每张不超过 1.5 MB。');
      return;
    }
    try {
      const encoded = await Promise.all(files.map((file) => new Promise<PendingImage>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const raw = String(reader.result || '');
          const comma = raw.indexOf(',');
          if (comma < 0) { reject(new Error('图片读取失败')); return; }
          resolve({ name: file.name, media_type: file.type, data: raw.slice(comma + 1) });
        };
        reader.onerror = () => reject(reader.error || new Error('图片读取失败'));
        reader.readAsDataURL(file);
      })));
      setPendingImages((previous) => [...previous, ...encoded]);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '图片读取失败');
    }
  }, [pendingImages]);

  const onFinishIncident = useCallback(async (action: 'resolve' | 'close') => {
    if (!isSupervisor || sessionStatus !== 'active' || settingsBusy) return;
    setSettingsBusy(true);
    setError('');
    try {
      await postJSON(`${base}/${action}`, { note: action === 'resolve' ? 'resolved by operator' : 'closed by operator' });
      applyState(await fetchState());
      setPendingImages([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSettingsBusy(false);
    }
  }, [applyState, base, fetchState, isSupervisor, sessionStatus, settingsBusy]);


  const onModelChange = useCallback(async (model: string) => {
    if (isRunning || settingsBusy || model === selectedModel) return;
    const previous = selectedModel;
    setSelectedModel(model);
    setSettingsBusy(true);
    setError('');
    try {
      await postJSON(`${base}/model`, { model });
    } catch (reason) {
      setSelectedModel(previous);
      setError(reason instanceof Error ? reason.message : String(reason));
      try { applyState(await fetchState()); } catch { /* keep the original settings error visible */ }
    } finally {
      setSettingsBusy(false);
    }
  }, [applyState, base, fetchState, isRunning, selectedModel, settingsBusy]);

  const onEffortChange = useCallback(async (effort: string) => {
    if (isRunning || settingsBusy || effort === selectedEffort) return;
    const previous = selectedEffort;
    setSelectedEffort(effort);
    setSettingsBusy(true);
    setError('');
    try {
      await postJSON(`${base}/effort`, { effort });
    } catch (reason) {
      setSelectedEffort(previous);
      setError(reason instanceof Error ? reason.message : String(reason));
      try { applyState(await fetchState()); } catch { /* keep the original settings error visible */ }
    } finally {
      setSettingsBusy(false);
    }
  }, [applyState, base, fetchState, isRunning, selectedEffort, settingsBusy]);

  const onCancel = useCallback(async () => {
    try {
      await postJSON(`${base}/stop`);
      await pollRun();
    } catch (reason) {
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [base, pollRun]);

  const onRetry = useCallback(async () => {
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/retry`);
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* leave session visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, pollRun]);

  const onEdit = useCallback(async (message: AppendMessage) => {
    const content = messageText(message);
    const targetId = message.sourceId;
    const targetIndex = messages.findIndex((item) => item.id === targetId);
    if (!targetId || !content || targetIndex < 0 || messages[targetIndex].role !== 'user') {
      setError('无法定位要编辑的用户消息，请刷新后重试。');
      try { applyState(await fetchState()); } catch { /* retain the visible error */ }
      return;
    }
    if (isRunning || settingsBusy) return;
    const hasLaterUserTurn = messages.slice(targetIndex + 1).some((item) => item.role === 'user');
    if (hasLaterUserTurn && !window.confirm('编辑这条较早的消息会移除它之后的对话记录，并从这里重新回答。是否继续？')) {
      applyState(await fetchState());
      return;
    }
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/message`, { content, edit_message_id: targetId });
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* keep the original request error visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isRunning, messages, pollRun, settingsBusy]);

  const onReload = useCallback(async (parentId: string | null) => {
    if (isRunning || settingsBusy) return;
    const target = (parentId ? messages.find((item) => item.id === parentId && item.role === 'user') : undefined)
      || [...messages].reverse().find((item) => item.role === 'user');
    if (!target) { setError('找不到这条回答对应的问题。'); return; }
    const targetIndex = messages.findIndex((item) => item.id === target.id);
    const hasLaterUserTurn = messages.slice(targetIndex + 1).some((item) => item.role === 'user');
    if (hasLaterUserTurn && !window.confirm('重新生成这条较早的回答会移除它之后的对话记录。是否继续？')) return;
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/message`, { content: target.content, edit_message_id: target.id });
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* keep the original request error visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isRunning, messages, pollRun, settingsBusy]);

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning,
    onNew,
    onEdit,
    onReload,
    ...(isSupervisor ? {} : { onCancel }),
  });

  if (loading) {
    return <div className="tm-aui-loading"><LoaderCircle className="tm-aui-spin" size={18} /> 正在恢复会话记录…</div>;
  }

  const canRetry = !isRunning && ['interrupted', 'error', 'stopped'].includes(String(activeRun?.status || ''));
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className={`tm-aui-shell${isSupervisor ? ' tm-aui-supervisor-shell' : ''}`}>
        {isSupervisor && (
          <div className="tm-aui-supervisor-context">
            <div className="tm-aui-supervisor-context-head">
              <strong>Cycle {cycleId || '未知'}</strong>
              <span className={`tm-aui-supervisor-status status-${sessionStatus}`}>{sessionStatus === 'active' ? '接管中' : sessionStatus === 'resolved' ? '已解决' : '已归档'}</span>
            </div>
            <p>{triggerEvent || '通知触发的 Supervisor 对话'}</p>
          </div>
        )}
        <ThreadPrimitive.Root className="tm-aui-thread">
          <ThreadPrimitive.Viewport className="tm-aui-viewport">
            <div className="tm-aui-message-list">
              <ThreadPrimitive.Messages components={{ Message: ChatMessageView }} />
            </div>
            <ThreadPrimitive.ScrollToBottom className="tm-aui-scroll-bottom" aria-label="滚动到最新消息">
              <ArrowDown size={16} />
            </ThreadPrimitive.ScrollToBottom>
          </ThreadPrimitive.Viewport>
          <div className="tm-aui-composer-wrap">
            <ComposerPrimitive.Root className="tm-aui-composer">
              <ComposerPrimitive.Input
                className="tm-aui-input"
                placeholder={isSupervisor ? '继续与 Supervisor 讨论这个通知…' : '向 Sciver 提问；可询问最近 Cycle、证据、科学性和未解决问题…'}
                aria-label={isSupervisor ? '发送给 Supervisor 的消息' : '发送给 Sciver 的消息'}
                disabled={settingsBusy || (isSupervisor && (sessionStatus !== 'active' || isRunning))}
                rows={1}
                autoFocus={false}
              />
              {isSupervisor && sessionStatus === 'active' && (
                <div className="tm-aui-supervisor-attachments">
                  <input ref={imageInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden onChange={(event) => void onImageSelect(event)} />
                  <button type="button" disabled={isRunning || settingsBusy || pendingImages.length >= 4}
                          onClick={() => imageInputRef.current?.click()}>附加图片</button>
                  {pendingImages.map((image, index) => (
                    <button type="button" className="tm-aui-attachment-chip" key={`${image.name}-${index}`}
                            disabled={isRunning || settingsBusy} title="移除此图片"
                            onClick={() => setPendingImages((previous) => previous.filter((_item, itemIndex) => itemIndex !== index))}>
                      {image.name} ×
                    </button>
                  ))}
                  {pendingImages.length > 0 && (
                    <button type="button" disabled={isRunning || settingsBusy}
                            onClick={() => void onNew({ content: [] } as unknown as AppendMessage)}>只发送图片</button>
                  )}
                </div>
              )}
              <div className="tm-aui-composer-toolbar">
                {isSupervisor ? <div className="tm-aui-composer-settings" /> : <div className="tm-aui-composer-settings">
                  <label className="tm-aui-model-picker">
                    <span className="tm-aui-sr-only">模型</span>
                    <select aria-label="模型" value={selectedModel} disabled={isRunning || settingsBusy}
                            onChange={(event) => void onModelChange(event.target.value)}>
                      {availableModels.map((model) => <option key={model} value={model}>{model}</option>)}
                    </select>
                    <ChevronDown size={14} aria-hidden="true" />
                  </label>
                  <label className="tm-aui-effort-picker">
                    <span>强度</span>
                    <span className="tm-aui-select-wrap">
                      <select aria-label="推理强度" value={selectedEffort} disabled={isRunning || settingsBusy}
                              onChange={(event) => void onEffortChange(event.target.value)}>
                        {availableEfforts.map((effort) => <option key={effort} value={effort}>{effortLabels[effort] || effort}</option>)}
                      </select>
                      <ChevronDown size={13} aria-hidden="true" />
                    </span>
                  </label>
                </div>}
                <div className="tm-aui-composer-actions">
                  {!isSupervisor && (
                    <button type="button" className="tm-aui-stop" aria-label="停止生成" title="停止生成"
                            disabled={!isRunning} onClick={() => void onCancel()}>
                      <Square size={14} />
                    </button>
                  )}
                  <ComposerPrimitive.Send className="tm-aui-send" aria-label="发送消息" title="发送消息"
                                          disabled={settingsBusy || (isSupervisor && (sessionStatus !== 'active' || isRunning))}>
                    <Send size={17} />
                  </ComposerPrimitive.Send>
                </div>
              </div>
            </ComposerPrimitive.Root>
            <div className="tm-aui-composer-note">{isSupervisor ? 'Enter 发送 · Shift+Enter 换行 · 对话直接记录到本次 Supervisor 接管会话。' : 'Enter 发送 · Shift+Enter 换行 · 对话只读科研资料；写入 Canvas 等操作仍需预览和确认。'}</div>
          </div>
        </ThreadPrimitive.Root>
        {isSupervisor && sessionStatus === 'active' && (
          <div className="tm-aui-supervisor-actions">
            <button type="button" disabled={isRunning || settingsBusy} onClick={() => void onFinishIncident('resolve')}>标记已解决</button>
            <button type="button" disabled={isRunning || settingsBusy} onClick={() => void onFinishIncident('close')}>关闭归档</button>
          </div>
        )}
        {isSupervisor && sessionStatus !== 'active' && <p className="tm-aui-supervisor-closed">此会话已结束，对话历史仍保留在右侧。</p>}
        {canRetry && (
          <div className="tm-aui-retry-row">
            <span>上一条消息未完成，已保留原问题。</span>
            <button type="button" onClick={() => void onRetry()}><RotateCcw size={14} /> 重试上一条消息</button>
          </div>
        )}
        {pendingAction && <p className="tm-aui-action-note">已生成待确认操作预览；请检查页面中的预览后再决定是否执行。</p>}
        {error && <div className="tm-aui-error" role="alert">{error}</div>}
      </div>
    </AssistantRuntimeProvider>
  );
}

const mount = document.getElementById('tm-research-chat-app');
if (mount) {
  const sessionId = mount.dataset.sessionId || '';
  const mode = mount.dataset.mode || 'research';
  if (sessionId) createRoot(mount).render(<App sessionId={sessionId} mode={mode} />);
}
