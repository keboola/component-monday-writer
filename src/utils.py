"""
Utility functions for Monday.com Writer component.

Provides helper functions for interacting with Keboola Storage API,
particularly for retrieving table column definitions.
"""

from typing import List

from client_storage import StorageAPIClient


def get_storage_column_definition(
        table_id: str,
        storage_url: str,
        storage_token: str
) -> List[str]:
    """
    Retrieve column names for a Keboola Storage table.

    Handles both typed and untyped tables:
    - Typed tables: Returns column names from table definition schema
    - Untyped tables: Returns column names from raw columns array

    Args:
        table_id: Storage table identifier (e.g., "in.c-bucket.table_name")
        storage_url: Base URL of Keboola Storage API
        storage_token: Storage API authentication token

    Returns:
        List of column names in the table

    Example:
        >>> get_storage_column_definition(
        ...     "in.c-main.customers",
        ...     "https://connection.keboola.com",
        ...     "your-storage-token"
        ... )
        ['id', 'name', 'email', 'created_at']
    """
    storage_client = StorageAPIClient(storage_url, storage_token)
    table_detail = storage_client.get_table_detail(table_id)

    if table_detail.get("isTyped") and table_detail.get("definition"):
        return [column["name"] for column in table_detail["definition"]["columns"]]

    return table_detail.get("columns", [])
