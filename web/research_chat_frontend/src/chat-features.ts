import type { ReadingSource } from './research-sources';

export type ChatAttachment = {
  id: string; name: string; media_type?: string; size?: number; status?: string;
  kind?: string; url?: string; preview_url?: string; text_preview?: string; error?: string;
};
export type ChatEvent = {
  id?: string; seq?: number; type?: string; kind?: string; status?: string; label?: string;
  text?: string; created_at?: string; started_at?: string; completed_at?: string;
  item_id?: string; [key: string]: unknown;
};
export type QuestionOption = { label: string; description?: string };
export type RunQuestion = {
  request_id?: string; id?: string; questions?: RunQuestion[]; question?: string;
  header?: string; options?: QuestionOption[]; isOther?: boolean; isSecret?: boolean;
};
export type RunDetails = {
  id?: string; status?: string; stage?: string; content?: string; error_type?: string;
  started_at?: string; completed_at?: string; duration_ms?: number;
  model?: string; reasoning_effort?: string; events?: ChatEvent[];
  usage?: Record<string, unknown>; context?: unknown; pending_questions?: RunQuestion[];
};
export type QueueEntry = {
  id: string; content: string; created_at?: string; attachment_ids?: string[];
  sources?: ReadingSource[]; attachments?: ChatAttachment[]; status?: string;
};
export type ChatBranch = { id: string; label?: string; created_at?: string; message_count?: number };
export type FeatureMessage = {
  id: string; role: 'assistant' | 'user' | 'system'; content: string; created_at?: string;
  sources?: ReadingSource[]; attachments?: ChatAttachment[]; artifacts?: ChatAttachment[]; attachment_ids?: string[];
  run?: RunDetails; run_id?: string;
};

export function formatLocalTime(value?: string): string {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date)
    : '';
}
export function elapsedMilliseconds(run: RunDetails | null | undefined, now = Date.now()): number | null {
  if (!run) return null;
  if (typeof run.duration_ms === 'number' && Number.isFinite(run.duration_ms)) return Math.max(0, run.duration_ms);
  const start = Date.parse(run.started_at || '');
  const end = run.completed_at ? Date.parse(run.completed_at) : now;
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null;
}
export function formatDuration(milliseconds: number | null): string {
  if (milliseconds === null) return '—';
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
}
export function formatBytes(value = 0): string {
  return value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;
}
export function quotedMessage(text: string): string {
  return `${text.trim().split('\n').map((line) => `> ${line}`).join('\n')}\n\n`;
}
export function matchingMessageIds(messages: FeatureMessage[], query: string): string[] {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return [];
  return messages.filter((message) => message.content.toLocaleLowerCase().includes(term)
    || message.attachments?.some((attachment) => attachment.name.toLocaleLowerCase().includes(term))).map((message) => message.id);
}
export function messageMarkdown(message: FeatureMessage): string {
  const details = [formatLocalTime(message.created_at), message.run?.model,
    message.run?.reasoning_effort, message.run ? `耗时 ${formatDuration(elapsedMilliseconds(message.run))}` : ''].filter(Boolean).join(' · ');
  const attachments = (message.attachments || []).map((attachment) => `- [${attachment.name}](${attachment.url || ''})`).join('\n');
  return `${details ? `<!-- ${details} -->\n\n` : ''}${message.content}${attachments ? `\n\n附件\n${attachments}` : ''}\n`;
}
export function downloadText(name: string, text: string, mime = 'text/markdown;charset=utf-8'): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function stageLabel(stage?: string): string {
  const labels: Record<string, string> = { pending: '待开始', in_progress: '进行中', started: '已开始', success: '已完成', failed: '失败', queued: '等待开始', preparing: '正在准备', starting: '正在连接模型', running: '正在处理', thinking: '正在思考', reasoning: '正在思考', tool: '正在使用工具', answering: '正在生成回答', responding: '正在生成回答', reconnecting: '正在重新连接', waiting_input: '等待你的回答', waiting_for_input: '等待你的回答', complete: '已完成', completed: '已完成', stopped: '已停止', interrupted: '已中断', error: '生成失败' };
  return labels[stage || ''] || stage || '正在处理';
}
export function eventLabel(event: ChatEvent): string {
  if (event.label) return event.label;
  const labels: Record<string, string> = { reasoning_summary: '思考摘要', reasoning: '思考摘要', tool: '工具活动', plan: '工作计划', message: '回复', usage: '用量', context: '上下文', reconnecting: '重新连接', question: '等待你的回答' };
  const name = typeof event.name === 'string' ? event.name : typeof event.tool_name === 'string' ? event.tool_name : '';
  return [labels[event.type || event.kind || ''] || '活动', name].filter(Boolean).join(' · ');
}
export function publicEvents(events: ChatEvent[] = []): ChatEvent[] {
  // Only server-published activity is rendered; raw/private reasoning never has a UI path.
  return events.filter((event) => !['message', 'usage', 'context'].includes(event.type || event.kind || '') && event.type !== 'reasoning_raw' && event.kind !== 'reasoning_raw');
}
export function sameOriginAttachmentUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value, location.origin); return url.origin === location.origin && /^\/api\/research-chat\//.test(url.pathname) ? url.href : undefined; } catch { return undefined; }
}
