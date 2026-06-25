/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AG_API_BASE?: string;
  readonly VITE_AG_BUCKET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
