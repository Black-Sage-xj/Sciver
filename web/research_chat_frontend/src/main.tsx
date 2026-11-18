import { bindComposerDraft } from "./drafts";
import { mergeSources, removeSubmittedSources, sourceKey, sourceMarkdown, type ReadingSource } from './research-sources';
import {
  catalogHint, choicesWithCurrent, createCatalogRefresher, effortAfterModelChange, effortLabels,
  effortsForModel, mergeCatalog, updateSelect, watchCatalogRefresh, type ModelCatalog,
} from './model-catalog.ts';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ChangeEvent } from 'react';
import { ActionBarPrimitive } from '@assistant-ui/react';
import { createRoot } from 'react-dom/client';
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useAuiState,
  useMessagePartText,
  type AppendMessage,
  type ThreadMessageLike,
} from '@assistant-ui/react';
import ReactMarkdown from 'react-markdown';
import { RichMarkdown } from './rich-content';
import { AttachmentPreview, DraftAttachments, MessageAttachments, attachmentAccept, useAttachments } from './attachments';
import { QuestionPanel, RunActivity, RunEvents, RunMetadata } from './activity-ui';
import { downloadText, formatLocalTime, matchingMessageIds, messageMarkdown, quotedMessage, type ChatAttachment, type ChatBranch, type FeatureMessage, type QueueEntry, type RunDetails, type RunQuestion } from './chat-features';
import 'katex/dist/katex.min.css';
import { ArrowDown, ChevronDown, Copy, LoaderCircle, Pencil, RotateCcw, Send, Square, Paperclip, Search, Quote, Download, GitFork, Maximize2, Minimize2, Bell, Plus, MoreHorizontal, FolderOpen, ArrowUp, X, ChevronUp } from 'lucide-react';
import './styles.css';
import './chat-surface.css';

type ChatMessage = FeatureMessage;
type ActiveRun = RunDetails | null;
type ChatState = Partial<ModelCatalog> & {
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
  runs?: RunDetails[]; branches?: ChatBranch[]; branch_id?: string; current_branch_id?: string;
  queue?: QueueEntry[]; queue_paused?: boolean; pending_questions?: RunQuestion[];
};

type ProgressState = { status?: string; streaming?: boolean; text?: string; next_offset?: number; reset?: boolean; error?: string; active_run?: ActiveRun; queue?: QueueEntry[]; queue_paused?: boolean; pending_questions?: RunQuestion[] };
type ChatSettings = Pick<ChatState, 'model' | 'reasoning_effort'> & Partial<ModelCatalog>;
type PendingImage = { name: string; media_type: string; data: string };

const streamMessageId = '__tm_research_chat_stream__';
const fallbackModels = ['gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna'];
const fallbackEfforts = ['low', 'medium', 'high', 'xhigh'];
const initialCatalog = (): ModelCatalog => ({ available_models: fallbackModels, available_reasoning_efforts: fallbackEfforts,
  model_reasoning_efforts: {}, model_catalog: { status: 'unqueried', stale: false, updated_at: null } });
const isTerminal = (status?: string) => ['complete', 'stopped', 'error', 'interrupted'].includes(String(status || ''));
const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

function revealMessageWhenRendered(id: string, className: string, onRevealed?: () => void): () => void {
  let frame = 0; let attempts = 0;
  const reveal = () => {
    const node = document.getElementById(`message-${id}`);
    if (node) { node.classList.add(className); node.scrollIntoView({ behavior: 'smooth', block: 'center' }); onRevealed?.(); }
    else if (++attempts < 60) frame = requestAnimationFrame(reveal);
  };
  // assistant-ui applies an external-store window after the parent render commits.
  frame = requestAnimationFrame(reveal);
  return () => cancelAnimationFrame(frame);
}

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
    metadata: { custom: { reading_sources: message.sources || [] } },
    createdAt: message.created_at ? new Date(message.created_at) : undefined,
  };
}

type ChatActions = {
  messages: ChatMessage[];
  quote: (text: string) => void;
  fork: (messageId: string) => void;
  regenerate: (messageId: string) => void;
  preview: (attachment: ChatAttachment) => void;
  busy: boolean;
};
const ChatActionsContext = createContext<ChatActions>({ messages: [], quote: () => {}, fork: () => {}, regenerate: () => {}, preview: () => {}, busy: false });
function MarkdownPart() {
  const part = useMessagePartText();
  const actions = useContext(ChatActionsContext);
  const taskId = document.querySelector<HTMLElement>('.tm-research-chat')?.dataset.taskId || '';
  return <RichMarkdown text={part.text} taskId={taskId} onQuote={actions.quote} />;
}
function UserTextPart() {
  const part = useMessagePartText();
  return <div className="sc-user-plain">{part.text}</div>;
}

function MessageReadingSources() {
  const sources = useAuiState((state) => state.message.metadata.custom.reading_sources) as ReadingSource[] | undefined;
  if (!sources?.length) return null;
  return <div className="tm-aui-message-sources"><ReactMarkdown components={{ a: ({href, children}) =>
    <a href={href} target="_top">{children}</a> }}>{sourceMarkdown(sources)}</ReactMarkdown></div>;
}

