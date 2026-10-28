import json
import os
import re
import subprocess

from databricks.sdk import WorkspaceClient
from inquirer import Confirm, List, Password, Text, list_input, prompt
from rich.progress import Progress, SpinnerColumn, TextColumn


def loading(func, message: str = "Loading...", client: WorkspaceClient = None):
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.start()
        progress.add_task(description=message)
        if client is None:
            res = func()
        else:
            res = func(client)
        progress.stop()

    return res


def cloud_validation(client, cloud):
    cloud_type = None
    if "azure" in client.config.host:
        cloud_type = "azure"
    elif "gcp" in client.config.host:
        cloud_type = "gcp"
    else:
        cloud_type = "aws"

    if cloud_type == cloud:
        return False
    return True


def cloud_type(client: WorkspaceClient):
    if "azure" in client.config.host:
        return "azure"
    elif "gcp" in client.config.host:
        return "gcp"
    else:
        return "aws"


def databricks_command(command: str):
    return json.loads(
        subprocess.run(
            command.split(" "),
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def get_profiles():
    output = databricks_command("databricks auth profiles -o json")["profiles"]
    valid_profiles = []
    for p in output:
        if p["valid"] and "accounts" not in p["host"]:
            valid_profiles.append(p["name"])
    return valid_profiles


def get_catalogs(client: WorkspaceClient):
    if uc_enabled(client) is False:
        return []
    valid_catalogs = []
    for c in client.catalogs.list():
        if c.catalog_type is not None and c.catalog_type.value != "SYSTEM_CATALOG":
            valid_catalogs.append(c.name)
    return valid_catalogs


def get_warehouses(client: WorkspaceClient):
    valid_warehouses = []
    for w in client.warehouses.list():
        valid_warehouses.append({"name": w.name, "id": w.id})
    return valid_warehouses


def uc_enabled(client: WorkspaceClient):
    try:
        client.metastores.current()
        return True
    except:
        return False


def get_account_profiles():
    output = databricks_command("databricks auth profiles -o json")["profiles"]
    return [p["name"] for p in output if p["valid"] and "accounts" in p["host"]]


def read_user_refresh_token(profile: str, account_id: str):
    """Read the U2M refresh token cached by `databricks auth login` for an account profile."""
    # Force a refresh so the cached refresh token is the newest one.
    subprocess.run(["databricks", "auth", "token", "-p", profile], capture_output=True, text=True, check=True)
    cache_path = os.path.expanduser("~/.databricks/token-cache.json")
    if not os.path.exists(cache_path):
        raise RuntimeError(
            f"{cache_path} not found. Set 'auth_storage = plaintext' under [__settings__] in ~/.databrickscfg "
            f"and run 'databricks auth login -p {profile}' again."
        )
    with open(cache_path) as fp:
        tokens = json.load(fp)["tokens"]
    for key, value in tokens.items():
        if (key == profile or key.endswith(f"/oidc/accounts/{account_id}")) and value.get("refresh_token"):
            return value["refresh_token"]
    raise RuntimeError(f"No refresh token cached for profile '{profile}' / account {account_id}.")
