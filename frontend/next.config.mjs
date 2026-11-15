import createNextIntlPlugin from "next-intl/plugin";
import { withPostHogConfig } from "@posthog/nextjs-config";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const isPostHogEnabled = Boolean(process.env.NEXT_PUBLIC_POSTHOG_KEY?.trim());
const posthogHost = (process.env.NEXT_PUBLIC_POSTHOG_HOST?.trim() || "https://eu.i.posthog.com").replace(/\/$/, "");
const posthogAssetsHost = posthogHost.replace(/^https:\/\/(us|eu)\.i\.posthog\.com$/, "https://$1-assets.i.posthog.com");

const posthogRewrites = isPostHogEnabled
  ? [
      { source: "/ingest/static/:path*", destination: `${posthogAssetsHost}/static/:path*` },
      { source: "/ingest/array/:path*", destination: `${posthogAssetsHost}/array/:path*` },
      { source: "/ingest/:path*", destination: `${posthogHost}/:path*` },
    ]
  : [];

/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.NEXT_BUILD_DIR || ".next",
  output: 'standalone',
  htmlLimitedBots: /.*/,
  skipTrailingSlashRedirect: isPostHogEnabled,
  typescript: {
    ignoreBuildErrors: false,
  },
  async rewrites() {
    return [
      {
        source: "/js/script.js",
        destination: "https://datafa.st/js/script.js",
      },
      {
        source: "/api/events",
        destination: "https://datafa.st/api/events",
      },
      ...posthogRewrites,
    ];
  },
};

const intlConfig = withNextIntl(nextConfig);

const posthogPersonalApiKey = process.env.POSTHOG_PERSONAL_API_KEY?.trim();
const posthogProjectId = process.env.POSTHOG_PROJECT_ID?.trim();

export default isPostHogEnabled && posthogPersonalApiKey && posthogProjectId
  ? withPostHogConfig(intlConfig, {
      personalApiKey: posthogPersonalApiKey,
      projectId: posthogProjectId,
      host: posthogHost,
    })
  : intlConfig;
