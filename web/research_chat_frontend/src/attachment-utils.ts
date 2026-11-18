const MAX_FILES = 10;
const MAX_FILE_BYTES = 20_000_000;
const supportedNames = /\.(png|jpe?g|webp|gif|pdf|docx|md|markdown|txt|csv)$/i;
export const attachmentAccept = '.png,.jpg,.jpeg,.webp,.gif,.pdf,.docx,.md,.markdown,.txt,.csv';
export function attachmentDraftKey(sessionId: string): string { return `sciver-chat-attachments-v1:research:${sessionId}`; }
export function validateAttachmentFiles(files: Pick<File, 'name' | 'size'>[], existing = 0): string | null {
  if (existing + files.length > MAX_FILES) return '每条消息最多添加 10 个附件。';
  for (const file of files) {
    if (!supportedNames.test(file.name)) return `${file.name}：支持图片、PDF、DOCX、Markdown、TXT 和 CSV；请将旧版 Word 文档转换为 DOCX 或 PDF。`;
    if (file.size > MAX_FILE_BYTES) return `${file.name} 超过单个附件 20 MB 的限制。`;
    if (file.size === 0) return `${file.name} 是空文件。`;
  }
  return null;
}
