type Composer = {
  getState(): { text: string };
  setText(text: string): void;
  subscribe(callback: () => void): () => void;
};
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

// Persist actual composer changes, so an inactive tab cannot overwrite a newer draft.
export function bindComposerDraft(
  composer: Composer, storage: StorageLike, legacyStorage: StorageLike,
  sessionId: string, mode: string,
): () => void {
  const key = `sciver-chat-composer-v2:${mode}:${sessionId}`;
  const legacyKey = `sciver-chat-composer-v1:${sessionId}`;
  try {
    let draft = storage.getItem(key);
    if (draft === null) {
      draft = legacyStorage.getItem(legacyKey);
      if (draft) storage.setItem(key, draft);
    }
    legacyStorage.removeItem(legacyKey);
    if (draft && !composer.getState().text) composer.setText(draft);
  } catch { /* Storage restrictions must not prevent composing. */ }
  let previous = composer.getState().text;
  return composer.subscribe(() => {
    const text = composer.getState().text;
    if (text === previous) return;
    previous = text;
    try {
      if (text) storage.setItem(key, text);
      else storage.removeItem(key);
    } catch { /* Keep the active composer usable. */ }
  });
}
