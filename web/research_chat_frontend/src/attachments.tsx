import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
import { formatBytes, sameOriginAttachmentUrl, type ChatAttachment } from './chat-features';
import { attachmentDraftKey, validateAttachmentFiles } from './attachment-utils';
import { workspaceStorage } from './workspace-storage';
export { attachmentAccept } from './attachment-utils';

type UploadItem = { key: string; file?: File; attachment?: ChatAttachment; progress: number; status: 'uploading' | 'ready' | 'failed' | 'restoring'; error?: string };
const MAX_FILES = 10;
const localStorage = workspaceStorage('localStorage');

export function useAttachments(sessionId: string, enabled: boolean, onError: (message: string) => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [preview, setPreview] = useState<ChatAttachment | null>(null);
  const [dragging, setDragging] = useState(false);
  const requests = useRef(new Map<string, XMLHttpRequest>());
  const currentItems = useRef(items); currentItems.current = items;
  const base = `/api/research-chat/${encodeURIComponent(sessionId)}/attachments`;
  const restored = useRef(false);
  const onErrorRef = useRef(onError); onErrorRef.current = onError;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let ids: string[] = [];
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(attachmentDraftKey(sessionId)) || '[]');
      if (Array.isArray(saved)) ids = [...new Set(saved.filter((id): id is string => typeof id === 'string' && /^[a-f0-9]{32}$/.test(id)))].slice(0, MAX_FILES);
    } catch { /* A restricted browser can still attach files for this page. */ }
    setItems(ids.map((id) => ({ key: id, progress: 100, status: 'restoring' })));
    void Promise.all(ids.map(async (id): Promise<UploadItem> => {
      try {
        const response = await fetch(`${base}/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '附件已不可用，请重新添加。');
        const attachment = (data.attachment || data) as ChatAttachment;
        return { key: id, attachment, progress: 100, status: attachment.status === 'ready' ? 'ready' : 'failed', error: attachment.error };
      } catch (reason) { return { key: id, progress: 0, status: 'failed', error: reason instanceof Error ? reason.message : '无法恢复附件，请重新添加。' }; }
    })).then((values) => { if (!cancelled) { setItems((current) => [...values, ...current.filter((item) => !ids.includes(item.key))]); restored.current = true; } });
    return () => { cancelled = true; };
  }, [base, enabled, sessionId]);

  useEffect(() => {
    if (!enabled || !restored.current) return;
    try { localStorage.setItem(attachmentDraftKey(sessionId), JSON.stringify(items.flatMap((item) => item.attachment?.id ? [item.attachment.id] : []))); } catch { /* Uploads remain usable with storage blocked. */ }
  }, [enabled, items, sessionId]);
  useEffect(() => () => { for (const request of requests.current.values()) request.abort(); }, []);

  const upload = useCallback((file: File, key: string = crypto.randomUUID()) => {
    setItems((previous) => previous.some((item) => item.key === key)
      ? previous.map((item) => item.key === key ? { key, file, progress: 0, status: 'uploading' } : item)
      : [...previous, { key, file, progress: 0, status: 'uploading' }]);
    const request = new XMLHttpRequest(); requests.current.set(key, request);
    request.open('POST', base);
    request.withCredentials = true;
    request.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    request.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
    request.setRequestHeader('X-TM-Operator-Intent', '1');
    request.timeout = 180_000;
    request.upload.onprogress = (event) => { if (event.lengthComputable) setItems((previous) => previous.map((item) => item.key === key ? { ...item, progress: Math.min(99, Math.round(event.loaded / event.total * 100)) } : item)); };
    const failed = (message: string) => {
      requests.current.delete(key);
      setItems((previous) => previous.map((item) => item.key === key ? { ...item, status: 'failed', error: message } : item));
    };
    request.onload = () => {
      requests.current.delete(key);
      try {
        const data = JSON.parse(request.responseText);
        if (request.status < 200 || request.status >= 300) throw new Error(data.error || `上传失败 (${request.status})`);
        const attachment = data.attachment as ChatAttachment;
        if (!attachment?.id) throw new Error('服务器没有返回附件记录，请重试。');
        setItems((previous) => previous.map((item) => item.key === key ? { ...item, attachment, progress: 100, status: attachment.status === 'ready' ? 'ready' : 'failed', error: attachment.error } : item));
      } catch (reason) { failed(reason instanceof Error ? reason.message : '无法读取上传结果。'); }
    };
    request.onerror = () => failed('网络连接中断，附件尚未上传。可重试。');
    request.ontimeout = () => failed('上传超时，请重试。');
    request.onabort = () => requests.current.delete(key);
    request.send(file);
  }, [base]);

  const addFiles = useCallback((files: File[]) => {
    if (!enabled || !files.length) return;
    const error = validateAttachmentFiles(files, currentItems.current.length);
    if (error) { onErrorRef.current(error); return; }
    onErrorRef.current('');
    for (const file of files) upload(file);
  }, [enabled, upload]);
  const remove = (key: string) => { requests.current.get(key)?.abort(); setItems((previous) => previous.filter((item) => item.key !== key)); };
  const clearSubmitted = (ids: string[]) => setItems((previous) => previous.filter((item) => !ids.includes(item.attachment?.id || '')));
  const onPaste = (event: ClipboardEvent) => {
    const files = Array.from(event.clipboardData.files);
    if (enabled && files.length) { event.preventDefault(); addFiles(files); }
  };
  const onDragOver = (event: DragEvent) => { if (enabled && event.dataTransfer.types.includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; setDragging(true); } };
  const onDrop = (event: DragEvent) => { if (enabled && event.dataTransfer.files.length) { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); } };
  return { items, preview, setPreview, dragging, setDragging, addFiles, remove,
    retry: (item: UploadItem) => item.file ? upload(item.file, item.key) : onErrorRef.current('请重新选择此文件后上传。'),
    ids: items.flatMap((item) => item.status === 'ready' && item.attachment?.id ? [item.attachment.id] : []),
    blocked: items.some((item) => item.status !== 'ready'), clearSubmitted, onPaste, onDragOver, onDrop };
}

export function DraftAttachments({ manager }: { manager: ReturnType<typeof useAttachments> }) {
  if (!manager.items.length) return null;
  return <div className="sc-attachment-list" aria-label="待发送附件">{manager.items.map((item) => <div className={`sc-attachment-card status-${item.status}`} key={item.key}>
    <button type="button" className="sc-attachment-name" disabled={!item.attachment} onClick={() => item.attachment && manager.setPreview(item.attachment)}>{item.attachment?.name || item.file?.name || '正在恢复附件…'}</button>
    <small>{formatBytes(item.attachment?.size || item.file?.size)} · {item.status === 'uploading' ? item.progress >= 99 ? '正在解析…' : `上传中 ${item.progress}%` : item.status === 'ready' ? '已就绪' : item.status === 'restoring' ? '恢复中' : '失败'}</small>
    {item.status === 'uploading' && <progress value={item.progress} max={100} aria-label="上传进度" />}
    {item.error && <span className="sc-attachment-error">{item.error}</span>}
    <div>{item.status === 'failed' && item.file && <button type="button" onClick={() => manager.retry(item)}>重试</button>}
      <button type="button" aria-label={`移除${item.attachment?.name || item.file?.name || '附件'}`} onClick={() => manager.remove(item.key)}>移除</button></div>
  </div>)}</div>;
}
export function MessageAttachments({ attachments, onPreview }: { attachments?: ChatAttachment[]; onPreview: (attachment: ChatAttachment) => void }) {
  if (!attachments?.length) return null;
  return <div className="sc-attachment-list sc-message-attachments">{attachments.map((attachment) => <div className="sc-attachment-card" key={attachment.id}>
    <button type="button" className="sc-attachment-name" onClick={() => onPreview(attachment)}>
      {attachment.kind === 'image' && sameOriginAttachmentUrl(attachment.preview_url || attachment.url) && <img src={sameOriginAttachmentUrl(attachment.preview_url || attachment.url)} alt="" loading="lazy" />}
      {attachment.name}
    </button><small>{formatBytes(attachment.size)}</small>
    {sameOriginAttachmentUrl(attachment.url) && <a href={`${attachment.url}${attachment.url?.includes('?') ? '&' : '?'}download=1`} download>下载</a>}
  </div>)}</div>;
}
export function AttachmentPreview({ attachment, onClose }: { attachment: ChatAttachment | null; onClose: () => void }) {
  const [showExtracted, setShowExtracted] = useState(false);
  useEffect(() => setShowExtracted(false), [attachment?.id]);
  useEffect(() => { if (!attachment) return; const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); }; document.addEventListener('keydown', close); return () => document.removeEventListener('keydown', close); }, [attachment, onClose]);
  if (!attachment) return null;
  const url = sameOriginAttachmentUrl(attachment.preview_url || attachment.url);
  const isImage = attachment.kind === 'image' || attachment.media_type?.startsWith('image/');
  return <div className="sc-preview-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}><section role="dialog" aria-modal="true" aria-label={attachment.name} className="sc-attachment-preview">
    <header><strong>{attachment.name}</strong>{attachment.media_type === 'application/pdf' && attachment.text_preview && <button type="button" onClick={() => setShowExtracted((previous) => !previous)}>{showExtracted ? '查看原始 PDF' : '查看提取文字'}</button>}{sameOriginAttachmentUrl(attachment.url) && <a href={`${attachment.url}${attachment.url?.includes('?') ? '&' : '?'}download=1`} download>下载原件</a>}<button type="button" onClick={onClose} autoFocus aria-label="关闭附件预览">关闭</button></header>
    {isImage && url ? <img className="sc-preview-image" src={url} alt={attachment.name} /> : attachment.media_type === 'application/pdf' && url && !showExtracted ? <iframe title={attachment.name} src={url} /> : attachment.text_preview ? <pre>{attachment.text_preview}</pre> : <p>{attachment.error || '此文件可下载后查看。'}</p>}
  </section></div>;
}
