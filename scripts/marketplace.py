"""Check Marketplace publishing permission and publish with Entra (never a PAT).

Requires an existing Azure CLI login, MARKETPLACE_AZURE_TENANT_ID, and
MARKETPLACE_PROFILE_ID. No cloud resources or publisher memberships are mutated.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID
from zipfile import BadZipFile, ZipFile

PUBLISHER = "AgentOpsAccelerator"
EXTENSION = "agentops-accelerator"
RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
MIN_VSCE = (3, 9, 2)
PROFILE_URL = "https://app.vssps.visualstudio.com/_apis/profile/profiles/me?api-version=7.1"
ROLES_URL = (
    "https://marketplace.visualstudio.com/_apis/securityroles/scopes/"
    f"gallery.publisher/roleassignments/resources/{PUBLISHER}?api-version=7.1-preview.1"
)
PUBLISHING_ROLES = {"Contributor", "Owner", "Creator"}


class MarketplaceError(Exception):
    """An actionable publishing error, safe to print without credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an Authorization header to a redirected endpoint.
        return None


def required_guid(name: str) -> str:
    value = os.environ.get(name, "").strip()
    try:
        return str(UUID(value))
    except ValueError:
        raise MarketplaceError(
            f"Set {name} to the approved identity's GUID. See docs/release-process.md."
        ) from None


def publishing_environment(tenant: str) -> dict[str, str]:
    env = os.environ.copy()
    # vsce tries EnvironmentCredential before AzureCliCredential. Prevent a
    # developer's unrelated app credentials (or old PAT) from winning that chain.
    for name in (
        "VSCE_PAT",
        "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_CERTIFICATE_PATH",
        "AZURE_CLIENT_CERTIFICATE_PASSWORD",
        "AZURE_USERNAME",
        "AZURE_PASSWORD",
        "AZURE_CLIENT_ID",
    ):
        env.pop(name, None)
    env["AZURE_TENANT_ID"] = tenant
    return env


def executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MarketplaceError(f"{name} is required for Marketplace publishing; install it first.")
    return path


