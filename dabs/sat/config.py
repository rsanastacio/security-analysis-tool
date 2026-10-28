import json
import os
import re
import subprocess

from databricks.sdk import WorkspaceClient
from inquirer import Confirm, List, Password, Text, list_input, prompt
from databricks.sdk.core import Config
from rich.progress import Progress, SpinnerColumn, TextColumn
from sat.utils import (
    cloud_type,
    cloud_validation,
    get_account_profiles,
    get_catalogs,
    get_profiles,
    get_warehouses,
    loading,
    read_user_refresh_token,
    uc_enabled,
)

USER_OAUTH_CLIENT_ID = "databricks-cli"
UNUSED_SP_PLACEHOLDER = "unused-user-auth"


def form():
    profile = list_input(
        message="Select profile",
        choices=loading(get_profiles, "Loading profiles..."),
    )
    client = WorkspaceClient(profile=profile)
    questions = [
        Text(
            name="account_id",
            message="Databricks Account ID",
            validate=lambda _, x: re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", x
            ),
        ),
        List(
            name="catalog",
            message="Select catalog",
            choices=loading(get_catalogs, client=client),
        ),
        Text(
            name="security_analysis_schema",
            message="Schema name for SAT",
            default="security_analysis",
        ),
        Confirm(
            name="enable_serverless",
            message="Run on serverless? [Only monitor current workspace]",
            default=True,
        ),
        List(
            name="warehouse",
            message="Select warehouse",
            choices=loading(get_warehouses, client=client),
        ),
    ]
    user_auth = [
        Confirm(
            name="use_user_auth",
            message="Authenticate as your nominal user instead of a service principal? [AWS/GCP, serverless only]",
            default=False,
            ignore=cloud_type(client) == "azure",
        ),
        List(
            name="account_profile",
            message="Select account-level CLI profile (databricks auth login --account-id ...)",
            choices=lambda _: loading(get_account_profiles, "Loading account profiles..."),
            ignore=lambda x: not x.get("use_user_auth"),
        ),
    ]
    proxies = [
        Confirm(
            name="use_proxy",
            message="Want to use a proxy?",
            default=False,
        ),
        Text(
            name="http",
            message="HTTP Proxy",
            ignore=lambda x: not x["use_proxy"],
            default="",
        ),
        Text(
            name="https",
            message="HTTPS Proxy",
            ignore=lambda x: not x["use_proxy"],
            default="",
        ),
    ]

    # Job Scheduling Configuration
    scheduling = [
        Text(
            name="driver_schedule",
            message="Driver job schedule (Quartz cron expression)",
            default="0 0 8 ? * Mon,Wed,Fri",
        ),
        Text(
            name="secrets_scanner_schedule", 
            message="Secrets scanner job schedule (Quartz cron expression)",
            default="0 0 8 ? * *",
        ),
        Text(
            name="job_timezone",
            message="Job schedule timezone (IANA timezone ID)",
            default="UTC",
        ),
    ]

    # Permissions Analysis Configuration
    brickhound = [
        Confirm(
            name="enable_brickhound",
            message="Deploy Permissions Analysis?",
            default=True,
            # BrickHound authenticates with its own SP-based SDK client.
            ignore=lambda x: x.get("use_user_auth"),
        ),
        Text(
            name="brickhound_schedule",
            message="Permissions Analysis schedule (Quartz cron expression)",
            default="0 0 2 ? * *",
            ignore=lambda x: not x.get("enable_brickhound", False) or x.get("use_user_auth"),
        ),
    ]

    questions = questions + user_auth + cloud_specific_questions(client) + proxies + scheduling + brickhound
    answers = prompt(questions)
    if answers.get("use_user_auth"):
        if not answers["enable_serverless"]:
            raise ValueError("Nominal user auth requires serverless: the run_as token only reaches the current workspace.")
        answers["enable_brickhound"] = False
    return client, answers, profile