function ChatMessageView() {
  const page = document.querySelector<HTMLElement>('.tm-research-chat');
  const isSupervisor = page?.dataset.mode === 'supervisor';
  const isMigration = page?.dataset.sessionMode === 'migration';
  const isSetup = page?.dataset.sessionMode === 'setup';
  const canEdit = !isSupervisor && !isMigration && !isSetup;
  const canReload = !isSupervisor && !isMigration && !isSetup;
  const id = useAuiState((state) => state.message.id);
  const actions = useContext(ChatActionsContext);
  const message = actions.messages.find((item) => item.id === id);
  const [selection, setSelection] = useState('');
  return (
    <MessagePrimitive.Root className="tm-aui-message" data-chat-message-id={id} id={`message-${id}`}
      onMouseUp={(event) => {
        const selected = window.getSelection();
        setSelection(selected?.anchorNode && event.currentTarget.contains(selected.anchorNode) ? selected.toString().trim() : '');
      }}>
      <MessagePrimitive.If user>
        <div className="tm-aui-user-row">
          <div className="tm-aui-user-bubble">
            <MessagePrimitive.Parts components={{ Text: isSupervisor ? MarkdownPart : UserTextPart }} />
            <MessageReadingSources />
            <MessageAttachments attachments={message?.attachments} onPreview={actions.preview} />
          </div>
          {message?.created_at && <time className="sc-message-time" dateTime={message.created_at} title={new Date(message.created_at).toLocaleString()}>{formatLocalTime(message.created_at)}</time>}
          <ActionBarPrimitive.Root className="tm-aui-message-actions tm-aui-user-actions" hideWhenRunning autohide="never">
            <ActionBarPrimitive.Copy className="tm-aui-message-action" aria-label="复制消息" title="复制消息"><Copy size={16} aria-hidden="true" /></ActionBarPrimitive.Copy>
            {canEdit && <ActionBarPrimitive.Edit className="tm-aui-message-action" aria-label="编辑并重发" title="编辑并重发（保留旧版本）"><Pencil size={16} aria-hidden="true" /></ActionBarPrimitive.Edit>}
            {!isSupervisor && <button type="button" className="tm-aui-message-action" title="引用此消息" aria-label="引用此消息" onClick={() => actions.quote(message?.content || '')}><Quote size={16} /></button>}
          </ActionBarPrimitive.Root>
        </div>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <div className="tm-aui-assistant-row">
          <div className="tm-aui-assistant-content"><MessagePrimitive.Parts components={{ Text: MarkdownPart }} /></div>
          <MessageAttachments attachments={message?.attachments} onPreview={actions.preview} />
          <div className="sc-codex-message-meta">{message?.created_at && <time className="sc-message-time" dateTime={message.created_at} title={new Date(message.created_at).toLocaleString()}>{formatLocalTime(message.created_at)}</time>}{!isSupervisor && <RunMetadata run={message?.run} />}</div>
          <ActionBarPrimitive.Root className="tm-aui-message-actions" hideWhenRunning autohide="never">
            <ActionBarPrimitive.Copy className="tm-aui-message-action" aria-label="复制回答" title="复制回答"><Copy size={16} aria-hidden="true" /></ActionBarPrimitive.Copy>
            {canReload && <button type="button" className="tm-aui-message-action" aria-label="重新生成（保留旧版本）" title="重新生成（保留旧版本）" disabled={actions.busy} onClick={() => actions.regenerate(id)}><RotateCcw size={16} aria-hidden="true" /></button>}
            {!isSupervisor && <>
              <button type="button" className="tm-aui-message-action" title="引用回答" aria-label="引用回答" onClick={() => actions.quote(message?.content || '')}><Quote size={16} /></button>
              <button type="button" className="tm-aui-message-action" title="从这里新建对话" aria-label="从这里新建对话" disabled={actions.busy} onClick={() => actions.fork(id)}><GitFork size={16} /></button>
              <button type="button" className="tm-aui-message-action" title="导出此回答" aria-label="导出此回答" onClick={() => message && downloadText(`sciver-answer-${id.slice(0, 8)}.md`, messageMarkdown(message))}><Download size={16} /></button>
            </>}
          </ActionBarPrimitive.Root>
        </div>
      </MessagePrimitive.If>
      {!isSupervisor && selection && <button className="sc-selection-quote" type="button" onClick={() => { actions.quote(selection); setSelection(''); window.getSelection()?.removeAllRanges(); }}>引用所选文字继续追问</button>}
    </MessagePrimitive.Root>
  );
}

function ComposerSubmitControl({ running, sendMode, hasAttachments, disabled, onSend }: {
  running: boolean; sendMode: 'queue' | 'steer'; hasAttachments: boolean; disabled: boolean; onSend: () => void;
}) {
  const hasText = useAuiState((state) => Boolean(state.composer.text.trim()));
  const label = running ? sendMode === 'queue' ? '加入待发送队列' : '即时补充当前任务' : '发送消息';
  return <button type="button" className={`tm-aui-send sc-codex-send${running && !hasText && !hasAttachments ? ' sc-codex-send-resting' : ''}`} aria-label={label} title={label} disabled={disabled || (!hasText && !hasAttachments)} onClick={onSend}><ArrowUp size={19} strokeWidth={2} /></button>;
}

