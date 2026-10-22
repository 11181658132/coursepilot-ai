/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep explicit local acceptance runs separate from the user's running app.
  distDir: process.env.COURSEPILOT_ACCEPTANCE_MODE === "1" ? ".next-acceptance" : ".next",
  output: "standalone",
  outputFileTracingIncludes: {
    "/api/demo/sources/*": ["./demo-index.json", "./demo-knowledge/*.md"],
    "/api/demo/sources/*/file": ["./demo-index.json", "./demo-knowledge/*.md"],
  },
};

export default nextConfig;

