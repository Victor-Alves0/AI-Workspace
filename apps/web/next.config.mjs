/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  webpack: (config) => {
    // @xenova/transformers (Whisper no navegador) referencia deps só-de-Node
    // (onnxruntime-node, sharp) que não existem no bundle do browser — alias
    // para `false` evita que o webpack tente resolvê-las.
    config.resolve.alias = {
      ...config.resolve.alias,
      "onnxruntime-node$": false,
      sharp$: false,
    };
    return config;
  },
};

export default nextConfig;
