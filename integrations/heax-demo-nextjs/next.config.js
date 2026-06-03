/** @type {import('next').NextConfig} */
const rawBasePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
// Normalize: must start with "/" and must not end with "/", or be empty.
let basePath = rawBasePath.trim();
if (basePath && !basePath.startsWith("/")) {
  basePath = "/" + basePath;
}
if (basePath.endsWith("/")) {
  basePath = basePath.replace(/\/+$/, "");
}

const nextConfig = {
  reactStrictMode: true,
  basePath: basePath || undefined,
  assetPrefix: basePath || undefined,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

module.exports = nextConfig;
