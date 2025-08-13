import os
import csv
import logging
from typing import Dict, List, Iterable

from keboola.component.exceptions import UserException
from monday_sdk import MondayClient as SDKMondayClient


class ApiClient:
    """
    Monday.com API client for Keboola integrations.
    Reads table inputs, maps columns, and performs batched upserts.
    """

    def __init__(self, config, component):
        self.config = config
        self.component = component
        self.data_dir = self.component.configuration.data_dir

        self.api_token = self.config.auth.api_key
        self.board_id = str(self.config.sync.board_id)
        self.batch_size = int(self.config.sync.batch_size)

        self.mapping_src_to_colid: Dict[str, str] = self.config.mappings
        self.unique_src, self.unique_col = self.config.unique

        self.group_id = (self.config.sync.group_id or "topics").strip()

        self.client = SDKMondayClient(token=self.api_token)
        logging.info("[Monday.com/Writer] Initialized")

    def scan_table_inputs(self) -> List[Dict[str, str]]:
        dir_path = os.path.join(self.data_dir, "in/tables")
        if not os.path.exists(dir_path):
            logging.info(f"[Monday.com/Writer] tables dir does not exist: {dir_path} → skipping.")
            return []

        files = []
        for filename in os.listdir(dir_path):
            if filename.endswith(".manifest"):
                continue
            full_path = os.path.join(dir_path, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(".csv"):
                files.append({"name": filename, "source": full_path})

        logging.info(f"[Monday.com/Writer] Found {len(files)} table CSV(s).")
        return files

    def _load_existing_index(self) -> Dict[str, str]:
        idx = {}
        items = self.client.boards.fetch_all_items_by_board_id(board_id=self.board_id)
        for it in items or []:
            if self.unique_col == "__item_name__":
                unique_val = (getattr(it, "name", "") or "").strip()
            else:
                unique_val = None
                for cv in getattr(it, "column_values", []) or []:
                    if getattr(cv, "id", None) == self.unique_col:
                        unique_val = (
                            getattr(cv, "text", None) or getattr(cv, "value", None) or getattr(cv, "raw_value", None)
                        )
                        break
                unique_val = (unique_val or "").strip()
            if unique_val:
                idx[unique_val] = str(it.id)
        logging.info(f"[Monday.com/Writer] Indexed {len(idx)} existing items by '{self.unique_col}'.")
        return idx

    def _build_column_values(self, row: Dict[str, str]) -> Dict[str, str]:
        out = {}
        for src, col_id in self.mapping_src_to_colid.items():
            if col_id == "__item_name__":
                continue
            val = row.get(src)
            if val not in (None, ""):
                out[col_id] = str(val)
        return out

    def _upsert_row(self, row: Dict[str, str], idx: Dict[str, str]):
        """Create or update a single item based on the unique key."""
        key_val = (row.get(self.unique_src) or "").strip()
        if not key_val:
            logging.warning(f"[Monday.com/Writer] Skipping row without unique key '{self.unique_src}'.")
            return

        col_values = self._build_column_values(row)
        if not col_values:
            logging.info(f"[Monday.com/Writer] Skipping key '{key_val}' – no mapped values to write.")
            return

        existing_id = idx.get(key_val)

        try:
            if existing_id:
                self.client.items.change_multiple_column_values(
                    board_id=self.board_id,
                    item_id=existing_id,
                    column_values=col_values
                )
                logging.debug(f"[Monday.com/Writer] Updated item_id={existing_id} (key={key_val})")
            else:
                created = self.client.items.create_item(
                    board_id=self.board_id,
                    group_id=self.group_id,
                    item_name=key_val,
                    column_values=col_values
                )
                new_id = getattr(created, "id", None) or getattr(created, "item_id", None)
                if new_id:
                    idx[key_val] = str(new_id)
                logging.debug(f"[Monday.com/Writer] Created item_id={new_id} (key={key_val})")
        except Exception as e:
            raise UserException(f"Upsert failed for key '{key_val}': {e}")

    def _row_batches(self, csv_path: str, batch_size: int) -> Iterable[List[Dict[str, str]]]:
        """Yield lists of rows of size up to batch_size from a CSV."""
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            batch: List[Dict[str, str]] = []
            for row in reader:
                batch.append(row)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

    def _upsert_batch(self, rows: List[Dict[str, str]], idx: Dict[str, str]) -> int:
        for row in rows:
            self._upsert_row(row, idx)
        return len(rows)

    def upsert_from_csv(self, csv_path: str):
        """Upsert all rows from a specific CSV file in batches."""
        if not os.path.exists(csv_path):
            raise UserException(f"Input CSV not found: {csv_path}")

        idx = self._load_existing_index()
        total = 0
        for i, batch in enumerate(self._row_batches(csv_path, self.batch_size), start=1):
            processed = self._upsert_batch(batch, idx)
            total += processed
            logging.info(f"[Monday.com/Writer] Batch {i}: processed {processed} rows (total {total}).")
        logging.info(f"[Monday.com/Writer] Finished upserting {total} rows from {csv_path}.")

    def upsert_all_tables(self):
        """Scan data/in/tables and upsert every CSV found (batched)."""
        files = self.scan_table_inputs()
        if not files:
            logging.info("[Monday.com/Writer] No table CSVs found; nothing to write.")
            return
        for f in files:
            logging.info(f"[Monday.com/Writer] Upserting from {f['source']}")
            self.upsert_from_csv(f["source"])
