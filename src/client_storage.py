"""
Keboola Storage API Client

Lightweight HTTP client for interacting with Keboola Storage API.
Provides table metadata retrieval with automatic retry logic for transient failures.
"""

import json
import logging
import time
import urllib.request
from typing import Dict, Any


class StorageAPIClient:
    """
    Client for Keboola Storage API operations.

    Handles HTTP requests to Storage API with built-in retry logic
    for handling transient network failures and API unavailability.
    """

    def __init__(self, base_url: str, storage_token: str, retry_attempts: int = 3):
        """
        Initialize Storage API client.

        Args:
            base_url: Base URL of Keboola Storage API (e.g., https://connection.keboola.com)
            storage_token: Storage API authentication token
            retry_attempts: Number of retry attempts for failed requests (default: 3)
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-StorageApi-Token": storage_token}
        self.retry_attempts = retry_attempts

    def get_table_detail(self, table_id: str) -> Dict[str, Any]:
        """
        Retrieve detailed metadata for a Storage table.

        Fetches complete table information including column definitions,
        table properties, and configuration. Automatically retries on failure
        with exponential backoff.

        Args:
            table_id: Storage table identifier (e.g., "in.c-bucket.table_name")

        Returns:
            Dictionary containing table metadata including:
            - columns: List of column names
            - definition: Column definitions for typed tables
            - isTyped: Whether table has typed schema
            - And other table properties

        Raises:
            Exception: If all retry attempts fail, raises the last exception encountered
            RuntimeError: If retries fail without capturing an exception
        """
        url = f"{self.base_url}/v2/storage/tables/{table_id}"
        last_exception = None

        for attempt_number in range(self.retry_attempts):
            try:
                request = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(request) as response:
                    response_data = response.read().decode("utf-8")
                    return json.loads(response_data)

            except Exception as exception:
                last_exception = exception
                logging.warning(
                    f"Attempt {attempt_number + 1}/{self.retry_attempts} failed: {exception}"
                )

                if attempt_number < self.retry_attempts - 1:
                    sleep_duration = attempt_number + 1
                    time.sleep(sleep_duration)

        if last_exception is not None:
            raise last_exception
        else:
            raise RuntimeError(
                "All attempts to get table detail failed, but no exception was captured."
            )
