"""
Monday.com Writer Component main class.
"""
import logging
from datetime import datetime, UTC

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

from configuration import Configuration
from api_client import ApiClient
from utils import get_sapi_column_definition


class Component(ComponentBase):
    def __init__(self):
        super().__init__()

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

    @sync_action("return_monday_table_types")
    def return_column_data(self):
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
        columns = get_sapi_column_definition(
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

    @sync_action("list_boards")
    def list_boards(self):
        """Fetch available Monday.com boards for dropdown."""
        api_key = self.configuration.parameters.get("authorization", {}).get("#api_key")
        if not api_key:
            raise UserException("Please provide an API token first.")

        client = ApiClient(Configuration(**{"parameters": self.configuration.parameters}), self)
        boards = client.client.boards.fetch_boards().data.boards

        return [SelectElement(b.id, b.name) for b in boards]

    @sync_action("list_groups")
    def list_groups(self):
        """Fetch groups for the selected Monday.com board."""
        board_id = (
            self.configuration.parameters.get("sync_options", {}).get("board_id")
        )
        api_key = self.configuration.parameters.get("authorization", {}).get("#api_key")

        if not api_key:
            raise UserException("Please provide an API token first.")
        if not board_id:
            raise UserException("Please select a board before loading groups.")

        client = ApiClient(Configuration(**{"parameters": self.configuration.parameters}), self)
        result = client.client.boards.fetch_groups_by_board_id(board_id=board_id)

        return [SelectElement(g.id, g.title) for g in result.data.boards[0].groups]

    @sync_action("fetch_monday_columns")
    def fetch_monday_columns(self):
        board_id = (self.configuration.parameters.get("sync_options", {}) or {}).get("board_id")
        if not board_id:
            raise UserException("Select a board first to load Monday columns.")
        cfg = Configuration(**{"parameters": self.configuration.parameters})
        client = ApiClient(cfg, self).client
        res = client.boards.fetch_columns_by_board_id(board_id=str(board_id))
        cols = getattr(res, "data", None)
        options = [SelectElement(value=c.id, label=f"{c.title} ({c.id})") for c in cols.boards[0].columns]
        options.insert(0, SelectElement(value="__item_name__", label="__item_name__ (Item Name)"))

        return options

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
