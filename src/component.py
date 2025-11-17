"""
Monday.com Writer Component main class.
"""
import logging
from datetime import datetime, UTC

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

from configuration import Configuration
from api_client import ApiClient, MondayGraphQLClient
from utils import get_sapi_column_definition


class Component(ComponentBase):
    def __init__(self):
        super().__init__()

        raw_config = {
            "parameters": self.configuration.parameters,
            "action": getattr(self.configuration, "action", "run"),
        }
        self.params = Configuration(**raw_config)

        api_key = self._get_api_key()
        self.monday_client = MondayGraphQLClient(api_key)

    def _get_api_key(self) -> str:
        try:
            return self.configuration.parameters["authorization"]["#api_key"]
        except Exception:
            raise UserException("Missing Monday.com API token")

    def run(self):
        run_time = datetime.now(UTC)
        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        logging.info("[Monday.com/Writer]: Starting upsert process...")

        raw_config = {
            "parameters": self.configuration.parameters,
            "action": getattr(self.configuration, "action", "run"),
        }
        config = Configuration(**raw_config)

        client = ApiClient(config, self)
        client.upsert_all_tables()

        new_state = {"last_successful_run": run_time_str}
        logging.info("[Monday.com/Writer]: Saving component state...")
        self.write_state_file(new_state)

        logging.info("[Monday.com/Writer]: Upsert process completed successfully.")

    # -----------------------
    # Column normalization
    # -----------------------
    def _normalize_columns(self, cols):
        cleaned = []
        for c in cols:
            if isinstance(c, dict):
                cleaned.append(c.get("name") or c.get("source_name"))
            else:
                cleaned.append(c)
        return cleaned

    # -----------------------
    # Workspaces
    # -----------------------
    @sync_action("list_workspaces")
    def list_workspaces(self):
        client = self.monday_client

        query = """
        query {
          workspaces {
            id
            name
          }
        }
        """

        data = client.query(query)
        workspaces = data.get("workspaces", [])
        if not workspaces:
            raise UserException("No workspaces found for this token")

        return [SelectElement(ws["id"], ws["name"]) for ws in workspaces]

    # -----------------------
    # Boards
    # -----------------------
    @sync_action("list_boards")
    def list_boards(self):
        params = self.configuration.parameters
        workspace_id = params.get("sync_options", {}).get("workspace_id")
        if not workspace_id:
            raise UserException("Select a workspace first")

        client = self.monday_client

        query = """
        query ($workspace_ids: [ID!]) {
          boards(workspace_ids: $workspace_ids) {
            id
            name
          }
        }
        """

        data = client.query(query, {"workspace_ids": [workspace_id]})
        boards = data.get("boards", [])
        if not boards:
            raise UserException(f"No boards found in workspace {workspace_id}")

        return [SelectElement(b["id"], b["name"]) for b in boards]

    # -----------------------
    # Groups
    # -----------------------
    @sync_action("list_groups")
    def list_groups(self):
        params = self.configuration.parameters
        board_id = params.get("sync_options", {}).get("board_id")
        if not board_id:
            raise UserException("Select a board first")

        client = self.monday_client

        query = """
        query ($board_ids: [ID!]) {
          boards(ids: $board_ids) {
            id
            name
            groups { id title }
          }
        }
        """

        data = client.query(query, {"board_ids": [board_id]})
        boards = data.get("boards", [])
        if not boards or not boards[0].get("groups"):
            raise UserException(f"No groups found for board {board_id}")

        return [SelectElement(g["id"], g["title"]) for g in boards[0]["groups"]]

    # -----------------------
    # Monday columns
    # -----------------------
    @sync_action("list_monday_columns")
    def list_monday_columns(self):
        board_id = (self.configuration.parameters.get("sync_options", {}) or {}).get("board_id")
        if not board_id:
            raise UserException("Select a board first to load Monday columns.")

        client = self.monday_client

        query = """
        query ($board_ids: [ID!]) {
          boards(ids: $board_ids) {
            columns { id title type }
          }
        }
        """

        data = client.query(query, {"board_ids": [board_id]})
        boards = data.get("boards", [])
        if not boards or not boards[0].get("columns"):
            raise UserException(f"No columns found for board {board_id}.")

        return [
            SelectElement(c["id"], f"{c.get('title') or c['id']} ({c['id']})")
            for c in boards[0]["columns"]
        ]

    # -----------------------
    # Source columns
    # -----------------------
    @sync_action("list_source_columns")
    def list_source_columns(self):
        token = self.environment_variables.token
        url = self.environment_variables.url

        if not token:
            raise UserException("Storage API Token is missing.")

        mappings = self.configuration.tables_input_mapping
        if not mappings or len(mappings) != 1:
            raise UserException("Exactly one input table must be mapped.")

        table_id = mappings[0].source

        raw_cols = get_sapi_column_definition(table_id, url, token)
        cols = self._normalize_columns(raw_cols)

        return {
            "type": "data",
            "data": {
                "source_columns": cols
            }
        }


    # -----------------------
    # Generate mapping rows
    # -----------------------
    @sync_action("generate_field_mappings")
    def generate_field_mappings(self):
        token = self.environment_variables.token
        url = self.environment_variables.url

        if not token:
            raise UserException("Storage API Token is missing.")

        mappings = self.configuration.tables_input_mapping
        if not mappings or len(mappings) != 1:
            raise UserException("Exactly one input table must be mapped.")

        table_id = mappings[0].source

        raw_cols = get_sapi_column_definition(table_id, url, token)
        cols = self._normalize_columns(raw_cols)

        field_mappings = [{"source_column": c, "monday_column_id": ""} for c in cols]

        return {
            "type": "data",
            "data": {
                "source_columns": cols,
                "field_mappings": field_mappings
            }
        }


"""
Main entrypoint
"""
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
