import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ["@parallaxops/contracts"],
  // Built with webpack (`next build --webpack`): the pnpm virtual store lives outside the checkout
  // (see .npmrc) to keep it out of OneDrive, and Turbopack requires one root containing both.
  // `tsc` (TypeScript 7, run by `pnpm typecheck`) is the type gate; Next's built-in check needs the
  // JS compiler API, which the native TypeScript 7 package no longer provides.
  typescript: { ignoreBuildErrors: true },
};

export default config;
