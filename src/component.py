"""
Monday.com Writer Component

Main Keboola component for syncing data from Storage tables to Monday.com boards.
Provides UI sync actions for workspace/board/group selection and column mapping,
plus the main run action for executing the data sync.
"""

import logging
from datetime import datetime, UTC

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

from configuration import Configuration
from api_client import ApiClient, MondayGraphQLClient
from utils import get_storage_column_definition


class Component(ComponentBase):
    """
    Monday.com Writer component for Keboola.

    Handles:
    - Main sync execution (run method)
    - UI sync actions for configuration dropdowns
    - State management for tracking last successful run
    """

    def __init__(self):
        super().__init__()

    def _get_api_key(self) -> str:
        """
        Retrieve Monday.com API token from configuration.

        Returns:
            Monday.com API token string

        Raises:
            UserException: If API token is not configured
        """
        try:
            return self.configuration.parameters["authorization"]["#api_key"]
        except Exception:
            raise UserException("Missing Monday.com API token")

    def run(self):
        """
        Main execution method for syncing data to Monday.com.

        Workflow:
        1. Initialize configuration
        2. Create API client
        3. Process all input tables
        4. Save state with timestamp of successful run
        """
        run_timestamp = datetime.now(UTC)
        run_timestamp_string = run_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

        logging.info("[Monday.com/Writer]: Starting upsert process...")

        raw_configuration = {
            "parameters": self.configuration.parameters,
            "action": getattr(self.configuration, "action", "run"),
        }
        configuration = Configuration(**raw_configuration)

        api_client = ApiClient(configuration, self)
        api_client.upsert_all_tables()

        new_state = {"last_successful_run": run_timestamp_string}
        logging.info("[Monday.com/Writer]: Saving component state...")
        self.write_state_file(new_state)

        logging.info("[Monday.com/Writer]: Upsert process completed successfully.")

    @sync_action("return_monday_table_types")
    def return_column_data(self):
        """
        Return source table column names for field mapping UI.

        This sync action populates the source columns in the field mappings
        configuration table by reading the input table structure from Storage API.

        Returns:
            Dictionary with column names for UI rendering

        Raises:
            UserException: If Storage token missing or input table not configured
        """
        if not self.environment_variables.token:
            raise UserException(
                "Storage API Token is missing. Please enable 'Forward Token' in the Keboola Component Settings."
            )

        if not self.configuration.tables_input_mapping or len(self.configuration.tables_input_mapping) != 1:
            raise UserException(
                "Exactly one input table must be mapped in the configuration. "
                "Please add an input table mapping in the UI or configuration."
            )

        table_id = self.configuration.tables_input_mapping[0].source
        columns = get_storage_column_definition(
            table_id,
            self.environment_variables.url,
            self.environment_variables.token,
        )

        return {
            "type": "data",
            "data": {
                "field_mappings": columns
            }
        }

    @sync_action("list_workspaces")
    def list_workspaces(self):
        """
        List all accessible Monday.com workspaces for dropdown selection.

        Returns:
            List of SelectElement objects with workspace IDs and names

        Raises:
            UserException: If no workspaces found or API token invalid
        """
        api_token = self._get_api_key()
        graphql_client = MondayGraphQLClient(api_token)

        query = """
        query {
          workspaces {
            id
            name
          }
        }
        """

        data = graphql_client.query(query)
        workspaces = data.get("workspaces", [])
        if not workspaces:
            raise UserException("No workspaces found for this token")

        return [SelectElement(workspace["id"], workspace["name"]) for workspace in workspaces]

    @sync_action("list_boards")
    def list_boards(self):
        """
        List all boards within selected workspace for dropdown selection.

        Returns:
            List of SelectElement objects with board IDs and names

        Raises:
            UserException: If workspace not selected or no boards found
        """
        parameters = self.configuration.parameters
        workspace_id = parameters.get("sync_options", {}).get("workspace_id")
        if not workspace_id:
            raise UserException("Select a workspace first")

        api_token = self._get_api_key()
        graphql_client = MondayGraphQLClient(api_token)

        query = """
        query ($workspace_ids: [ID!]) {
          boards(workspace_ids: $workspace_ids) {
            id
            name
            workspace_id
          }
        }
        """

        data = graphql_client.query(query, {"workspace_ids": [workspace_id]})
        boards = data.get("boards", [])
        if not boards:
            raise UserException(f"No boards found in workspace {workspace_id}")

        return [SelectElement(board["id"], board["name"]) for board in boards]

    @sync_action("list_groups")
    def list_groups(self):
        """
        List all groups within selected board for dropdown selection.

        Returns:
            List of SelectElement objects with group IDs and titles

        Raises:
            UserException: If board not selected or no groups found
        """
        parameters = self.configuration.parameters
        board_id = parameters.get("sync_options", {}).get("board_id")
        if not board_id:
            raise UserException("Select a board first")

        api_token = self._get_api_key()
        graphql_client = MondayGraphQLClient(api_token)

        query = """
        query ($board_ids: [ID!]) {
          boards(ids: $board_ids) {
            id
            name
            groups {
              id
              title
            }
          }
        }
        """

        data = graphql_client.query(query, {"board_ids": [board_id]})
        boards = data.get("boards", [])
        if not boards or not boards[0].get("groups"):
            raise UserException(f"No groups found for board {board_id}")

        return [SelectElement(group["id"], group["title"]) for group in boards[0]["groups"]]

    @sync_action("list_monday_columns")
    def list_monday_columns(self):
        """
        List all columns in selected Monday.com board for mapping dropdown.

        Returns column options formatted as "Column Title (column_id)" for
        clarity in the field mapping UI.

        Returns:
            List of SelectElement objects with column IDs and formatted labels

        Raises:
            UserException: If board not selected or no columns found
        """
        board_id = (self.configuration.parameters.get("sync_options", {}) or {}).get("board_id")
        if not board_id:
            raise UserException("Select a board first to load Monday columns.")

        api_token = self._get_api_key()
        graphql_client = MondayGraphQLClient(api_token)

        query = """
        query ($board_ids: [ID!]) {
          boards(ids: $board_ids) {
            columns { id title type }
          }
        }
        """

        data = graphql_client.query(query, {"board_ids": [board_id]})
        boards = data.get("boards", [])
        if not boards or not boards[0].get("columns"):
            raise UserException(f"No columns found for board {board_id}.")

        column_options = []
        for column in boards[0]["columns"]:
            column_id = column.get("id")
            column_title = column.get("title") or column_id
            if column_id:
                column_options.append(
                    SelectElement(value=column_id, label=f"{column_title} ({column_id})")
                )

        if not column_options:
            raise UserException(f"No usable columns returned for board {board_id}.")

        return column_options

    @sync_action("list_source_columns")
    def list_source_columns(self):
        """
        List all columns in source input table for mapping dropdown.

        Fetches column names from the configured input table in Keboola Storage
        to populate the source column dropdown in the field mapping UI.

        Returns:
            List of SelectElement objects with column names

        Raises:
            UserException: If Storage token missing, table not configured, or column fetch fails
        """
        storage_token = self.environment_variables.token
        storage_url = self.environment_variables.url

        if not storage_token:
            raise UserException(
                "Storage API Token is missing. Enable 'Forward Token' in the Component settings."
            )

        if not self.configuration.tables_input_mapping or len(self.configuration.tables_input_mapping) != 1:
            raise UserException("Exactly one input table must be mapped in the configuration.")

        table_id = self.configuration.tables_input_mapping[0].source

        try:
            columns = get_storage_column_definition(table_id, storage_url, storage_token)
        except Exception as exception:
            raise UserException(f"Failed to fetch columns for table '{table_id}': {exception}")

        if not columns:
            raise UserException(f"No columns found in input table '{table_id}'.")

        return [SelectElement(column, column) for column in columns]


if __name__ == "__main__":
    """
    Component entry point.

    Configures logging and executes the component action (run or sync action).
    Exit codes:
    - 0: Success
    - 1: UserException (configuration or validation error)
    - 2: Unexpected exception (system error)
    """
    logging.basicConfig(level=logging.INFO)

    try:
        component = Component()
        component.execute_action()
    except UserException as user_exception:
        logging.exception(user_exception)
        exit(1)
    except Exception as unexpected_exception:
        logging.exception(unexpected_exception)
        exit(2)
