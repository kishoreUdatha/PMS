import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          // Libraries change far less often than screens, so each gets its own
          // long-cached chunk instead of riding along in the entry bundle.
          manualChunks: {
            'vendor-react': ['react', 'react-dom', 'react-router-dom'],
            'vendor-query': ['@tanstack/react-query'],
            'vendor-icons': ['lucide-react'],
            'vendor-axios': ['axios'],
          },
        },
      },
    },
    server: {
      port: Number(env.VITE_PORT) || 5173,
      // Bind IPv4 as well as IPv6. Left to itself Vite listened only on
      // [::1], and a browser that resolves "localhost" to 127.0.0.1 got a
      // refused connection and an error page while curl (which picked ::1)
      // was perfectly happy — a confusing way to lose an afternoon.
      host: true,
      proxy: {
        // Forward API and flow calls to the gateway during local dev.
        '/api': env.VITE_GATEWAY_URL || 'http://localhost:8000',
        '/flows': env.VITE_GATEWAY_URL || 'http://localhost:8000',
        // The guest booking engine. In production the gateway serves both
        // this and the console from one origin, so /book/{code} is a plain
        // relative link. In dev they are two servers, and without this line
        // Vite's SPA fallback answers /book/559167 with the console's own
        // index.html -- a 200, the right-looking URL, and the wrong
        // application. Proxying it keeps the two environments honest rather
        // than teaching anybody a dev-only address.
        '/book': env.VITE_GATEWAY_URL || 'http://localhost:8000',
        // Media is deliberately NOT proxied here. The object store's URLs
        // are SigV4-presigned, and SigV4 signs the Host; routing them through
        // this dev proxy produced a 403 because the header did not arrive at
        // MinIO as it was signed. Media is served from the gateway's own
        // origin instead (MINIO_PUBLIC_ENDPOINT), which needs no proxy and is
        // what production does anyway. A proxy that returns 403 for every
        // image is worse than no proxy at all.
      },
    },
  }
})
