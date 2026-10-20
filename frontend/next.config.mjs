/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingIncludes: {
    "/api/demo/sources/*": ["./demo-index.json", "./demo-knowledge/*.md"],
    "/api/demo/sources/*/file": ["./demo-index.json", "./demo-knowledge/*.md"],
  },
};

export default nextConfig;

