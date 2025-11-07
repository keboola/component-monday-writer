import os
import csv
import logging
import re
from datetime import datetime
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

        self.error_count = 0
        self.skipped_count = 0
        self.success_count = 0

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
                                getattr(cv, "text", None) or getattr(cv, "value", None)
                                or getattr(cv, "raw_value", None)
                        )
                        break
                unique_val = (unique_val or "").strip()
            if unique_val:
                idx[unique_val] = str(it.id)
        logging.info(f"[Monday.com/Writer] Indexed {len(idx)} existing items by '{self.unique_col}'.")
        return idx

    def _is_valid_email(self, email_str: str) -> bool:
        if not email_str or email_str.strip() == "":
            return False
        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(email_regex, email_str.strip()))

    def _sanitize_date(self, date_str: str) -> str:
        if not date_str or date_str.strip() == "":
            return ""

        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str

        formats = ['%b %d, %Y', '%B %d, %Y', '%m/%d/%Y', '%d/%m/%Y']

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        logging.warning(f"[Monday.com/Writer] Could not parse date: {date_str}")
        return ""

    def _sanitize_status(self, status_str: str) -> str:
        return status_str.strip() if status_str else ""

    def _build_column_values(self, row: Dict[str, str]) -> Dict[str, str]:
        out = {}
        for src, col_id in self.mapping_src_to_colid.items():
            if col_id == "__item_name__":
                continue
            val = row.get(src)
            if val not in (None, ""):
                if col_id.startswith("email_"):
                    if not self._is_valid_email(val):
                        logging.warning(f"[Monday.com/Writer] Skipping invalid email for column {col_id}: '{val}'")
                        continue
                elif "date" in col_id.lower():
                    val = self._sanitize_date(val)
                    if not val:
                        continue
                elif col_id.startswith("color_") or col_id.startswith("dropdown_"):
                    val = self._sanitize_status(val)
                    if not val:
                        continue
                out[col_id] = str(val)
        return out

    def _upsert_row(self, row: Dict[str, str], idx: Dict[str, str]):
        key_val = (row.get(self.unique_src) or "").strip()
        if not key_val:
            self.skipped_count += 1
            logging.warning(f"[Monday.com/Writer] Skipping row without unique key '{self.unique_src}'. Row data: {row}")
            return

        col_values = self._build_column_values(row)
        if not col_values:
            self.skipped_count += 1
            logging.info(f"[Monday.com/Writer] Skipping key '{key_val}' – no mapped values to write.")
            return

        existing_id = idx.get(key_val)

        try:
            logging.info(f"[Monday.com/Writer] Attempting sync for key '{key_val}'")
            logging.info(f"[Monday.com/Writer] Column values being sent: {col_values}")

            if existing_id:
                self.client.items.change_multiple_column_values(
                    board_id=self.board_id,
                    item_id=existing_id,
                    column_values=col_values,
                    create_labels_if_missing=True
                )
                self.success_count += 1
                logging.info(f"[Monday.com/Writer] ✓ Updated item_id={existing_id} (key={key_val})")
            else:
                created = self.client.items.create_item(
                    board_id=self.board_id,
                    group_id=self.group_id,
                    item_name=key_val,
                    column_values=col_values,
                    create_labels_if_missing=True
                )
                new_id = getattr(created, "id", None) or getattr(created, "item_id", None)
                if new_id:
                    idx[key_val] = str(new_id)
                    self.success_count += 1
                    logging.info(f"[Monday.com/Writer] ✓ Created item_id={new_id} (key={key_val})")
                else:
                    self.error_count += 1
                    logging.error(f"[Monday.com/Writer] ✗ Create failed - received None item_id for key '{key_val}'")
                    logging.error(f"[Monday.com/Writer] Response object: {created}")
        except Exception as e:
            self.error_count += 1
            error_msg = str(e)

            logging.error(f"[Monday.com/Writer] ERROR for key '{key_val}': {error_msg}")
            logging.error(f"[Monday.com/Writer] Failed row data: {row}")
            logging.error(f"[Monday.com/Writer] Column values attempted: {col_values}")

    def _row_batches(self, csv_path: str, batch_size: int) -> Iterable[List[Dict[str, str]]]:
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
        if not os.path.exists(csv_path):
            raise UserException(f"Input CSV not found: {csv_path}")

        idx = self._load_existing_index()
        total = 0
        for i, batch in enumerate(self._row_batches(csv_path, self.batch_size), start=1):
            processed = self._upsert_batch(batch, idx)
            total += processed
            logging.info(f"[Monday.com/Writer] Batch {i}: processed {processed} rows (total {total}).")

        logging.info("[Monday.com/Writer] ═══ SYNC SUMMARY ═══")
        logging.info(f"[Monday.com/Writer] Total rows processed: {total}")
        logging.info(f"[Monday.com/Writer] ✓ Successfully synced: {self.success_count}")
        logging.info(f"[Monday.com/Writer] ✗ Errors encountered: {self.error_count}")
        logging.info(f"[Monday.com/Writer] ⊘ Skipped (no unique key): {self.skipped_count}")
        logging.info(f"[Monday.com/Writer] Finished upserting from {csv_path}.")

    def upsert_all_tables(self):
        files = self.scan_table_inputs()
        if not files:
            logging.info("[Monday.com/Writer] No table CSVs found; nothing to write.")
            return
        for f in files:
            logging.info(f"[Monday.com/Writer] Upserting from {f['source']}")
            self.upsert_from_csv(f["source"])