def mask(value: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::add-mask::{escaped}", flush=True)


def get_token(tenant: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [
                executable("az"), "account", "get-access-token",
                "--tenant", tenant, "--resource", RESOURCE,
                "--query", "accessToken", "--output", "tsv", "--only-show-errors",
            ],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MarketplaceError("Azure CLI token acquisition failed or timed out.") from None
    if result.returncode or not result.stdout.strip():
        raise MarketplaceError(
            "Azure CLI could not obtain a Marketplace token. Check the OIDC login and "
            "tenant, or run az login --tenant <publisher-tenant> --allow-no-subscriptions."
        )
    token = result.stdout.strip()
    mask(token)
    return token


def get_json(url: str, authorization: str) -> dict:
    request = Request(url, headers={"Authorization": authorization, "Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise MarketplaceError(
            f"Marketplace permission check returned HTTP {error.code}. "
            "Check the identity tenant and ask a publisher Owner to grant Contributor access."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise MarketplaceError("Marketplace permission check failed: network error or timeout.") from None
    except (ValueError, UnicodeError):
        raise MarketplaceError("Marketplace permission check returned invalid JSON.") from None
    if not isinstance(payload, dict):
        raise MarketplaceError("Marketplace permission check returned an unexpected response.")
    return payload


def publishing_role(profile: dict, assignments: dict, expected_profile: str) -> str:
    profile_id = profile.get("id")
    if not isinstance(profile_id, str) or profile_id.lower() != expected_profile.lower():
        raise MarketplaceError(
            "Marketplace identity mismatch. The Azure CLI login does not match "
            "MARKETPLACE_PROFILE_ID; check the selected account and tenant."
        )
    values = assignments.get("value")
    if not isinstance(values, list):
        raise MarketplaceError("Marketplace returned an invalid role-assignment list.")
    roles = set()
    for assignment in values:
        if not isinstance(assignment, dict):
            raise MarketplaceError("Marketplace returned an invalid role assignment.")
        user = assignment.get("user")
        if not isinstance(user, dict):
            raise MarketplaceError("Marketplace returned a role assignment without a user.")
        user_id = user.get("id")
        if not isinstance(user_id, str) or user_id.lower() != profile_id.lower():
            continue
        role = assignment.get("role")
        if not isinstance(role, dict) or type(role.get("denyPermissions")) is not int:
            raise MarketplaceError("Marketplace returned invalid permission details for this identity.")
        if role["denyPermissions"] != 0:
            raise MarketplaceError("The Marketplace identity has denied permissions; ask a publisher Owner.")
        name = role.get("name")
        if isinstance(name, str) and name in PUBLISHING_ROLES:
            roles.add(name)
    if not roles:
        raise MarketplaceError(
            f"No explicit publishing role for this identity on {PUBLISHER}. "
            "Contributor (or Owner/Creator) is required; Reader access is not sufficient."
        )
    return min(roles)


def preflight(tenant: str, expected_profile: str, env: dict[str, str]) -> tuple[str, str]:
    token = get_token(tenant, env)
    # Match vsce's Basic OAuth convention, rather than testing only Bearer access.
    basic = base64.b64encode(f"OAuth:{token}".encode()).decode("ascii")
    mask(basic)
    authorization = f"Basic {basic}"
    profile = get_json(PROFILE_URL, authorization)
    assignments = get_json(ROLES_URL, authorization)
    role = publishing_role(profile, assignments, expected_profile)
    print(f"Marketplace preflight passed: {PUBLISHER}, explicit {role} role. No upload performed.")
    return token, basic


def validate_package(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read("extension/package.json"))
    except (OSError, BadZipFile, KeyError, ValueError, UnicodeError):
        raise MarketplaceError("Cannot read extension/package.json from the VSIX package.") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("publisher") != PUBLISHER
        or manifest.get("name") != EXTENSION
    ):
        raise MarketplaceError(f"The VSIX must contain {PUBLISHER}.{EXTENSION}.")


def publish(
    path: Path, pre_release: bool, allow_already_exists: bool,
    tenant: str, expected_profile: str, env: dict[str, str],
) -> int:
    validate_package(path)
    vsce = executable("vsce")
    try:
        version = subprocess.run(
            [vsce, "--version"], env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MarketplaceError("Could not determine vsce version.") from None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.stdout.strip())
    if version.returncode or not match or tuple(map(int, match.groups())) < MIN_VSCE:
        raise MarketplaceError("Install @vscode/vsce@3.9.2 or newer for Entra publishing.")
    token, basic = preflight(tenant, expected_profile, env)
    command = [vsce, "publish", "--azure-credential", "--packagePath", str(path)]
    if pre_release:
        command.append("--pre-release")
    if allow_already_exists:
        # vsce handles existing versions and HTTP 409 specifically. Do not
        # suppress authentication/network failures by matching arbitrary output.
        command.append("--skip-duplicate")
    try:
        result = subprocess.run(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        raise MarketplaceError("Could not start vsce publishing.") from None
    output = result.stdout.replace(token, "***").replace(basic, "***")
    print(output, end="" if output.endswith("\n") else "\n")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check the selected identity and publishing role; never upload.")
    upload = commands.add_parser("publish", help="Check permission, then publish an existing VSIX.")
    upload.add_argument("--package-path", type=Path, required=True)
    upload.add_argument("--pre-release", action="store_true")
    upload.add_argument("--allow-already-exists", action="store_true")
    args = parser.parse_args(argv)
    try:
        tenant = required_guid("MARKETPLACE_AZURE_TENANT_ID")
        profile = required_guid("MARKETPLACE_PROFILE_ID")
        env = publishing_environment(tenant)
        if args.command == "check":
            preflight(tenant, profile, env)
            return 0
        return publish(
            args.package_path, args.pre_release, args.allow_already_exists, tenant, profile, env,
        )
    except MarketplaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
