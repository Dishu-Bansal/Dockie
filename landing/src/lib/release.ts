import { useEffect, useState } from "react";
import {
  DEFAULT_SIZE_MB,
  DEFAULT_VERSION,
  DOWNLOAD_FALLBACK_URL,
  RELEASES_API_URL,
} from "../site";

export type LatestRelease = {
  tag: string;
  version: string;
  url: string;
  assetName: string;
  sizeLabel: string;
};

type GithubAsset = {
  name: string;
  size: number;
  browser_download_url: string;
};

type GithubRelease = {
  tag_name: string;
  assets: GithubAsset[];
};

// Shared, cached fetch: every consumer (nav, hero, download, footer) awaits
// the same in-flight promise, so the API is hit once per page load. Returns
// null when the repo has no published release (api returns 404) or no
// matching dockie_setup_*.exe asset; callers fall back to a known URL.
let releasePromise: Promise<LatestRelease | null> | null = null;

export function getLatestRelease(): Promise<LatestRelease | null> {
  releasePromise ??= (async () => {
    try {
      const res = await fetch(RELEASES_API_URL, {
        headers: { Accept: "application/vnd.github+json" },
      });
      if (!res.ok) return null;
      const json = (await res.json()) as GithubRelease;
      const asset = (json.assets ?? []).find((a) =>
        /^dockie_setup.*\.exe$/i.test(a.name),
      );
      if (!asset?.browser_download_url) return null;
      const tag = json.tag_name ?? "";
      return {
        tag,
        version: tag.replace(/^v/i, ""),
        url: asset.browser_download_url,
        assetName: asset.name,
        sizeLabel:
          asset.size > 0
            ? `~${Math.max(1, Math.round(asset.size / 1_048_576))} MB`
            : "",
      };
    } catch {
      return null;
    }
  })();
  return releasePromise;
}

/**
 * Latest-release state for the download CTAs. Renders with the fallback URL
 * immediately (so the button always works), then swaps to the live asset
 * once the API resolves.
 */
export function useLatestRelease() {
  const [release, setRelease] = useState<LatestRelease | null>(null);

  useEffect(() => {
    let active = true;
    getLatestRelease().then((r) => {
      if (active) setRelease(r);
    });
    return () => {
      active = false;
    };
  }, []);

  return {
    url: release?.url ?? DOWNLOAD_FALLBACK_URL,
    version: release?.version ?? DEFAULT_VERSION,
    sizeLabel: release?.sizeLabel ?? `~${DEFAULT_SIZE_MB} MB`,
    loaded: release !== null,
  };
}
