// Shared site facts. The repo was renamed from FileFinder to Dockie; the
// old URL redirects, but the canonical name is used everywhere below.
export const APP_NAME = "Dockie";
export const GITHUB_REPO = "Dishu-Bansal/Dockie";

// Latest-release discovery: the page fetches this at runtime so the download
// buttons always resolve the newest dockie_setup_*.exe asset instead of a
// hardcoded versioned URL that goes stale on the next release.
export const RELEASES_API_URL = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;
export const RELEASES_URL = `https://github.com/${GITHUB_REPO}/releases`;
export const GITHUB_URL = `https://github.com/${GITHUB_REPO}`;

// Fallback while the API call is in flight, or when no release is published:
// today's known installer. The runtime fetch replaces this URL as soon as a
// release with a dockie_setup_*.exe asset is live.
export const DOWNLOAD_FALLBACK_URL =
  "https://github.com/Dishu-Bansal/Dockie/releases/latest/download/dockie_setup_v100.exe";
export const DEFAULT_VERSION = "1.0.0";
export const DEFAULT_SIZE_MB = "62";
export const DATA_DIR = "~/.dockie";