function EditMessageComposer() {
  return (
    <MessagePrimitive.Root className="tm-aui-message">
      <ComposerPrimitive.Root className="tm-aui-composer tm-aui-edit-composer">
        <ComposerPrimitive.Input className="tm-aui-input" aria-label="编辑消息" rows={3} autoFocus />
        <div className="tm-aui-edit-actions">
          <ComposerPrimitive.Cancel className="tm-aui-edit-cancel">取消</ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send className="tm-aui-edit-submit">编辑并重发</ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
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
    sources: message.sources || [],
    attachments: [...new Map([...(message.attachments || []), ...(message.artifacts || [])].map((item) => [item.id, item])).values()], attachment_ids: message.attachment_ids || [], run: message.run, run_id: message.run_id,
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
  const [catalog, setCatalog] = useState<ModelCatalog>(initialCatalog);
  const receivedLiveCatalog = useRef(false);
  const modelSelectRef = useRef<HTMLSelectElement>(null);
  const effortSelectRef = useRef<HTMLSelectElement>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [sessionStatus, setSessionStatus] = useState('active');
  const [cycleId, setCycleId] = useState('');
  const [triggerEvent, setTriggerEvent] = useState('');
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const taskId = document.querySelector<HTMLElement>('.tm-research-chat')?.dataset.taskId || '';
  const sourcesStorageKey = `sciver-chat-sources-v1:${mode}:${sessionId}`;
  const [pendingSources, setPendingSources] = useState<ReadingSource[]>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(sourcesStorageKey) || '[]');
      return Array.isArray(stored) ? stored.reduce((all: ReadingSource[], source: ReadingSource) =>
        mergeSources(all, source, taskId), []) : [];
    } catch { return []; }
  });
  const pendingSourcesRef = useRef(pendingSources);
  pendingSourcesRef.current = pendingSources;
  const imageInputRef = useRef<HTMLInputElement>(null);
  const base = isSupervisor
    ? `/api/supervisor-session/${encodeURIComponent(sessionId)}`
    : `/api/research-chat/${encodeURIComponent(sessionId)}`;

  const attachments = useAttachments(sessionId, !isSupervisor, setError);
  const attachmentsRef = useRef(attachments); attachmentsRef.current = attachments;
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [fileCollection, setFileCollection] = useState<ChatAttachment[]>([]);
  const [showFiles, setShowFiles] = useState(false);
  const [filesBusy, setFilesBusy] = useState(false);
  const [queuePaused, setQueuePaused] = useState(false);
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueEditId, setQueueEditId] = useState('');
  const [queueEditText, setQueueEditText] = useState('');
  const [branches, setBranches] = useState<ChatBranch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [questions, setQuestions] = useState<RunQuestion[]>([]);
  const [search, setSearch] = useState('');
  const [searchIndex, setSearchIndex] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const [openPopover, setOpenPopover] = useState<'model' | 'more' | null>(null);
  const modelPopoverRef = useRef<HTMLDivElement>(null);
  const morePopoverRef = useRef<HTMLDivElement>(null);
  const modelTriggerRef = useRef<HTMLButtonElement>(null);
  const moreTriggerRef = useRef<HTMLButtonElement>(null);
  const searchTriggerRef = useRef<HTMLButtonElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const utilityRef = useRef<HTMLDivElement>(null);
  const filesTriggerRef = useRef<HTMLButtonElement>(null);
  const projectLabel = document.querySelector<HTMLElement>('.tm-research-chat')?.dataset.projectLabel?.trim() || (taskId ? '当前科研项目' : '科研对话');
  const [historyLimit, setHistoryLimit] = useState(100);
  const [historyEnd, setHistoryEnd] = useState<number | null>(null);
  const [anchorMessageId, setAnchorMessageId] = useState('');
  const [expandedInput, setExpandedInput] = useState(false);
  const [sendMode, setSendMode] = useState<'queue' | 'steer'>('queue');
  const [shortcut, setShortcut] = useState<'enter' | 'ctrlEnter'>(() => {
    try { return localStorage.getItem('sciver-chat-send-shortcut') === 'ctrlEnter' ? 'ctrlEnter' : 'enter'; } catch { return 'enter'; }
  });
  const [connection, setConnection] = useState<'online' | 'reconnecting' | 'offline'>(navigator.onLine ? 'online' : 'offline');
  const [notificationsEnabled, setNotificationsEnabled] = useState(() => {
    try { return localStorage.getItem('sciver-chat-notifications') === '1' && 'Notification' in window && Notification.permission === 'granted'; } catch { return false; }
  });
  const notificationRef = useRef(notificationsEnabled); notificationRef.current = notificationsEnabled;
  const notifiedRuns = useRef(new Set<string>());
  const pollingRef = useRef(false);
  const mountedRef = useRef(true);
  const submissionRef = useRef(false);
  const mutationRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<{ getState(): { text: string }; setText(text: string): void } | null>(null);
  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (isSupervisor) return;
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== window.parent || window.parent === window
          || event.data?.type !== 'sciver-chat-add-source' || event.data.task_id !== taskId) return;
      const previous = pendingSourcesRef.current;
      const full = previous.length >= 4 && !previous.some((item) => sourceKey(item) === sourceKey(event.data.source));
      const error = full ? '每条消息最多附加 4 个来源，请移除一个后再添加。' : '';
      if (error) setError(error);
      const next = mergeSources(previous, event.data.source, taskId);
      pendingSourcesRef.current = next;
      setPendingSources(next);
      window.parent.postMessage({type:'sciver-chat-source-result',task_id:taskId,
        request_id:event.data.request_id,error}, location.origin);
    };
    window.addEventListener('message', receive);
    if (window.parent !== window) window.parent.postMessage({ type: 'sciver-chat-ready', task_id: taskId }, location.origin);
    return () => window.removeEventListener('message', receive);
  }, [isSupervisor, taskId]);

  useEffect(() => {
    if (isSupervisor) return;
    try { localStorage.setItem(sourcesStorageKey, JSON.stringify(pendingSources)); } catch { /* Keep the active draft usable. */ }
  }, [pendingSources, sourcesStorageKey, isSupervisor]);

  const fetchState = useCallback(async () => {
    if (isSupervisor) return getJSON<ChatState>(`${base}/state`);
    const [state, settings] = await Promise.all([
      getJSON<ChatState>(`${base}/state`),
      getJSON<ChatSettings>(`${base}/settings`),
    ]);
    return { ...state, ...settings };
  }, [base, isSupervisor]);
  const applyState = useCallback((state: ChatState) => {
    const stored = normalizeMessages(state.messages).map((message) => ({ ...message,
      run: message.run || state.runs?.find((run) => run.id === message.run_id),
    }));
    if (state.streaming && state.stream_text) stored.push({ id: streamMessageId, role: 'assistant', content: state.stream_text,
      created_at: state.active_run?.started_at, run: state.active_run || undefined });
    setMessages(stored);
    setSelectedModel(state.model || 'gpt-6.1-sol');
    setSelectedEffort(state.reasoning_effort || 'medium');
    if (!receivedLiveCatalog.current) setCatalog((previous) => mergeCatalog(previous, state));
    setActiveRun(state.active_run || null);
    setPendingAction(Boolean(state.pending_action));
    setSessionStatus(state.status || 'active');
    setCycleId(state.cycle_id || '');
    setTriggerEvent(state.trigger_event || '');
    if (state.queue) setQueue(state.queue);
    if (typeof state.queue_paused === 'boolean') setQueuePaused(state.queue_paused);
    if (state.branches) setBranches(state.branches);
    setBranchId(state.current_branch_id || state.branch_id || '');
    setQuestions(state.pending_questions || state.active_run?.pending_questions || []);
    if (typeof state.streaming === 'boolean') setIsRunning(state.streaming);
  }, []);

  const pollRun = useCallback(async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    let consecutiveNetworkFailures = 0;
    try {
      if (isSupervisor) {
        for (let attempt = 0; attempt < 900; attempt += 1) {
          if (!mountedRef.current) return;
          if (document.hidden) { await sleep(1000); continue; }
          let progress: ProgressState;
          try { progress = await getJSON<ProgressState>(`${base}/progress`); consecutiveNetworkFailures = 0; }
          catch (reason) { if (++consecutiveNetworkFailures > 60) throw reason; await sleep(1000); continue; }
          if (!progress.streaming) { applyState(await fetchState()); setIsRunning(false); if (progress.error) setError(String(progress.error)); return; }
          await sleep(1000);
        }
        setIsRunning(false);
        setError('Supervisor 回复仍未完成。已保留会话记录；刷新页面可重新查看状态。');
        return;
      }
      let streamText = '';
      let streamOffset = 0;
      let streamRunId = '';
      while (mountedRef.current) {
        let progress: ProgressState;
        try {
          progress = await getJSON<ProgressState>(`${base}/progress?offset=${streamOffset}`);
          consecutiveNetworkFailures = 0; setConnection('online');
        } catch (reason) {
          setConnection(navigator.onLine ? 'reconnecting' : 'offline');
          if (++consecutiveNetworkFailures > 60) throw reason;
          await sleep(1000); continue;
        }
        if (progress.active_run) {
          if (streamRunId && progress.active_run.id !== streamRunId) {
            streamText = ''; streamOffset = 0; streamRunId = progress.active_run.id || '';
            applyState(await fetchState());
            continue;
          }
          streamRunId = progress.active_run.id || streamRunId;
          setActiveRun(progress.active_run);
        }
        if (progress.queue) setQueue(progress.queue);
        if (typeof progress.queue_paused === 'boolean') setQueuePaused(progress.queue_paused);
        setQuestions(progress.pending_questions || progress.active_run?.pending_questions || []);
        const delta = String(progress.text || '');
        streamText = progress.reset ? delta : streamText + delta;
        streamOffset = typeof progress.next_offset === 'number' ? Math.max(0, progress.next_offset) : streamOffset + delta.length;
        if (streamText) setMessages((previous) => [...previous.filter((message) => message.id !== streamMessageId), {
          id: streamMessageId, role: 'assistant', content: streamText,
          created_at: progress.active_run?.started_at, run: progress.active_run || undefined,
        }]);
        if (isTerminal(progress.status) || progress.streaming === false) {
          const state = await fetchState(); applyState(state);
          const completedId = progress.active_run?.id || streamRunId;
          if (progress.status === 'complete' && completedId && !notifiedRuns.current.has(completedId)) {
            notifiedRuns.current.add(completedId);
            if (notificationRef.current && 'Notification' in window && Notification.permission === 'granted') {
              const notification = new Notification('Sciver 回复已完成', { body: '点击查看对话。', tag: `sciver-${sessionId}` });
              notification.onclick = () => { window.focus(); notification.close(); };
            }
          }
          if (progress.status === 'error') setError(progress.error || '回复生成失败，问题已保存，可以重试。');
          if (progress.status === 'interrupted') setError('回复已中断，问题已保存，可以重试。');
          if (state.pending_action) { window.location.reload(); return; }
          if (state.streaming) { streamText = ''; streamOffset = 0; streamRunId = state.active_run?.id || ''; await sleep(350); continue; }
          setIsRunning(false); return;
        }
        await sleep(document.hidden ? 1000 : 350);
      }
    } finally { pollingRef.current = false; }
  }, [applyState, base, fetchState, isSupervisor, sessionId]);

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

    }).catch((reason: unknown) => {
      if (!cancelled) {
        setLoading(false);
        if (!isSupervisor) setConnection('reconnecting');
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    });
    return () => { cancelled = true; };
  }, [applyState, fetchState, pollRun, isSupervisor]);

  const returnToLatest = useCallback(() => {
    setHistoryEnd(null); setHistoryLimit(100); setSearch(''); setSearchIndex(0); setAnchorMessageId('');
    document.querySelectorAll('.sc-search-current,.sc-anchor-current').forEach((element) => {
      element.classList.remove('sc-search-current', 'sc-anchor-current');
    });
    if (location.hash.startsWith('#message-')) history.replaceState(history.state, '', location.pathname + location.search);
  }, []);

  const onNew = useCallback(async (message: AppendMessage) => {
    const content = messageText(message);
    const submittedSources = pendingSources;
    const images = isSupervisor ? pendingImages : [];
    const attachmentIds = isSupervisor ? [] : attachmentsRef.current.ids;
    if ((!content && !images.length && !attachmentIds.length) || isRunning || settingsBusy || submissionRef.current || (!isSupervisor && attachmentsRef.current.blocked) || (isSupervisor && sessionStatus !== 'active')) return;
    if (!isSupervisor) returnToLatest();
    submissionRef.current = true;
    setError(''); setIsRunning(true);
    const optimisticContent = content || images.map((image) => `[图片] ${image.name}`).join('\n');
    const submittedAttachments = attachmentsRef.current.items.flatMap((item) => item.attachment && attachmentIds.includes(item.attachment.id) ? [item.attachment] : []);
    setMessages((previous) => [...previous.filter((item) => item.id !== streamMessageId), {
      id: `optimistic-${crypto.randomUUID()}`, role: 'user', content: optimisticContent,
      created_at: new Date().toISOString(), sources: submittedSources, attachments: submittedAttachments,
    }]);
    try {
      if (isSupervisor) { await postJSON(`${base}/message`, { content, images }); setPendingImages([]); }
      else {
        await postJSON(`${base}/message`, { content, sources: submittedSources, attachment_ids: attachmentIds });
        attachmentsRef.current.clearSubmitted(attachmentIds);
        setPendingSources((current) => removeSubmittedSources(current, submittedSources));
        if (composerRef.current?.getState().text.trim() === content) composerRef.current?.setText('');
        try { applyState(await fetchState()); } catch { setConnection('reconnecting'); }
      }
      submissionRef.current = false;
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* Keep the draft and original error available. */ }
      setIsRunning(false); setError(reason instanceof Error ? reason.message : String(reason));
    } finally { submissionRef.current = false; }
  }, [applyState, base, fetchState, isRunning, isSupervisor, pendingImages, pendingSources, pollRun, returnToLatest, sessionStatus, settingsBusy]);

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
      const updated = await postJSON<{ reasoning_effort?: string }>(`${base}/model`, { model });
      if (updated.reasoning_effort) setSelectedEffort(updated.reasoning_effort);
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
    if (!isSupervisor) returnToLatest();
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/retry`);
      applyState(await fetchState());
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* leave session visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isSupervisor, pollRun, returnToLatest]);

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
    returnToLatest();
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/message`, { content, edit_message_id: targetId, sources: messages[targetIndex].sources || [], attachment_ids: messages[targetIndex].attachment_ids || messages[targetIndex].attachments?.map((item) => item.id) || [] });
      applyState(await fetchState());
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* keep the original request error visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isRunning, messages, pollRun, returnToLatest, settingsBusy]);

  const onReload = useCallback(async (parentId: string | null) => {
    if (isRunning || settingsBusy) return;
    const target = (parentId ? messages.find((item) => item.id === parentId && item.role === 'user') : undefined)
      || [...messages].reverse().find((item) => item.role === 'user');
    if (!target) { setError('找不到这条回答对应的问题。'); return; }
    returnToLatest();
    setError('');
    setIsRunning(true);
    try {
      await postJSON(`${base}/regenerate`, { message_id: target.id });
      applyState(await fetchState());
      await pollRun();
    } catch (reason) {
      try { applyState(await fetchState()); } catch { /* keep the original request error visible */ }
      setIsRunning(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applyState, base, fetchState, isRunning, messages, pollRun, returnToLatest, settingsBusy]);

  const visibleEnd = historyEnd === null ? messages.length : Math.min(historyEnd, messages.length);
  const visibleStart = Math.max(0, visibleEnd - historyLimit);
  const runtime = useExternalStoreRuntime({
    messages: isSupervisor ? messages : messages.slice(visibleStart, visibleEnd),
    convertMessage,
    isRunning,
    isSendDisabled: !isSupervisor && attachments.blocked,
    onNew,
    onEdit,
    onReload,
    ...(isSupervisor ? {} : { onCancel }),
  });

  composerRef.current = runtime.thread.composer;

  useEffect(() => {
    if (loading) return;
    try {
      return bindComposerDraft(runtime.thread.composer, window.localStorage,
        window.sessionStorage, sessionId, mode);
    } catch { /* Storage access can be blocked by browser policy. */ }
  }, [runtime, loading, sessionId, mode]);

  useEffect(() => {
    if (isSupervisor || loading) return;
    let active = true;
    const refresh = createCatalogRefresher(
      () => getJSON<Partial<ModelCatalog>>('/api/research-chat/models?refresh=1'),
      (value) => { if (active) { receivedLiveCatalog.current = true; setCatalog((previous) => mergeCatalog(previous, value)); } },
      () => { if (active) setCatalog((previous) => ({ ...previous,
        model_catalog: { ...previous.model_catalog, status: 'unavailable', stale: true } })); },
    );
    const stop = watchCatalogRefresh(refresh, [modelSelectRef.current, effortSelectRef.current].filter((control): control is HTMLSelectElement => Boolean(control)));
    return () => { active = false; stop(); };
  }, [isSupervisor, loading]);

  const refreshCurrentState = useCallback(async () => {
    const state = await fetchState(); applyState(state);
    setConnection('online');
    if (state.streaming) void pollRun().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
    return state;
  }, [applyState, fetchState, pollRun]);
  useEffect(() => {
    if (isSupervisor) return;
    const offline = () => setConnection('offline');
    const online = () => { setConnection('reconnecting'); void refreshCurrentState().catch(() => setConnection('reconnecting')); };
    window.addEventListener('offline', offline); window.addEventListener('online', online);
    return () => { window.removeEventListener('offline', offline); window.removeEventListener('online', online); };
  }, [isSupervisor, refreshCurrentState]);

  const onQueueAction = async (action: 'add' | 'edit' | 'remove' | 'resume', queueId?: string, content?: string) => {
    if (queueBusy || submissionRef.current || mutationRef.current) return;
    if (action === 'add' || action === 'resume') returnToLatest();
    mutationRef.current = true;
    setQueueBusy(true); setError('');
    try {
      const submittedIds = action === 'add' ? attachmentsRef.current.ids : undefined;
      await postJSON(`${base}/queue`, { action, queue_id: queueId, content,
        ...(action === 'add' ? { attachment_ids: submittedIds, sources: pendingSources } : {}) });
      if (action === 'add') {
        if (composerRef.current?.getState().text.trim() === content) composerRef.current?.setText('');
        attachmentsRef.current.clearSubmitted(submittedIds || []);
        setPendingSources((current) => removeSubmittedSources(current, pendingSources));
      }
      setQueueEditId(''); await refreshCurrentState();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { mutationRef.current = false; setQueueBusy(false); }
  };
  const submitDraft = async () => {
    const content = composerRef.current?.getState().text.trim() || '';
    if (submissionRef.current || mutationRef.current || settingsBusy || queueBusy || attachmentsRef.current.blocked || (!content && !attachmentsRef.current.ids.length)) return;
    if (connection === 'offline') { setError('当前离线，草稿已保留；恢复网络后再发送。'); return; }
    if (!isRunning) { await onNew({ content: [{ type: 'text', text: content }] } as unknown as AppendMessage); return; }
    if (sendMode === 'queue') { await onQueueAction('add', undefined, content); return; }
    if (!content) { setError('即时补充需要文字。附件可以加入待发送队列。'); return; }
    if (attachmentsRef.current.ids.length || pendingSources.length) { setError('即时补充只发送文字。请切换到排队发送，以保留本条的附件和讨论来源。'); return; }
    returnToLatest();
    mutationRef.current = true; setQueueBusy(true); setError('');
    try {
      await postJSON(`${base}/steer`, { content });
      if (composerRef.current?.getState().text.trim() === content) composerRef.current?.setText('');
      await refreshCurrentState();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { mutationRef.current = false; setQueueBusy(false); }
  };
  const quote = (text: string) => {
    if (!text.trim()) return;
    const composer = composerRef.current;
    const old = composer?.getState().text || '';
    composer?.setText(`${old}${old && !old.endsWith('\n') ? '\n\n' : ''}${quotedMessage(text)}`);
    inputRef.current?.focus();
  };
  const fork = async (messageId: string) => {
    if (settingsBusy || isRunning) return;
    setSettingsBusy(true); setError('');
    try {
      const result = await postJSON<{ session_id?: string }>(`${base}/fork`, { message_id: messageId });
      if (!result.session_id) throw new Error('未返回新对话地址，请重试。');
      location.assign(`/research-chat/?session=${encodeURIComponent(result.session_id)}${document.body.classList.contains('tm-chat-embedded') ? '&embed=1' : ''}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setSettingsBusy(false); }
  };
  const regenerate = async (messageId: string) => {
    if (isRunning || settingsBusy || mutationRef.current) return;
    mutationRef.current = true; setIsRunning(true); setError(''); returnToLatest();
    try { await postJSON(`${base}/regenerate`, { message_id: messageId }); applyState(await fetchState()); mutationRef.current = false; await pollRun(); }
    catch (reason) { try { applyState(await fetchState()); } catch { setConnection('reconnecting'); } setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { mutationRef.current = false; }
  };
  const switchBranch = async (selected: string) => {
    if (!selected || isRunning || settingsBusy) return;
    setSettingsBusy(true); setError('');
    try { await postJSON(`${base}/branch`, { branch_id: selected }); setHistoryEnd(null); setHistoryLimit(100); await refreshCurrentState(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSettingsBusy(false); }
  };
  const onAnswer = async (requestId: string, answers: Record<string, string[]>) => {
    if (queueBusy) return;
    setQueueBusy(true); setError('');
    try { await postJSON(`${base}/answer`, { request_id: requestId, answers }); await refreshCurrentState(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setQueueBusy(false); }
  };
  const loadFiles = async (toggle = true) => {
    if (toggle && showFiles) { setShowFiles(false); return; }
    setSearchOpen(false); setShowFiles(true); setFilesBusy(true);
    try { const result = await getJSON<{ attachments: ChatAttachment[] }>(`${base}/attachments`); setFileCollection(result.attachments || []); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setFilesBusy(false); }
  };
  const toggleNotifications = async () => {
    if (notificationsEnabled) { setNotificationsEnabled(false); try { localStorage.removeItem('sciver-chat-notifications'); } catch { /* optional preference */ } return; }
    if (!('Notification' in window)) { setError('当前浏览器不支持系统通知，可通过聊天窗口的未读提示查看回复。'); return; }
    const permission = await Notification.requestPermission();
    if (permission === 'granted') { setNotificationsEnabled(true); try { localStorage.setItem('sciver-chat-notifications', '1'); } catch { /* preference applies to current page */ } }
    else setError('浏览器未允许通知。你可在网站权限中开启后再次启用。');
  };
  const searchHits = matchingMessageIds(messages, search);
  const searchKey = searchHits.join('|');
  useEffect(() => {
    document.querySelectorAll('.sc-search-current').forEach((element) => element.classList.remove('sc-search-current'));
    const id = searchKey.split('|')[searchIndex % Math.max(searchKey.split('|').length, 1)];
    if (!id) return;
    const index = messages.findIndex((message) => message.id === id);
    if (index >= 0 && (index < visibleStart || index >= visibleEnd)) { setHistoryEnd(Math.min(messages.length, index + 51)); setHistoryLimit(100); return; }
    return revealMessageWhenRendered(id, 'sc-search-current');
  }, [searchKey, searchIndex, visibleStart, visibleEnd]);
  useEffect(() => {
    const readAnchor = () => {
      try { setAnchorMessageId(location.hash.startsWith('#message-') ? decodeURIComponent(location.hash.slice('#message-'.length)) : ''); }
      catch { setAnchorMessageId(''); }
    };
    readAnchor(); window.addEventListener('hashchange', readAnchor);
    return () => window.removeEventListener('hashchange', readAnchor);
  }, []);
  useEffect(() => {
    if (loading || !anchorMessageId) return;
    const index = messages.findIndex((message) => message.id === anchorMessageId);
    if (index < 0) return;
    if (index < visibleStart || index >= visibleEnd) { setHistoryEnd(Math.min(messages.length, index + 51)); setHistoryLimit(100); return; }
    document.querySelectorAll('.sc-anchor-current').forEach((element) => element.classList.remove('sc-anchor-current'));
    return revealMessageWhenRendered(anchorMessageId, 'sc-anchor-current', () => setAnchorMessageId(''));
  }, [anchorMessageId, loading, visibleStart, visibleEnd, messages]);

  useEffect(() => {
    if (isSupervisor) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      const wrapper = openPopover === 'model' ? modelPopoverRef.current : morePopoverRef.current;
      if (openPopover && !wrapper?.contains(event.target)) setOpenPopover(null);
      if (!utilityRef.current?.contains(event.target)) { setSearchOpen(false); setShowFiles(false); }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && openPopover) {
        event.preventDefault(); event.stopPropagation();
        const trigger = openPopover === 'model' ? modelTriggerRef.current : moreTriggerRef.current;
        setOpenPopover(null); trigger?.focus();
      } else if (event.key === 'Escape' && searchOpen) {
        event.preventDefault(); setSearchOpen(false); setSearch(''); searchTriggerRef.current?.focus();
      } else if (event.key === 'Escape' && showFiles) {
        event.preventDefault(); setShowFiles(false); filesTriggerRef.current?.focus();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f'
        && document.querySelector('.tm-research-chat')?.contains(document.activeElement)) {
        event.preventDefault(); setShowFiles(false); setSearchOpen(true); requestAnimationFrame(() => searchInputRef.current?.focus());
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => { document.removeEventListener('pointerdown', onPointerDown); document.removeEventListener('keydown', onKeyDown, true); };
  }, [isSupervisor, openPopover, searchOpen, showFiles]);
  useEffect(() => {
    if (!openPopover) return;
    const frame = requestAnimationFrame(() => {
      const wrapper = openPopover === 'model' ? modelPopoverRef.current : morePopoverRef.current;
      wrapper?.querySelector<HTMLElement>('[role="dialog"] select:not(:disabled), [role="dialog"] button:not(:disabled)')?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [openPopover]);

  if (loading) {
    return <div className="tm-aui-loading"><LoaderCircle className="tm-aui-spin" size={18} /> 正在恢复会话记录…</div>;
  }

  const availableModels = catalog.available_models;
  const availableEfforts = effortsForModel(catalog, selectedModel);
  const capabilityHint = catalogHint(catalog, selectedModel, selectedEffort);
  const canRetry = !isRunning && ['interrupted', 'error', 'stopped'].includes(String(activeRun?.status || ''));
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ChatActionsContext.Provider value={{ messages, quote, fork: (id) => void fork(id), preview: attachments.setPreview, regenerate: (id) => void regenerate(id), busy: settingsBusy || isRunning }}>
      <div className={`tm-aui-shell sc-codex-shell${isSupervisor ? ' tm-aui-supervisor-shell' : ''}${expandedInput ? ' sc-input-expanded' : ''}${attachments.dragging ? ' sc-file-dragging' : ''}`}
        onDragOver={attachments.onDragOver} onDrop={attachments.onDrop}
        onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) attachments.setDragging(false); }}>
        {!isSupervisor && <>
          <div className="sc-chat-utility" ref={utilityRef}>
          <div className={`sc-chat-tools sc-codex-top-tools${searchOpen ? ' sc-codex-search-open' : ''}`}>
            <div className="sc-codex-tool-spacer" /><div data-preparation-secondary-slot />
            <button ref={searchTriggerRef} type="button" className="sc-codex-tool-button" aria-label="查找对话内容" title="查找对话内容（Ctrl / ⌘ + F）" aria-expanded={searchOpen} aria-controls="sc-codex-search-panel" onClick={() => { setShowFiles(false); setSearchOpen((value) => !value); if (!searchOpen) requestAnimationFrame(() => searchInputRef.current?.focus()); }}><Search size={16} /><span>查找</span></button>
            {branches.length > 1 && <label className="sc-branch-picker sc-codex-branch-picker"><span>版本</span><select aria-label="对话版本" value={branchId} disabled={isRunning || settingsBusy} onChange={(event) => void switchBranch(event.target.value)}>{branches.map((branch, index) => <option key={branch.id} value={branch.id}>{branch.label || `版本 ${index + 1}`} · {branch.message_count ?? '—'} 条</option>)}</select></label>}
            <button ref={filesTriggerRef} type="button" className="sc-codex-tool-button" aria-controls="sc-chat-files-panel" aria-haspopup="dialog" aria-label="本聊天文件" title="本聊天文件" aria-expanded={showFiles} onClick={() => void loadFiles()}><Paperclip size={16} /><span>文件</span></button>
          </div>
          <div id="sc-codex-search-panel" className="sc-codex-search-panel" role="search" aria-label="对话查找" hidden={!searchOpen}>
            <label className="sc-chat-search"><Search size={15} /><input ref={searchInputRef} aria-label="搜索当前对话" placeholder="在此对话中查找" value={search} onChange={(event) => { setSearch(event.target.value); setSearchIndex(0); }} /></label>
            {search && <><small>{searchHits.length ? `${searchIndex % searchHits.length + 1} / ${searchHits.length}` : '无匹配'}</small><button type="button" aria-label="上一个" title="上一个匹配" disabled={!searchHits.length} onClick={() => setSearchIndex((previous) => (previous - 1 + searchHits.length) % searchHits.length)}><ChevronUp size={15} /></button><button type="button" aria-label="下一个" title="下一个匹配" disabled={!searchHits.length} onClick={() => setSearchIndex((previous) => (previous + 1) % searchHits.length)}><ChevronDown size={15} /></button></>}
            <button type="button" aria-label="关闭对话查找" title="关闭查找" onClick={() => { setSearchOpen(false); setSearch(''); searchTriggerRef.current?.focus(); }}><X size={15} /></button>
          </div>
          {showFiles && <section id="sc-chat-files-panel" className="sc-chat-files" role="dialog" aria-label="本聊天文件列表"><header><strong>本聊天文件</strong><span>包含已上传草稿与对话成果</span><button type="button" disabled={filesBusy} onClick={() => void loadFiles(false)}>刷新</button><button type="button" onClick={() => { setShowFiles(false); filesTriggerRef.current?.focus(); }}>收起</button></header>{filesBusy ? <p>正在读取文件…</p> : fileCollection.length ? <MessageAttachments attachments={fileCollection} onPreview={(attachment) => { setShowFiles(false); attachments.setPreview(attachment); }} /> : <p>还没有文件。点击输入框旁的 + 添加图片或文档。</p>}</section>}
          </div>
          {connection !== 'online' && <div className="sc-network-state" role="status"><span>{connection === 'offline' ? '网络已断开，草稿已保留' : '连接中断，正在重新连接…'}</span><button type="button" onClick={() => void refreshCurrentState().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))}>重新连接</button></div>}
          {attachments.dragging && <div className="sc-drop-hint">松开以上传图片或文档</div>}
        </>}
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
              {!isSupervisor && visibleStart > 0 && <button type="button" className="sc-history-load" onClick={() => setHistoryLimit((count) => count + 100)}>查看更早的消息（还有 {visibleStart} 条）</button>}
              {!isSupervisor && historyEnd !== null && <button type="button" className="sc-history-load" onClick={() => { setHistoryEnd(null); setHistoryLimit(100); setSearch(''); }}>返回最新消息</button>}
              <ThreadPrimitive.Messages components={{ Message: ChatMessageView, UserEditComposer: EditMessageComposer }} />
              {!isSupervisor && visibleEnd === messages.length && <div data-preparation-inline-slot data-streaming={isRunning ? 'true' : 'false'} />}
              {!isSupervisor && visibleEnd < messages.length && <button type="button" className="sc-history-load" onClick={() => { setHistoryEnd(visibleEnd + 100 >= messages.length ? null : visibleEnd + 100); setHistoryLimit(100); setSearch(''); }}>查看后续消息（还有 {messages.length - visibleEnd} 条）</button>}
            </div>
            <ThreadPrimitive.ScrollToBottom className="tm-aui-scroll-bottom" aria-label="滚动到最新消息">
              <ArrowDown size={16} />
            </ThreadPrimitive.ScrollToBottom>
          </ThreadPrimitive.Viewport>
          <div className="tm-aui-composer-wrap">
            {isRunning && <RunActivity run={activeRun} hasReply={messages.some((message) => message.id === streamMessageId && Boolean(message.content))} />}
            {!isSupervisor && <>
              {isRunning && <RunEvents run={activeRun} active />}
              {questions.length > 0 && <QuestionPanel requests={questions} onAnswer={onAnswer} busy={queueBusy} />}
              {(queue.length > 0 || queuePaused) && <section className="sc-queue-panel" aria-label="待发送消息队列">
                <header><strong>待发送 · {queue.length}</strong>{queuePaused && <><span>队列已暂停</span><button type="button" disabled={queueBusy} onClick={() => void onQueueAction('resume')}>继续队列</button></>}</header>
                <ol>{queue.map((entry) => <li key={entry.id}>{queueEditId === entry.id ? <form onSubmit={(event) => { event.preventDefault(); void onQueueAction('edit', entry.id, queueEditText); }}>
                  <textarea aria-label="编辑待发送消息" value={queueEditText} onChange={(event) => setQueueEditText(event.target.value)} rows={3} /><button type="submit" disabled={queueBusy || !queueEditText.trim()}>保存</button><button type="button" onClick={() => setQueueEditId('')}>取消</button>
                </form> : <><p>{entry.content || '附件消息'}</p>{Boolean(entry.attachment_ids?.length || entry.attachments?.length) && <small>{entry.attachment_ids?.length || entry.attachments?.length} 个附件</small>}<button type="button" disabled={queueBusy} onClick={() => { setQueueEditId(entry.id); setQueueEditText(entry.content); }}>编辑</button><button type="button" disabled={queueBusy} onClick={() => void onQueueAction('remove', entry.id)}>撤回</button></>}</li>)}</ol>
              </section>}
            </>}
            {!isSupervisor && <div className="sc-codex-context-bar" aria-label="当前项目上下文"><FolderOpen size={17} aria-hidden="true" /><span title={projectLabel}>{projectLabel}</span>{pendingSources.length > 0 && <small>{pendingSources.length} 个讨论来源</small>}</div>}
            <ComposerPrimitive.Root className={`tm-aui-composer${isSupervisor ? '' : ' sc-codex-composer'}`}>
              {!isSupervisor && pendingSources.length > 0 && (
                <div className="tm-aui-reading-sources" aria-label="本条消息的讨论来源">
                  <p>讨论来源 · 发送后才加入对话</p>
                  {pendingSources.map((source) => (
                    <div className="tm-aui-reading-source" key={sourceKey(source)}>
                      <a href={source.href} target="_top">{source.label}{source.section ? ' · ' + source.section : ''}</a>
                      <small>{[source.hypothesis_id, source.cycle_id ? 'Cycle ' + source.cycle_id : '',
                        source.source_version.slice(0, 8)].filter(Boolean).join(' · ')}</small>
                      <button type="button" aria-label={`移除来源：${source.label}`} onClick={() =>
                        setPendingSources((previous) => previous.filter((item) => sourceKey(item) !== sourceKey(source)))}>×</button>
                      {source.quote && <blockquote>{source.quote}</blockquote>}
                    </div>
                  ))}
                </div>
              )}
              {!isSupervisor && <DraftAttachments manager={attachments} />}
              <ComposerPrimitive.Input
                ref={inputRef}
                submitMode={isSupervisor ? undefined : 'none'}
                addAttachmentOnPaste={isSupervisor}
                onPaste={isSupervisor ? undefined : attachments.onPaste}
                onKeyDown={isSupervisor ? undefined : (event) => {
                  if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.shiftKey) return;
                  if (shortcut === 'enter' && !event.ctrlKey && !event.metaKey || shortcut === 'ctrlEnter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void submitDraft(); }
                }}
                className="tm-aui-input"
                placeholder={isSupervisor ? '继续与 Supervisor 讨论这个通知…' : '随心输入，继续讨论…'}
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
              {isSupervisor ? <div className="tm-aui-composer-toolbar"><div className="tm-aui-composer-settings" /><div className="tm-aui-composer-actions"><ComposerPrimitive.Send className="tm-aui-send" aria-label="发送消息" title="发送消息" disabled={settingsBusy || sessionStatus !== 'active' || isRunning}><Send size={17} /></ComposerPrimitive.Send></div></div> : <div className="tm-aui-composer-toolbar sc-codex-composer-footer">
                <div className="sc-codex-composer-left">
                  <input ref={attachmentInputRef} type="file" multiple accept={attachmentAccept} hidden onChange={(event) => { attachments.addFiles(Array.from(event.target.files || [])); event.target.value = ''; }} />
                  <button type="button" className="sc-codex-icon-button sc-codex-add" aria-label="添加图片或文档" title="添加图片或文档，也可粘贴或拖入" onClick={() => attachmentInputRef.current?.click()} disabled={attachments.items.length >= 10}><Plus size={22} strokeWidth={1.7} /></button>
                  <div className="sc-codex-popover-anchor" ref={morePopoverRef}>
                    <button ref={moreTriggerRef} type="button" className="sc-codex-icon-button" aria-label="更多输入选项" title="更多输入选项" aria-haspopup="dialog" aria-expanded={openPopover === 'more'} aria-controls="sc-codex-more-popover" onClick={() => setOpenPopover((current) => current === 'more' ? null : 'more')}><MoreHorizontal size={20} /></button>
                    <div id="sc-codex-more-popover" role="dialog" aria-label="输入选项" className="sc-codex-popover sc-codex-more-popover" hidden={openPopover !== 'more'}>
                      <header><strong>输入选项</strong><button type="button" className="sc-codex-icon-button" aria-label="关闭输入选项" onClick={() => { setOpenPopover(null); moreTriggerRef.current?.focus(); }}><X size={14} /></button></header>
                      <button type="button" className="sc-codex-menu-action" aria-label={expandedInput ? '收起输入框' : '展开输入框'} onClick={() => { setExpandedInput((previous) => !previous); setOpenPopover(null); inputRef.current?.focus(); }}>{expandedInput ? <Minimize2 size={16} /> : <Maximize2 size={16} />}<span>{expandedInput ? '收起输入框' : '展开输入框'}</span></button>
                      <label className="sc-codex-setting-field"><span>发送快捷键</span><select aria-label="发送快捷键" value={shortcut} onChange={(event) => { const value = event.target.value as 'enter' | 'ctrlEnter'; setShortcut(value); try { localStorage.setItem('sciver-chat-send-shortcut', value); } catch { /* current page preference */ } }}><option value="enter">Enter 发送</option><option value="ctrlEnter">Ctrl / ⌘ + Enter 发送</option></select></label>
                      <button type="button" className="sc-codex-menu-action" aria-pressed={notificationsEnabled} aria-label={notificationsEnabled ? '关闭回复提醒' : '启用回复提醒'} onClick={() => void toggleNotifications()}><Bell size={16} /><span>{notificationsEnabled ? '关闭回复提醒' : '回复完成时通知我'}</span></button>
                      <p>Shift + Enter 换行</p><p className="sc-codex-file-help">图片、PDF、DOCX、Markdown、TXT、CSV<br />单个文件 ≤ 20 MB · 每条最多 10 个</p>
                    </div>
                  </div>
                </div>
                <div className="tm-aui-composer-actions sc-codex-composer-right">
                  {isRunning && <label className="sc-codex-send-mode"><select className="sc-send-mode" aria-label="生成期间发送方式" value={sendMode} onChange={(event) => setSendMode(event.target.value as 'queue' | 'steer')}><option value="queue">排队发送</option><option value="steer">即时补充</option></select><ChevronDown size={11} aria-hidden="true" /></label>}
                  <div className="sc-codex-popover-anchor" ref={modelPopoverRef}>
                    <button ref={modelTriggerRef} type="button" className="sc-codex-model-trigger" aria-label="模型与推理强度" title={`${selectedModel} · ${effortLabels[selectedEffort] || selectedEffort}`} aria-haspopup="dialog" aria-expanded={openPopover === 'model'} aria-controls="sc-codex-model-popover" onClick={() => setOpenPopover((current) => current === 'model' ? null : 'model')}><span className="sc-codex-model-name">{selectedModel.replace(/^gpt-/i, 'GPT-').replace(/-(sol|luna)$/i, (_, family: string) => ' ' + family[0].toUpperCase() + family.slice(1))}</span><span className="sc-codex-model-effort">{effortLabels[selectedEffort] || selectedEffort}</span><ChevronDown size={13} aria-hidden="true" /></button>
                    <div id="sc-codex-model-popover" role="dialog" aria-label="模型与推理设置" className="sc-codex-popover sc-codex-model-popover" hidden={openPopover !== 'model'}>
                      <header><strong>模型与推理</strong><button type="button" className="sc-codex-icon-button" aria-label="关闭模型设置" onClick={() => { setOpenPopover(null); modelTriggerRef.current?.focus(); }}><X size={14} /></button></header>
                      <label className="tm-aui-model-picker sc-codex-setting-field"><span>模型</span><select ref={modelSelectRef} aria-label="模型" value={selectedModel} disabled={isRunning || settingsBusy} title={capabilityHint} aria-describedby="tm-aui-model-catalog-status" onChange={(event) => void onModelChange(event.target.value)}>{choicesWithCurrent(availableModels, selectedModel).map((model) => <option key={model} value={model}>{model}{availableModels.includes(model) ? '' : '（当前）'}</option>)}</select></label>
                      <label className="tm-aui-effort-picker sc-codex-setting-field"><span>推理强度</span><select ref={effortSelectRef} aria-label="推理强度" value={selectedEffort} disabled={isRunning || settingsBusy} title={capabilityHint} aria-describedby="tm-aui-model-catalog-status" onChange={(event) => void onEffortChange(event.target.value)}>{choicesWithCurrent(availableEfforts, selectedEffort).map((effort) => <option key={effort} value={effort}>{effortLabels[effort] || effort}{availableEfforts.includes(effort) ? '' : '（当前）'}</option>)}</select></label>
                      <p>{isRunning ? '回复完成后可调整模型与推理强度。' : '设置用于接下来的回复。'}</p>
                      <span id="tm-aui-model-catalog-status" className="tm-aui-sr-only" role="status" aria-live="polite">{capabilityHint}</span>
                    </div>
                  </div>
                  {isRunning && <button type="button" className="tm-aui-stop sc-codex-stop" aria-label="停止生成" title="停止生成" onClick={() => void onCancel()}><Square size={14} fill="currentColor" /></button>}
                  <ComposerSubmitControl running={isRunning} sendMode={sendMode} hasAttachments={attachments.ids.length > 0} disabled={settingsBusy || queueBusy || attachments.blocked || connection === 'offline'} onSend={() => void submitDraft()} />
                </div>
              </div>}

            </ComposerPrimitive.Root>
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
        {error && <div className="tm-aui-error" role="alert">{error}<button type="button" className="sc-dismiss-error" aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
        {!isSupervisor && <AttachmentPreview attachment={attachments.preview} onClose={() => attachments.setPreview(null)} />}
      </div>
      </ChatActionsContext.Provider>
    </AssistantRuntimeProvider>
  );
}

const mount = document.getElementById('tm-research-chat-app');
if (mount) {
  const sessionId = mount.dataset.sessionId || '';
  const mode = mount.dataset.mode || 'research';
  if (sessionId) createRoot(mount).render(<App sessionId={sessionId} mode={mode} />);
}

const literatureReport = document.querySelector<HTMLElement>('[data-literature-report]');
const literatureReportData = document.getElementById('tm-literature-report-json');
if (literatureReport && literatureReportData) {
  try {
    const payload: unknown = JSON.parse(literatureReportData.textContent || '{}');
    if (payload && typeof payload === 'object' && 'text' in payload && typeof payload.text === 'string') {
      createRoot(literatureReport).render(<RichMarkdown text={payload.text} taskId="" />);
    }
  } catch { /* The escaped server-rendered report remains readable. */ }
}

const newModelSelect = document.querySelector<HTMLSelectElement>('[data-new-chat-model]');
const newEffortSelect = document.querySelector<HTMLSelectElement>('[data-new-chat-effort]');
if (newModelSelect && newEffortSelect && document.querySelector<HTMLElement>('.tm-research-chat')?.dataset.mode !== 'supervisor') {
  let catalog: ModelCatalog = { ...initialCatalog(),
    available_models: Array.from(newModelSelect.options).map((option) => option.value),
    available_reasoning_efforts: Array.from(newEffortSelect.options).map((option) => option.value),
  };
  const status = document.createElement('span');
  status.id = 'tm-new-chat-model-catalog-status';
  status.className = 'tm-research-sr-only';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  newModelSelect.parentElement?.append(status);
  newModelSelect.setAttribute('aria-describedby', status.id);
  newEffortSelect.setAttribute('aria-describedby', status.id);
  const render = () => {
    updateSelect(newModelSelect, catalog.available_models);
    updateSelect(newEffortSelect, effortsForModel(catalog, newModelSelect.value), effortLabels);
    const hint = catalogHint(catalog, newModelSelect.value, newEffortSelect.value);
    newModelSelect.title = hint;
    newEffortSelect.title = hint;
    status.textContent = hint;
  };
  const refresh = createCatalogRefresher(
    () => getJSON<Partial<ModelCatalog>>('/api/research-chat/models?refresh=1'),
    (value) => { catalog = mergeCatalog(catalog, value); render(); },
    () => { catalog = { ...catalog, model_catalog: { ...catalog.model_catalog, status: 'unavailable', stale: true } }; render(); },
  );
  newModelSelect.addEventListener('change', () => {
    const effort = effortAfterModelChange(catalog, newModelSelect.value, newEffortSelect.value);
    updateSelect(newEffortSelect, effortsForModel(catalog, newModelSelect.value), effortLabels);
    newEffortSelect.value = effort;
    try { window.localStorage.setItem('tm-research-chat-new-effort-v1', effort); } catch { /* Browser storage may be unavailable. */ }
    render();
  }, true);
  newEffortSelect.addEventListener('change', render);
  render();
  watchCatalogRefresh(refresh, [newModelSelect, newEffortSelect]);
}
