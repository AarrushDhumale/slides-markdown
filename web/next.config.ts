import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Explain requests send slide images to Gemini; keep the server bundle lean otherwise.
  poweredByHeader: false,
};

export default nextConfig;
