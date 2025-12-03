"""
Monday.com API Client for Keboola Writer Component

This module provides a GraphQL-based client for syncing data from Keboola Storage
to Monday.com boards with intelligent upsert logic, strict validation, and
comprehensive error logging.

Architecture:
- GraphQL client for metadata queries and create/update mutations
- Monday.com Python SDK for fetching existing items only
- Pre-API validation for emails, dates, and dropdown/status labels
- Events logger for tracking all validation and API failures
"""

import csv
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple, Any

import requests
from keboola.component.exceptions import UserException
from monday_sdk import MondayClient


class MondayGraphQLClient:
    """
    GraphQL client for Monday.com API operations.

    Handles:
    - Metadata queries (board columns, types, allowed labels)
    - Item creation mutations
    - Item update mutations
    - Error formatting and reporting
    """

    def __init__(self, token: str):
        self.token = token
        self.url = "https://api.monday.com/v2"

    def query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute a GraphQL query against Monday.com API.

        Args:
            query: GraphQL query string
            variables: Query variables dictionary

        Returns:
            Response data dictionary (may include 'errors' key)

        Raises:
            UserException: If HTTP request fails
        """
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

        response = requests.post(
            self.url,
            headers=headers,
            json={"query": query, "variables": variables or {}},
        )

        if response.status_code != 200:
            raise UserException(f"GraphQL HTTP {response.status_code}: {response.text}")

        data = response.json()
        if "errors" in data:
            return data

        return data.get("data", {})

    def create_item(
            self,
            board_id: str,
            group_id: str,
            item_name: str,
            column_values: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Create a new item in Monday.com board.

        Args:
            board_id: Target board ID
            group_id: Target group ID within board
            item_name: Name for the new item (typically the unique key)
            column_values: Dictionary of column_id -> formatted value

        Returns:
            Tuple of (item_id, error_message)
            - Success: (item_id, None)
            - Failure: (None, error_message)
        """
        mutation = """
        mutation ($board_id: ID!, $group_id: String!, $item_name: String!, $column_values: JSON!) {
          create_item(
            board_id: $board_id,
            group_id: $group_id,
            item_name: $item_name,
            column_values: $column_values,
            create_labels_if_missing: true
          ) {
            id
          }
        }
        """

        variables = {
            "board_id": board_id,
            "group_id": group_id,
            "item_name": item_name,
            "column_values": json.dumps(column_values),
        }

        try:
            result = self.query(mutation, variables)

            if "errors" in result:
                error_details = result["errors"]
                error_message = self._format_graphql_errors(error_details)
                return None, error_message

            created_item = result.get("create_item", {})
            item_id = created_item.get("id")

            if item_id:
                return str(item_id), None
            else:
                return None, f"No item_id returned from create_item mutation. Response: {result}"

        except Exception as e:
            return None, f"Exception during create_item: {str(e)}"

    def update_item(
            self,
            board_id: str,
            item_id: str,
            column_values: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Update an existing item in Monday.com board.

        Args:
            board_id: Target board ID
            item_id: ID of item to update
            column_values: Dictionary of column_id -> formatted value

        Returns:
            Tuple of (success, error_message)
            - Success: (True, None)
            - Failure: (False, error_message)
        """
        mutation = """
        mutation ($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
          change_multiple_column_values(
            board_id: $board_id,
            item_id: $item_id,
            column_values: $column_values,
            create_labels_if_missing: true
          ) {
            id
          }
        }
        """

        variables = {
            "board_id": board_id,
            "item_id": item_id,
            "column_values": json.dumps(column_values),
        }

        try:
            result = self.query(mutation, variables)

            if "errors" in result:
                error_details = result["errors"]
                error_message = self._format_graphql_errors(error_details)
                return False, error_message

            updated_item = result.get("change_multiple_column_values", {})
            if updated_item and updated_item.get("id"):
                return True, None
            else:
                return False, f"Update failed. Response: {result}"

        except Exception as e:
            return False, f"Exception during update_item: {str(e)}"

    def _format_graphql_errors(self, errors: List[Dict[str, Any]]) -> str:
        """
        Format GraphQL error response into human-readable message.

        Extracts error messages and any additional problem explanations
        from the GraphQL error extensions.
        """
        messages = []
        for error in errors:
            message = error.get("message", "Unknown error")

            extensions = error.get("extensions", {})
            if "problems" in extensions:
                problems = extensions["problems"]
                for problem in problems:
                    if "explanation" in problem:
                        message += f" | {problem['explanation']}"

            messages.append(message)

        return " | ".join(messages)


class MondayWriterEventsLogger:
    """
    Event logger for Monday.com Writer operations.

    Logs all validation failures and API errors to an incremental CSV table:
    - data/out/tables/monday_writer_events.csv

    Event types:
    - rejected_row: Pre-API validation failure (invalid email, invalid label, missing key)
    - error_upsert: API error during create or update operation
    - skip_column: Column skipped due to unparseable data (e.g., invalid date)
    - skip_no_mapped_values: Row skipped because no values mapped
    """

    HEADER = ["event_id", "event_time", "event_type", "unique_id", "error_message", "row_data"]

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        output_file_name = "monday_writer_events.csv"
        self.tables_output_directory = os.path.join(self.data_dir, "out", "tables")
        os.makedirs(self.tables_output_directory, exist_ok=True)

        self.events_file_path = os.path.join(self.tables_output_directory, output_file_name)
        self.manifest_file_path = self.events_file_path + ".manifest"

        self._ensure_manifest()
        self._ensure_header()

    def _ensure_manifest(self) -> None:
        """
        Create manifest file for events table with incremental loading configuration.
        """
        manifest = {
            "incremental": True,
            "primary_key": ["event_id"],
            "destination": "out.c-debug.monday_writer_events"
        }
        try:
            with open(self.manifest_file_path, "w", encoding="utf-8") as file_handle:
                json.dump(manifest, file_handle)
        except Exception as e:
            logging.warning(f"[Monday.com/Writer] Failed to write manifest for events table: {e}")

    def _ensure_header(self) -> None:
        """
        Create events CSV file with header row if it doesn't exist.
        """
        if not os.path.exists(self.events_file_path):
            try:
                with open(self.events_file_path, "w", newline="", encoding="utf-8") as file_handle:
                    writer = csv.writer(file_handle)
                    writer.writerow(self.HEADER)
            except Exception as e:
                logging.warning(f"[Monday.com/Writer] Failed to init events CSV header: {e}")

    def log_event(
            self,
            event_type: str,
            unique_id: Optional[str],
            error_message: str,
            row_data: Dict[str, Any],
    ) -> None:
        """
        Log an event to the events CSV file.

        Args:
            event_type: Type of event (rejected_row, error_upsert, skip_column, etc.)
            unique_id: Unique identifier from source data (if available)
            error_message: Human-readable error description
            row_data: Complete source row data as dictionary
        """
        event_id = uuid.uuid4().hex
        event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            with open(self.events_file_path, "a", newline="", encoding="utf-8") as file_handle:
                writer = csv.writer(file_handle)
                writer.writerow([
                    event_id,
                    event_time,
                    event_type,
                    unique_id or "",
                    error_message,
                    json.dumps(row_data, ensure_ascii=False),
                    ])
        except Exception as e:
            logging.warning(f"[Monday.com/Writer] Failed to append event row: {e}")


class ApiClient:
    """
    Main API client for Monday.com Writer component.

    Orchestrates the complete sync workflow:
    1. Load board metadata (column types, allowed labels)
    2. Build index of existing items from Monday.com
    3. Process CSV rows in batches
    4. Validate data against board metadata
    5. Execute GraphQL mutations (create/update)
    6. Log all errors and validation failures

    Uses:
    - GraphQL for all create/update operations (transparent errors)
    - Monday.com SDK only for fetching existing items
    - Strict pre-API validation to prevent invalid data from reaching API
    """

    def __init__(self, config, component):
        self.config = config
        self.component = component
        self.data_directory = self.component.configuration.data_dir

        self.api_token: str = self.config.auth.api_key
        self.board_id: str = str(self.config.sync.board_id)
        self.batch_size: int = int(self.config.sync.batch_size)
        self.group_id: str = (self.config.sync.group_id or "topics").strip()

        self.source_to_monday_mapping: Dict[str, str] = self.config.mappings
        self.unique_source_column, self.unique_monday_column = self.config.unique

        self.sdk_client = MondayClient(token=self.api_token)
        self.graphql_client = MondayGraphQLClient(self.api_token)

        self.columns_metadata: Dict[str, Dict[str, Any]] = {}
        self.protected_note_columns: List[str] = []

        self.success_count = 0
        self.error_count = 0
        self.rejected_count = 0
        self.skipped_count = 0

        self.events_logger = MondayWriterEventsLogger(self.data_directory)

        logging.info("[Monday.com/Writer] Initialized (GraphQL mutations + SDK lookups).")

        self._load_board_metadata()

    def _load_board_metadata(self) -> None:
        """
        Load board column metadata from Monday.com API.

        Retrieves:
        - Column IDs, titles, and types
        - Settings (including allowed labels for status/dropdown columns)
        - Identifies protected note/doc columns that should not be auto-updated
        """
        query = """
        query ($board_ids: [ID!]) {
          boards(ids: $board_ids) {
            id
            columns {
              id
              title
              type
              settings_str
            }
          }
        }
        """

        data = self.graphql_client.query(query, {"board_ids": [self.board_id]})

        if "errors" in data:
            raise UserException(f"GraphQL Error loading metadata: {data['errors']}")

        boards = data.get("boards") or []
        if not boards:
            raise UserException(f"No board metadata returned for board_id={self.board_id}")

        board = boards[0]
        columns = board.get("columns") or []
        if not columns:
            raise UserException(f"No columns metadata returned for board_id={self.board_id}")

        columns_metadata: Dict[str, Dict[str, Any]] = {}
        protected_columns = []

        for column in columns:
            column_id = column.get("id")
            column_type = column.get("type")
            settings_str = column.get("settings_str") or "{}"

            try:
                settings = json.loads(settings_str) if settings_str else {}
            except Exception:
                settings = {}

            allowed_labels: List[str] = []
            if column_type in ("color", "status"):
                labels_object = settings.get("labels") or settings.get("labels_colors") or {}
                if isinstance(labels_object, dict):
                    allowed_labels = [
                        value for value in labels_object.values()
                        if isinstance(value, str) and value.strip()
                    ]
                elif isinstance(labels_object, list):
                    allowed_labels = [
                        value for value in labels_object
                        if isinstance(value, str) and value.strip()
                    ]

            elif column_type in ("dropdown", "multi-select", "multi-select-legacy"):
                labels_object = settings.get("labels") or []
                if isinstance(labels_object, list):
                    allowed_labels = [
                        value for value in labels_object
                        if isinstance(value, str) and value.strip()
                    ]

            if column_type in ("long_text", "doc"):
                protected_columns.append(column_id)

            columns_metadata[column_id] = {
                "id": column_id,
                "title": column.get("title"),
                "type": column_type,
                "settings": settings,
                "allowed_labels": allowed_labels,
            }

        self.columns_metadata = columns_metadata
        self.protected_note_columns = protected_columns

        logging.info(
            f"[Monday.com/Writer] Loaded metadata for {len(columns_metadata)} columns "
            f"on board {self.board_id}."
        )
        if protected_columns:
            logging.info(
                "[Monday.com/Writer] Detected %d protected note columns (types=long_text/doc): %s",
                len(protected_columns),
                protected_columns,
            )

    def scan_table_inputs(self) -> List[Dict[str, str]]:
        """
        Scan input directory for CSV files to process.

        Returns:
            List of dictionaries with 'name' and 'source' keys for each CSV file
        """
        input_directory = os.path.join(self.data_directory, "in", "tables")
        if not os.path.exists(input_directory):
            logging.info(
                f"[Monday.com/Writer] tables dir does not exist: {input_directory} → skipping."
            )
            return []

        csv_files: List[Dict[str, str]] = []
        for filename in os.listdir(input_directory):
            if filename.endswith(".manifest"):
                continue

            full_path = os.path.join(input_directory, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(".csv"):
                csv_files.append({"name": filename, "source": full_path})

        logging.info(f"[Monday.com/Writer] Found {len(csv_files)} table CSV(s).")
        return csv_files

    def _row_batches(
            self, csv_path: str, batch_size: int
    ) -> Iterable[List[Dict[str, str]]]:
        """
        Generator that yields batches of rows from CSV file.

        Args:
            csv_path: Path to CSV file
            batch_size: Number of rows per batch

        Yields:
            Lists of row dictionaries (batch_size rows each)
        """
        with open(csv_path, newline="", encoding="utf-8") as file_handle:
            reader = csv.DictReader(file_handle)
            batch: List[Dict[str, str]] = []

            for row in reader:
                batch.append(row)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []

            if batch:
                yield batch

    def _load_existing_index(self) -> Dict[str, str]:
        """
        Build index of existing items on Monday.com board.

        Maps unique key values to Monday.com item IDs to determine
        whether each row should trigger a create or update operation.

        Returns:
            Dictionary mapping unique_value -> item_id
        """
        index: Dict[str, str] = {}
        logging.info(
            f"[Monday.com/Writer] Building existing index for board {self.board_id}..."
        )

        try:
            items = self.sdk_client.boards.fetch_all_items_by_board_id(
                board_id=self.board_id
            )
        except Exception as e:
            raise UserException(
                f"Failed to fetch items from board {self.board_id} via SDK: {e}"
            )

        indexed_count = 0
        for item in items or []:
            unique_value = ""

            if self.unique_monday_column == "__item_name__":
                unique_value = (getattr(item, "name", "") or "").strip()
            else:
                for column_value in getattr(item, "column_values", []) or []:
                    if getattr(column_value, "id", None) == self.unique_monday_column:
                        raw_value = (
                                getattr(column_value, "text", None)
                                or getattr(column_value, "value", None)
                                or getattr(column_value, "raw_value", None)
                        )
                        unique_value = (raw_value or "").strip()
                        break

            if unique_value:
                index[unique_value] = str(item.id)
                indexed_count += 1

        logging.info(
            f"[Monday.com/Writer] Indexed {indexed_count} existing items by '{self.unique_monday_column}' "
            f"for board_id={self.board_id}."
        )
        return index

    @staticmethod
    def _is_valid_email(email_string: str) -> bool:
        """
        Validate email address format using regex.

        Args:
            email_string: Email address to validate

        Returns:
            True if valid email format, False otherwise
        """
        if not email_string or email_string.strip() == "":
            return False

        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(email_regex, email_string.strip()))

    @staticmethod
    def _sanitize_date(date_string: str) -> str:
        """
        Normalize date strings to ISO format (YYYY-MM-DD).

        Supports multiple input formats:
        - ISO: 2025-08-15
        - US: 08/15/2025
        - UK: 15/08/2025
        - Written: Aug 15, 2025 or August 15, 2025

        Args:
            date_string: Date string in various formats

        Returns:
            ISO formatted date string (YYYY-MM-DD) or empty string if parsing fails
        """
        if not date_string or date_string.strip() == "":
            return ""

        normalized = date_string.strip()

        if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized):
            return normalized

        from datetime import datetime as dt

        date_formats = ["%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d/%m/%Y"]
        for date_format in date_formats:
            try:
                parsed_date = dt.strptime(normalized, date_format)
                return parsed_date.strftime("%Y-%m-%d")
            except ValueError:
                continue

        logging.warning(f"[Monday.com/Writer] Could not parse date: {date_string}")
        return ""

    def _get_column_metadata(self, column_id: str) -> Dict[str, Any]:
        """
        Retrieve metadata for a specific column.

        Args:
            column_id: Monday.com column ID

        Returns:
            Column metadata dictionary (empty dict if column not found)
        """
        return self.columns_metadata.get(column_id, {})

    def _build_column_values_and_validate(
            self, row: Dict[str, str], unique_key_value: str
    ) -> Optional[Dict[str, Any]]:
        """
        Build column values dictionary and validate data against board metadata.

        Validation rules:
        - Email columns: Must be valid email format (rejects row if invalid)
        - Status/dropdown columns: Must match allowed labels (rejects row if invalid)
        - Date columns: Attempts normalization (skips column if fails, doesn't reject row)
        - Protected note/doc columns: Always skipped to preserve existing content

        Args:
            row: Source data row dictionary
            unique_key_value: Value of the unique key for this row

        Returns:
            Dictionary of formatted column values, or None if validation fails
        """
        column_values: Dict[str, Any] = {}

        for source_column, monday_column_id in self.source_to_monday_mapping.items():
            if source_column == self.unique_source_column and self.unique_monday_column == "__item_name__":
                continue

            value = row.get(source_column)

            if value is None or value == "":
                continue

            if monday_column_id in self.protected_note_columns:
                logging.debug(
                    "[Monday.com/Writer] Skipping protected note/doc column '%s' for key '%s'.",
                    monday_column_id,
                    unique_key_value,
                )
                continue

            metadata = self._get_column_metadata(monday_column_id)
            column_type = metadata.get("type")

            if column_type == "email" or monday_column_id.startswith("email_"):
                if not self._is_valid_email(value):
                    error_message = f"Invalid email '{value}' for column '{monday_column_id}'. Row rejected."
                    logging.warning(
                        "[Monday.com/Writer] Row rejected for key '%s': %s",
                        unique_key_value,
                        error_message,
                    )
                    self.events_logger.log_event(
                        event_type="rejected_row",
                        unique_id=unique_key_value,
                        error_message=error_message,
                        row_data=row,
                    )
                    self.rejected_count += 1
                    return None

                column_values[monday_column_id] = {"email": value.strip(), "text": value.strip()}
                continue

            if column_type == "date" or monday_column_id.startswith("date_"):
                normalized_date = self._sanitize_date(value)
                if not normalized_date:
                    error_message = f"Could not parse date '{value}' for column '{monday_column_id}'. Column skipped."
                    logging.info(
                        "[Monday.com/Writer] %s (key='%s')",
                        error_message,
                        unique_key_value,
                    )
                    self.events_logger.log_event(
                        event_type="skip_column",
                        unique_id=unique_key_value,
                        error_message=error_message,
                        row_data=row,
                    )
                    continue

                column_values[monday_column_id] = {"date": normalized_date}
                continue

            if column_type in ("color", "status", "dropdown", "multi-select", "multi-select-legacy") or \
                    monday_column_id.startswith("color_") or monday_column_id.startswith("dropdown_"):
                allowed_labels = metadata.get("allowed_labels") or []
                normalized_value = value.strip()

                if not normalized_value:
                    continue

                if allowed_labels and normalized_value not in allowed_labels:
                    error_message = (
                        f"Value '{normalized_value}' is not an allowed label for column '{monday_column_id}'. "
                        f"Allowed: {allowed_labels}"
                    )
                    logging.warning(
                        "[Monday.com/Writer] Row rejected for key '%s': %s",
                        unique_key_value,
                        error_message,
                    )
                    self.events_logger.log_event(
                        event_type="rejected_row",
                        unique_id=unique_key_value,
                        error_message=error_message,
                        row_data=row,
                    )
                    self.rejected_count += 1
                    return None

                if column_type in ("dropdown", "multi-select", "multi-select-legacy") or \
                        monday_column_id.startswith("dropdown_"):
                    column_values[monday_column_id] = {"labels": [normalized_value]}
                else:
                    column_values[monday_column_id] = {"label": normalized_value}
                continue

            column_values[monday_column_id] = str(value)

        return column_values

    def _upsert_row(self, row: Dict[str, str], existing_items_index: Dict[str, str]) -> None:
        """
        Process a single row: validate, then create or update in Monday.com.

        Workflow:
        1. Extract unique key value
        2. Validate and build column values
        3. Check if item exists in index
        4. Execute GraphQL create or update mutation
        5. Log any errors to events table

        Args:
            row: Source data row dictionary
            existing_items_index: Index mapping unique values to Monday.com item IDs
        """
        unique_key_value = (row.get(self.unique_source_column) or "").strip()

        if not unique_key_value:
            error_message = f"Missing unique key '{self.unique_source_column}'."
            logging.warning(
                "[Monday.com/Writer] Row rejected: %s Row data: %s",
                error_message,
                row,
            )
            self.events_logger.log_event(
                event_type="rejected_row",
                unique_id="",
                error_message=error_message,
                row_data=row,
            )
            self.rejected_count += 1
            return

        column_values = self._build_column_values_and_validate(row, unique_key_value)
        if column_values is None:
            return

        if not column_values:
            error_message = f"Skipping key '{unique_key_value}' – no mapped values to write."
            logging.info("[Monday.com/Writer] %s", error_message)
            self.events_logger.log_event(
                event_type="skip_no_mapped_values",
                unique_id=unique_key_value,
                error_message=error_message,
                row_data=row,
            )
            self.skipped_count += 1
            return

        existing_item_id = existing_items_index.get(unique_key_value)

        logging.info(
            "[Monday.com/Writer] Attempting sync for key '%s' (existing_id=%s)",
            unique_key_value,
            existing_item_id,
        )
        logging.info(
            "[Monday.com/Writer] Column values being sent: %s",
            column_values,
        )

        if existing_item_id:
            success, error_message = self.graphql_client.update_item(
                board_id=self.board_id,
                item_id=existing_item_id,
                column_values=column_values,
            )

            if success:
                self.success_count += 1
                logging.info(
                    "[Monday.com/Writer] ✓ Updated item_id=%s (key=%s)",
                    existing_item_id,
                    unique_key_value,
                )
            else:
                logging.error(
                    "[Monday.com/Writer] Update failed for key '%s': %s",
                    unique_key_value,
                    error_message,
                )
                self.events_logger.log_event(
                    event_type="error_upsert",
                    unique_id=unique_key_value,
                    error_message=error_message or "Unknown error",
                    row_data=row,
                )
                self.error_count += 1
        else:
            new_item_id, error_message = self.graphql_client.create_item(
                board_id=self.board_id,
                group_id=self.group_id,
                item_name=unique_key_value,
                column_values=column_values,
            )

            if new_item_id:
                existing_items_index[unique_key_value] = new_item_id
                self.success_count += 1
                logging.info(
                    "[Monday.com/Writer] ✓ Created item_id=%s (key=%s)",
                    new_item_id,
                    unique_key_value,
                )
            else:
                logging.error(
                    "[Monday.com/Writer] Create failed for key '%s': %s",
                    unique_key_value,
                    error_message,
                )
                self.events_logger.log_event(
                    event_type="error_upsert",
                    unique_id=unique_key_value,
                    error_message=error_message or "Unknown error",
                    row_data=row,
                )
                self.error_count += 1

    def _upsert_batch(self, rows: List[Dict[str, str]], existing_items_index: Dict[str, str]) -> int:
        """
        Process a batch of rows.

        Args:
            rows: List of source data row dictionaries
            existing_items_index: Index mapping unique values to Monday.com item IDs

        Returns:
            Number of rows processed
        """
        for row in rows:
            self._upsert_row(row, existing_items_index)
        return len(rows)

    def upsert_from_csv(self, csv_path: str) -> None:
        """
        Process all rows from a CSV file and sync to Monday.com.

        Workflow:
        1. Load existing items index from Monday.com
        2. Process CSV in batches
        3. Log summary statistics

        Args:
            csv_path: Path to input CSV file

        Raises:
            UserException: If CSV file not found
        """
        if not os.path.exists(csv_path):
            raise UserException(f"Input CSV not found: {csv_path}")

        logging.info(f"[Monday.com/Writer] Upserting from {csv_path}")
        existing_items_index = self._load_existing_index()

        total_processed = 0
        for batch_number, batch in enumerate(self._row_batches(csv_path, self.batch_size), start=1):
            processed_count = self._upsert_batch(batch, existing_items_index)
            total_processed += processed_count
            logging.info(
                "[Monday.com/Writer] Batch %d: processed %d rows (total %d).",
                batch_number,
                processed_count,
                total_processed,
            )

        logging.info("[Monday.com/Writer] ═══ SYNC SUMMARY ═══")
        logging.info(f"[Monday.com/Writer] Total rows processed: {total_processed}")
        logging.info(f"[Monday.com/Writer] ✓ Successfully synced: {self.success_count}")
        logging.info(f"[Monday.com/Writer] ✗ Errors encountered: {self.error_count}")
        logging.info(f"[Monday.com/Writer] ↯ Rejected (invalid data): {self.rejected_count}")
        logging.info(f"[Monday.com/Writer] ⊘ Skipped (no mapped values): {self.skipped_count}")
        logging.info(f"[Monday.com/Writer] Finished upserting from {csv_path}.")

    def upsert_all_tables(self) -> None:
        """
        Process all CSV files from input directory.

        Scans data/in/tables for CSV files and processes each one sequentially.
        """
        csv_files = self.scan_table_inputs()
        if not csv_files:
            logging.info("[Monday.com/Writer] No table CSVs found; nothing to write.")
            return

        for csv_file in csv_files:
            logging.info(f"[Monday.com/Writer] Upserting from {csv_file['source']}")
            self.upsert_from_csv(csv_file["source"])