def cloud_specific_questions(client: WorkspaceClient):
    azure = [
        Text(
            name="azure-tenant-id",
            message="Azure Tenant ID",
            ignore=cloud_validation(client, "azure"),
        ),
        Text(
            name="azure-subscription-id",
            message="Azure Subscription ID",
            ignore=cloud_validation(client, "azure"),
        ),
        Text(
            name="azure-client-id",
            message="Client ID",
            ignore=cloud_validation(client, "azure"),
        ),
        Password(
            name="azure-client-secret",
            message="Client Secret",
            ignore=cloud_validation(client, "azure"),
            echo="",
        ),
    ]
    gcp = [
        Text(
            name="gcp-client-id",
            message="Client ID",
            ignore=lambda x, skip=cloud_validation(client, "gcp"): skip or x.get("use_user_auth"),
        ),
        Password(
            name="gcp-client-secret",
            message="Client Secret",
            ignore=lambda x, skip=cloud_validation(client, "gcp"): skip or x.get("use_user_auth"),
            echo="",
        ),
    ]
    aws = [
        Text(
            name="aws-client-id",
            message="Client ID",
            ignore=lambda x, skip=cloud_validation(client, "aws"): skip or x.get("use_user_auth"),
        ),
        Password(
            name="aws-client-secret",
            message="Client Secret",
            ignore=lambda x, skip=cloud_validation(client, "aws"): skip or x.get("use_user_auth"),
            echo="",
        ),
    ]
    return aws + azure + gcp


def generate_secrets(client: WorkspaceClient, answers: dict, cloud_type: str):

    scope_name = "sat_scope"
    # Only create the scope if it doesn't already exist. Previous versions
    # deleted and recreated the scope on every install, which wiped ACLs —
    # including the READ ACL that Databricks Apps auto-creates when a
    # `secret` resource is bound to an app. That left re-installs with the
    # BrickHound app unable to resolve its `valueFrom` env vars and crashing
    # at startup. Overwriting individual keys with `put_secret` below
    # preserves existing ACLs.
    existing = {scope.name for scope in client.secrets.list_scopes()}
    if scope_name not in existing:
        client.secrets.create_scope(scope_name)

    client.secrets.put_secret(
        scope=scope_name,
        key="account-console-id",
        string_value=answers["account_id"],
    )
    client.secrets.put_secret(
        scope=scope_name,
        key="sql-warehouse-id",
        string_value=answers["warehouse"]["id"],
    )
    client.secrets.put_secret(
        scope=scope_name,
        key="analysis_schema_name",
        string_value=f'`{answers["catalog"]}`.{answers["security_analysis_schema"]}',
    )

    if answers["use_proxy"]:
        client.secrets.put_secret(
            scope=scope_name,
            key="proxies",
            string_value=json.dumps(
                {
                    "http": answers["http"],
                    "https": answers["https"],
                }
            ),
        )
    else:
        client.secrets.put_secret(
            scope=scope_name,
            key="proxies",
            string_value="{}",
        )

    if answers.get("use_user_auth"):
        generate_user_auth_secrets(client, answers, scope_name)
        return

    if cloud_type == "aws" or cloud_type == "gcp":
        client.secrets.put_secret(
            scope=scope_name,
            key="use-sp-auth",
            string_value=True,
        )

    for value in answers.keys():
        if cloud_type in value:
            client.secrets.put_secret(
                scope=scope_name,
                key=value.replace(f"{cloud_type}-", ""),
                string_value=answers[value],
            )


def generate_user_auth_secrets(client: WorkspaceClient, answers: dict, scope_name: str):
    profile_account_id = Config(profile=answers["account_profile"]).account_id
    if profile_account_id != answers["account_id"]:
        raise ValueError(
            f"Profile '{answers['account_profile']}' points to account {profile_account_id}, not {answers['account_id']}."
        )
    secrets = {
        "use-user-auth": "true",
        "use-sp-auth": "false",
        "user-oauth-client-id": USER_OAUTH_CLIENT_ID,
        "account-refresh-token": read_user_refresh_token(answers["account_profile"], answers["account_id"]),
        # Notebooks still read these keys unconditionally.
        "client-id": UNUSED_SP_PLACEHOLDER,
        "client-secret": UNUSED_SP_PLACEHOLDER,
    }
    for key, value in secrets.items():
        client.secrets.put_secret(scope=scope_name, key=key, string_value=value)
