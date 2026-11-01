/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    const api = process.env.CLIPOS_API_INTERNAL_URL || "http://localhost:8000";
    return [
      { source: "/healthz", destination: `${api}/healthz` },
      { source: "/readyz", destination: `${api}/readyz` },
      { source: "/api/:path*", destination: `${api}/api/:path*` }
    ];
  }
};

export default nextConfig;
