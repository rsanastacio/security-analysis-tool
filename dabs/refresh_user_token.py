"""Re-push the nominal user's U2M refresh token to sat_scope after `databricks auth login`."""
import argparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from sat.config import USER_OAUTH_CLIENT_ID
from sat.utils import read_user_refresh_token

SCOPE_NAME = "sat_scope"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-profile", required=True, help="CLI profile of the workspace where SAT is deployed")
    parser.add_argument("--account-profile", required=True, help="Account-level CLI profile of the same user")
    args = parser.parse_args()

    account_id = Config(profile=args.account_profile).account_id
    client = WorkspaceClient(profile=args.workspace_profile)
    client.secrets.put_secret(
        scope=SCOPE_NAME,
        key="account-refresh-token",
        string_value=read_user_refresh_token(args.account_profile, account_id),
    )
    client.secrets.put_secret(scope=SCOPE_NAME, key="user-oauth-client-id", string_value=USER_OAUTH_CLIENT_ID)
    print(f"Refresh token for account {account_id} stored in {SCOPE_NAME} on {client.config.host}")


if __name__ == "__main__":
    main()
