"""
Monday.com Writer Component main class.
"""
import logging
from datetime import datetime, UTC

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException

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

    @sync_action("fetch_monday_columns")
    def fetch_monday_columns(self):
        """
        Fetch available columns for the given Monday.com board.
        Returned as [{"label": "Column Name", "value": "column_id"}]
        """
        board_id = (
            self.configuration.parameters.get("sync_options", {}).get("board_id")
            or None
        )

        if not board_id:
            raise UserException("Please Specify Board ID before fetching Monday.com columns")

        api_client = ApiClient(Configuration(**{"parameters": self.configuration.parameters}), self)
        result = api_client.client.boards.fetch_columns_by_board_id(board_id=board_id)

        options = [
            {"label": c.title, "value": c.id}
            for c in result.data.boards[0].columns
        ]

        options.insert(0, {"label": "Unique field", "value": "__item_name__"})

        return {"type": "data", "data": options}

    @sync_action("fetch_boards_and_groups")
    def fetch_boards_and_groups(self):
        """
        Fetch all boards and their groups from Monday.com.
        Returns dropdown data for 'board_id' and 'group_id' fields.
        """
        from api_client import ApiClient
        from configuration import Configuration

        api_client = ApiClient(Configuration(**{"parameters": self.configuration.parameters}), self)
        monday_client = api_client.client

        all_boards = []
        cursor = None
        page = 1

        while True:
            logging.info(f"[Monday.com/Writer] Fetching boards (page {page})...")
            result = monday_client.boards.fetch_boards(cursor=cursor)
            boards_data = result.data.boards

            if not boards_data:
                break

            for board in boards_data:
                all_boards.append({
                    "id": str(board.id),
                    "name": board.name,
                    "groups": [{"id": g.id, "title": g.title} for g in board.groups or []]
                })

            cursor = result.data.page_info.end_cursor if hasattr(result.data, "page_info") else None
            if not cursor or not result.data.page_info.has_next_page:
                break

            page += 1

        board_options = [
            {
                "label": f"{board['name']} ({board['id']})",
                "value": board["id"]
            }
            for board in all_boards
        ]

        groups_by_board = {
            board["id"]: [
                {"label": f"{g['title']} ({g['id']})", "value": g["id"]}
                for g in board["groups"]
            ]
            for board in all_boards
        }

        return {
            "type": "data",
            "data": {
                "boards": board_options,
                "groups_by_board": groups_by_board
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
