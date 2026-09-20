/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GATEWAY_URL?: string
  readonly VITE_PORT?: string
  readonly VITE_PROPERTY_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
