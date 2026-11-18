type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function workspaceStorageKey(key: string, scope?: string): string {
  const workspace = scope ?? (typeof document === 'undefined' ? ''
    : document.querySelector<HTMLMetaElement>('meta[name="sciver-workspace"]')?.content || '');
  return workspace ? `sciver-workspace:${workspace}:${key}` : key;
}

export function workspaceStorage(kind: 'localStorage' | 'sessionStorage'): StorageLike {
  return {
    getItem: key => window[kind].getItem(workspaceStorageKey(key)),
    setItem: (key, value) => window[kind].setItem(workspaceStorageKey(key), value),
    removeItem: key => window[kind].removeItem(workspaceStorageKey(key)),
  };
}
