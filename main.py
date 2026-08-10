import re
import os
from datetime import datetime, timezone

import requests


def define_env(env):
    def _github_headers() -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "agentops-docs-mkdocs-macros",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _parse_published_at(value: str | None) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    @env.macro
    def latest_release(repo="Azure/agentops"):
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            r = requests.get(api_url, headers=_github_headers(), timeout=10)
            r.raise_for_status()
            tag = (r.json() or {}).get("tag_name")
            if tag:
                return tag
        except Exception:
            pass

        try:
            url = f"https://github.com/{repo}/releases/latest"
            r = requests.get(url, allow_redirects=True, timeout=10)
            if r.ok and "/releases/tag/" in r.url:
                return r.url.split("/")[-1]
        except Exception:
            pass

        return ""

    def _get_latest_prerelease(repo: str, prefer_rc: bool = True) -> dict | None:
        api_url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
        try:
            r = requests.get(api_url, headers=_github_headers(), timeout=10)
            r.raise_for_status()
            releases = r.json() or []
        except Exception:
            return None

        prereleases = [rel for rel in releases if rel.get("prerelease")]
        if not prereleases:
            return None

        if prefer_rc:
            rc_re = re.compile(r"(?:^|[-._])rc(?:[-._]|$)", re.IGNORECASE)
            rc_rels = []
            for rel in prereleases:
                tag = rel.get("tag_name", "")
                name = rel.get("name", "")
                if rc_re.search(tag) or rc_re.search(name):
                    rc_rels.append(rel)

            if rc_rels:
                return max(rc_rels, key=lambda x: _parse_published_at(x.get("published_at")))

        return max(prereleases, key=lambda x: _parse_published_at(x.get("published_at")))

    @env.macro
    def latest_release_candidate(repo: str = "Azure/agentops") -> str:
        rel = _get_latest_prerelease(repo)
        return (rel or {}).get("tag_name", "")

    @env.macro
    def latest_release_candidate_url(repo: str = "Azure/agentops") -> str:
        rel = _get_latest_prerelease(repo)
        return (rel or {}).get("html_url", f"https://github.com/{repo}/releases")
