import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: '/research-chat/assets/',
  plugins: [react()],
  build: {
    outDir: resolve(here, '../tm_observer/research_chat_dist'),
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: 'research-chat.js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: 'research-chat.[ext]',
      },
    },
  },
});
