export function isGraphLink(href: string, base: string): boolean {
  try {
    const url = new URL(href, base);
    return url.origin === new URL(base).origin
      && (/^\/research-explorer\/?$/.test(url.pathname) || url.pathname === '/research-chat/open');
  } catch { return false; }
}

/** Keep local citations out of the website's raw filesystem-shaped URL space. */
export function chatLink(href: string, taskId: string, origin: string): string | null {
  if (!href || /^(?:javascript|data|vbscript):/i.test(href)) return null;
  let target = href;
  if (/^https?:\/\//i.test(href)) {
    const url = new URL(href);
    if (url.origin !== origin) return href;
    target = url.pathname + url.search + url.hash;
  }
  const local = /^file:/i.test(target)
    || /(?:\.md)(?::\d+(?::\d+)?)?(?:[?#]|$)/i.test(target)
    || /^(?:\.?\.?\/)*(?:notes|nodes|literature)\//.test(target)
    || /^\/(?:home|Users|tmp|var)\//.test(target)
    || /^[a-z]:[\\/]/i.test(target);
  if (!local) return href;
  if (!/^[a-f0-9]{16}$/.test(taskId)) return null;
  return '/research-chat/open?' + new URLSearchParams({ task_id: taskId, target });
}
