"""Static IaC contract tests for the hosted Cockpit deployment template.

These tests never call `bicep build`, `az`, or `azd` — they statically parse
the template assets under `src/agentops/templates/cockpit-hosted/` as text
so they run anywhere (no Bicep CLI dependency) and fail loudly the moment the
template drifts from the allowlisted contract described in spec.md
(FR-054-FR-078): a fixed resource-type allowlist, secretless app settings,
mandatory Easy Auth with only `/healthz` anonymous, a Reader/Log Analytics
Reader-only role allowlist, deterministic role-assignment IDs, and no
telemetry/Foundry resource creation or mutation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import agentops


TEMPLATE_ROOT = Path(agentops.__file__).parent / "templates" / "cockpit-hosted"

BICEP_PATH = TEMPLATE_ROOT / "infra" / "main.bicep"
PARAMETERS_PATH = TEMPLATE_ROOT / "infra" / "main.parameters.json"
AZURE_YAML_PATH = TEMPLATE_ROOT / "azure.yaml"
MAIN_PY_PATH = TEMPLATE_ROOT / "app" / "main.py"
REQUIREMENTS_TMPL_PATH = TEMPLATE_ROOT / "app" / "requirements.txt.tmpl"

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Resource types this template is allowed to CREATE (declared without the
# `existing` keyword). Anything else appearing as a created resource is a
# contract violation (FR-054).
ALLOWED_CREATED_RESOURCE_TYPES = {
    "Microsoft.Web/serverfarms",
    "Microsoft.Web/sites",
    "Microsoft.Web/sites/config",
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.Authorization/roleAssignments",
}

# Resource types this template may only REFERENCE via `existing` (never
# create or mutate) so read-only role assignments can be scoped to them.
ALLOWED_EXISTING_RESOURCE_TYPES = {
    "Microsoft.CognitiveServices/accounts",
    "Microsoft.CognitiveServices/accounts/projects",
    "Microsoft.OperationalInsights/workspaces",
}

# Resource types that must never appear anywhere in the template, created or
# referenced, because creating/mutating them would violate the "no telemetry
# resource creation/mutation" and "no Foundry resource creation" constraints.
PROHIBITED_RESOURCE_TYPE_FRAGMENTS = (
    "Microsoft.Insights/components",  # Application Insights
    "Microsoft.Insights/diagnosticSettings",
    "Microsoft.Insights/metricAlerts",
    "Microsoft.Insights/scheduledQueryRules",
    "Microsoft.OperationalInsights/workspaces/tables",
    "Microsoft.OperationalInsights/workspaces/dataExports",
    "Microsoft.ApiManagement",
    "Microsoft.AlertsManagement",
)

READER_ROLE_DEFINITION_ID = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
LOG_ANALYTICS_READER_ROLE_DEFINITION_ID = "73c42c96-874c-492b-b04d-ab87d138a893"
# A role explicitly NOT allowed for this deployment (write-capable /
# broader-than-Reader roles on Log Analytics data) -- must never appear.
PROHIBITED_ROLE_DEFINITION_IDS = (
    "3b03c2da-16b3-4a49-8834-0f8130efdd3b",  # Log Analytics Data Reader
    "92aaf0da-9dab-42b6-94a3-d43ce8d16293",  # Log Analytics Contributor
    "b24988ac-6180-42a0-ab88-20f7382dd24c",  # Contributor
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",  # Owner
)

EXPECTED_APP_SETTING_NAMES = {
    "AGENTOPS_ATTRIBUTION_CONFIG",
    "AGENTOPS_COCKPIT_MODE",
    "AGENTOPS_COST_MODEL",
    "AGENTOPS_OBSERVE_SCOPE",
    "AGENTOPS_TENANT_ID",
    "AGENTOPS_APPLICATION_CLIENT_ID",
    "AGENTOPS_UAMI_CLIENT_ID",
    "AGENTOPS_ALLOWED_GROUP_OBJECT_ID",
    "AGENTOPS_VERSION",
    "SCM_DO_BUILD_DURING_DEPLOYMENT",
    "WEBSITES_PORT",
}

# Substrings that must never appear in an app setting *name* -- a secret,
# token, password, or connection string would never be a legitimate,
# non-secret, statically-known setting name (FR-060).
FORBIDDEN_SETTING_NAME_FRAGMENTS = (
    "SECRET",
    "PASSWORD",
    "CONNECTIONSTRING",
    "CONNECTION_STRING",
    "APIKEY",
    "API_KEY",
    "CREDENTIAL",
)

_RESOURCE_DECL_RE = re.compile(
    r"resource\s+\w+\s+'([\w./]+)@[\w.-]+'(\s+existing)?\s*="
)


@pytest.fixture(scope="module")
def bicep_text() -> str:
    return BICEP_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parameters_json() -> dict:
    return json.loads(PARAMETERS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def azure_yaml_doc() -> dict:
    return yaml.safe_load(AZURE_YAML_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def main_py_text() -> str:
    return MAIN_PY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def requirements_tmpl_text() -> str:
    return REQUIREMENTS_TMPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return PYPROJECT_PATH.read_text(encoding="utf-8")


def _appsettings_block(bicep_text: str) -> str:
    start = bicep_text.index("appSettings:")
    end = bicep_text.index("\n      ])", start)
    return bicep_text[start:end]


class TestTemplateFilesExist:
    def test_all_owned_template_files_exist(self) -> None:
        for path in (
            BICEP_PATH,
            PARAMETERS_PATH,
            AZURE_YAML_PATH,
            MAIN_PY_PATH,
            REQUIREMENTS_TMPL_PATH,
        ):
            assert path.is_file(), f"expected template asset at {path}"


class TestBicepResourceAllowlist:
    def test_created_resources_are_allowlisted(self, bicep_text: str) -> None:
        created_types = {
            match.group(1)
            for match in _RESOURCE_DECL_RE.finditer(bicep_text)
            if not match.group(2)
        }
        assert created_types, "expected at least one created resource in main.bicep"
        disallowed = created_types - ALLOWED_CREATED_RESOURCE_TYPES
        assert not disallowed, f"main.bicep creates disallowed resource types: {disallowed}"

    def test_existing_references_are_allowlisted(self, bicep_text: str) -> None:
        existing_types = {
            match.group(1)
            for match in _RESOURCE_DECL_RE.finditer(bicep_text)
            if match.group(2)
        }
        disallowed = existing_types - ALLOWED_EXISTING_RESOURCE_TYPES
        assert not disallowed, f"main.bicep references disallowed existing types: {disallowed}"

    def test_foundry_and_log_analytics_resources_are_never_created(
        self, bicep_text: str
    ) -> None:
        for match in _RESOURCE_DECL_RE.finditer(bicep_text):
            resource_type, existing_flag = match.group(1), match.group(2)
            if resource_type in ALLOWED_EXISTING_RESOURCE_TYPES:
                assert existing_flag, (
                    f"{resource_type} must only ever be declared with `existing`; "
                    "main.bicep must never create or mutate it"
                )

    def test_no_prohibited_telemetry_or_foundry_resource_types(
        self, bicep_text: str
    ) -> None:
        for fragment in PROHIBITED_RESOURCE_TYPE_FRAGMENTS:
            assert fragment not in bicep_text, (
                f"main.bicep must not reference prohibited resource type '{fragment}'"
            )

    def test_uami_is_dedicated_and_name_is_stable_across_reruns(
        self, bicep_text: str
    ) -> None:
        match = re.search(
            r"resource\s+uami\s+'Microsoft\.ManagedIdentity/userAssignedIdentities@[^']+'\s*=\s*\{\s*"
            r"name:\s*'([^']+)'",
            bicep_text,
        )
        assert match, "expected a single dedicated UAMI resource named deterministically"
        name_expr = match.group(1)
        # A stable name must be built only from the input parameter(s), never
        # from uniqueString()/utcNow()/guid() (which would create a new
        # identity -- and invalidate existing role assignments/FIC bindings
        # -- on every re-run).
        for volatile_fn in ("uniqueString(", "utcNow(", "guid(", "newGuid("):
            assert volatile_fn not in name_expr, (
                "UAMI name must be deterministic across re-runs, "
                f"found volatile expression '{volatile_fn}' in '{name_expr}'"
            )


class TestBicepAppSettingsAreSecretless:
    def test_app_settings_match_expected_allowlist_exactly(self, bicep_text: str) -> None:
        block = _appsettings_block(bicep_text)
        names = set(re.findall(r"name:\s*'([^']+)'", block))
        assert names == EXPECTED_APP_SETTING_NAMES, (
            f"unexpected app settings drift: {names.symmetric_difference(EXPECTED_APP_SETTING_NAMES)}"
        )

    def test_no_forbidden_setting_name_fragments(self, bicep_text: str) -> None:
        block = _appsettings_block(bicep_text)
        names = re.findall(r"name:\s*'([^']+)'", block)
        for name in names:
            upper = name.upper()
            for fragment in FORBIDDEN_SETTING_NAME_FRAGMENTS:
                assert fragment not in upper, (
                    f"app setting name '{name}' looks like a secret/credential holder"
                )

    def test_cost_model_setting_is_conditionally_omitted_when_empty(
        self, bicep_text: str
    ) -> None:
        block = _appsettings_block(bicep_text)
        assert "empty(agentopsCostModel) ? []" in block

    def test_attribution_setting_is_conditionally_omitted_when_empty(
        self, bicep_text: str
    ) -> None:
        block = _appsettings_block(bicep_text)
        assert "empty(agentopsAttributionConfig) ? []" in block
        assert "name: 'AGENTOPS_ATTRIBUTION_CONFIG'" in block

    def test_attribution_parameter_is_secure(self, bicep_text: str) -> None:
        assert re.search(
            r"@secure\(\)\s*param\s+agentopsAttributionConfig\s+string",
            bicep_text,
        )

    def test_no_secret_or_connection_string_style_values_anywhere(
        self, bicep_text: str
    ) -> None:
        lowered = bicep_text.lower()
        for forbidden in (
            "connectionstring",
            "connection_string",
            "sharedaccesskey",
            "accountkey",
            "clientsecret",
            "client_secret",
        ):
            assert forbidden not in lowered, (
                f"main.bicep must not reference '{forbidden}' -- settings must stay non-secret"
            )


class TestBicepAuthentication:
    def test_authsettingsv2_child_resource_present(self, bicep_text: str) -> None:
        assert re.search(
            r"resource\s+authSettings\s+'Microsoft\.Web/sites/config@[^']+'\s*=", bicep_text
        ), "expected an authsettingsV2 child resource on the Web App"
        assert "name: 'authsettingsV2'" in bicep_text

    def test_only_healthz_is_excluded_from_authentication(self, bicep_text: str) -> None:
        match = re.search(r"excludedPaths:\s*\[\s*([^\]]*)\]", bicep_text)
        assert match, "expected globalValidation.excludedPaths in authsettingsV2"
        paths = [p.strip().strip("'\"") for p in match.group(1).split(",") if p.strip()]
        assert paths == ["/healthz"], (
            f"only /healthz may be anonymous at the platform boundary, found: {paths}"
        )

    def test_authentication_is_required_and_single_tenant_aad(self, bicep_text: str) -> None:
        assert "requireAuthentication: true" in bicep_text
        assert "unauthenticatedClientAction: 'Return401'" in bicep_text
        assert "azureActiveDirectory: {" in bicep_text
        assert "enabled: true" in bicep_text
        # Single-tenant issuer must be built from the tenantId parameter, not
        # a multi-tenant "common"/"organizations"/"consumers" endpoint.
        issuer_match = re.search(r"openIdIssuer:\s*'([^']+)'", bicep_text)
        assert issuer_match, "expected an explicit openIdIssuer for single-tenant Easy Auth"
        issuer_expr = issuer_match.group(1)
        assert "tenantId" in issuer_expr
        for multi_tenant_marker in ("/common/", "/organizations/", "/consumers/"):
            assert multi_tenant_marker not in issuer_expr


class TestBicepRoleAssignments:
    def test_only_reader_and_log_analytics_reader_role_definitions_used(
        self, bicep_text: str
    ) -> None:
        role_guids = set(
            re.findall(
                r"roleDefinitions',\s*'([0-9a-fA-F-]{36})'",
                bicep_text,
            )
        )
        assert role_guids == {READER_ROLE_DEFINITION_ID, LOG_ANALYTICS_READER_ROLE_DEFINITION_ID}, (
            f"unexpected role definition GUIDs in main.bicep: {role_guids}"
        )

    def test_prohibited_role_definition_ids_absent(self, bicep_text: str) -> None:
        for prohibited_guid in PROHIBITED_ROLE_DEFINITION_IDS:
            assert prohibited_guid not in bicep_text, (
                f"prohibited (write-capable or overly broad) role GUID {prohibited_guid} "
                "must never appear in main.bicep"
            )

    def test_role_assignment_names_are_deterministic_guid_expressions(
        self, bicep_text: str
    ) -> None:
        role_assignment_blocks = re.findall(
            r"resource\s+\w+\s+'Microsoft\.Authorization/roleAssignments@[^']+'[^\n]*\n"
            r"(?:.*\n)*?\s*name:\s*([^\n]+)\n",
            bicep_text,
        )
        assert role_assignment_blocks, "expected at least one role assignment resource"
        for name_expr in role_assignment_blocks:
            assert name_expr.strip().startswith("guid("), (
                "role assignment names must be deterministic guid(...) expressions, "
                f"found: {name_expr.strip()}"
            )
            # A deterministic ID must depend on stable, already-scoped inputs
            # (the target resource and the UAMI) rather than a fresh random
            # seed such as utcNow()/newGuid(), which would create a new
            # (duplicate) assignment on every re-run.
            assert "utcNow(" not in name_expr and "newGuid(" not in name_expr

    def test_role_assignments_only_scoped_to_own_resource_group_or_same_rg_existing(
        self, bicep_text: str
    ) -> None:
        # Every role-assignment `scope:` must point at either the deployment's
        # own resource group or a symbol declared in this same file (never a
        # raw cross-subscription/cross-resource-group literal), consistent
        # with the single-file Bicep scoping constraint documented at the top
        # of main.bicep.
        scope_exprs = re.findall(r"scope:\s*([^\n,]+)", bicep_text)
        for expr in scope_exprs:
            expr = expr.strip().rstrip(",")
            assert "subscription(" not in expr or expr == "resourceGroup()", (
                f"unexpected subscription-scoped expression: {expr}"
            )


class TestParametersJson:
    def test_parameters_json_is_valid_and_matches_bicep_params(
        self, bicep_text: str, parameters_json: dict
    ) -> None:
        bicep_param_names = set(re.findall(r"^param\s+(\w+)\s", bicep_text, re.MULTILINE))
        json_param_names = set(parameters_json["parameters"].keys())
        assert bicep_param_names == json_param_names, (
            "main.parameters.json must declare exactly the parameters main.bicep expects; "
            f"diff: {bicep_param_names.symmetric_difference(json_param_names)}"
        )

    def test_parameters_json_has_deployment_parameters_schema(
        self, parameters_json: dict
    ) -> None:
        assert parameters_json["$schema"].endswith("deploymentParameters.json#")
        assert "contentVersion" in parameters_json

    def test_default_parameters_do_not_broaden_reader_scope(
        self, parameters_json: dict
    ) -> None:
        assert (
            parameters_json["parameters"]["grantReaderOnResourceGroup"]["value"]
            is False
        )

    def test_cost_model_parameter_is_optional_and_non_secret(
        self, parameters_json: dict
    ) -> None:
        assert (
            parameters_json["parameters"]["agentopsCostModel"]["value"]
            == "${AGENTOPS_COST_MODEL=}"
        )

    def test_attribution_parameter_is_optional_and_disabled_by_default(
        self, parameters_json: dict
    ) -> None:
        assert (
            parameters_json["parameters"]["agentopsAttributionConfig"]["value"]
            == "${AGENTOPS_ATTRIBUTION_CONFIG=}"
        )


class TestAzureYaml:
    def test_defines_appservice_hosted_service(self, azure_yaml_doc: dict) -> None:
        assert azure_yaml_doc["infra"]["provider"] == "bicep"
        assert azure_yaml_doc["infra"]["path"] == "infra"
        assert azure_yaml_doc["infra"]["module"] == "main"
        services = azure_yaml_doc["services"]
        assert len(services) == 1, "hosted Cockpit template must define exactly one service"
        (service_config,) = services.values()
        assert service_config["host"] == "appservice"
        assert service_config["language"] == "py"

    def test_does_not_declare_hooks_or_extra_resources(self, azure_yaml_doc: dict) -> None:
        assert "hooks" not in azure_yaml_doc
        assert "resources" not in azure_yaml_doc


class TestMainPy:
    def test_no_azure_or_agentops_import_at_module_top_level(self, main_py_text: str) -> None:
        module_top = main_py_text.split("def create_hosted_app", 1)[0]
        assert "import azure" not in module_top
        assert "from azure" not in module_top
        assert "from agentops" not in module_top, (
            "the agentops package import must be deferred (lazy) inside a function, "
            "not executed at module import time"
        )

    def test_create_app_import_is_lazy_inside_function(self, main_py_text: str) -> None:
        assert "from agentops.agent.cockpit import create_app" in main_py_text
        func_source = main_py_text.split("def create_hosted_app", 1)[1]
        assert "from agentops.agent.cockpit import create_app" in func_source

    def test_defaults_to_hosted_cockpit_mode(self, main_py_text: str) -> None:
        assert (
            'os.environ.setdefault("AGENTOPS_COCKPIT_MODE", "hosted")' in main_py_text
        )

    def test_exposes_app_module_attribute_for_gunicorn_uvicorn(
        self, main_py_text: str
    ) -> None:
        assert re.search(r"^app\s*=\s*create_hosted_app\(\)", main_py_text, re.MULTILINE)


class TestRequirementsTemplate:
    def test_contains_version_placeholder(self, requirements_tmpl_text: str) -> None:
        assert "__AGENTOPS_VERSION__" in requirements_tmpl_text
        assert "agentops-accelerator[agent]==__AGENTOPS_VERSION__" in requirements_tmpl_text

    def test_no_secret_like_content(self, requirements_tmpl_text: str) -> None:
        # Only inspect non-comment (actual requirement) lines -- explanatory
        # comments are allowed to mention "secret" in prose (e.g. "no
        # secrets belong here") without that being a contract violation.
        requirement_lines = "\n".join(
            line
            for line in requirements_tmpl_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ).lower()
        for forbidden in ("secret", "password", "token=", "connectionstring"):
            assert forbidden not in requirement_lines


class TestPyprojectRegistration:
    def test_cockpit_hosted_assets_registered_in_package_data(
        self, pyproject_text: str
    ) -> None:
        match = re.search(
            r'"agentops\.templates"\s*=\s*\[(.*?)\]', pyproject_text, re.DOTALL
        )
        assert match, 'expected "agentops.templates" package-data entry in pyproject.toml'
        block = match.group(1)
        for expected_entry in (
            "cockpit-hosted/azure.yaml",
            "cockpit-hosted/app/*",
            "cockpit-hosted/infra/*",
        ):
            assert expected_entry in block, (
                f"pyproject.toml package-data must register '{expected_entry}'"
            )
