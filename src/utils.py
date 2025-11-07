from client_storage import SAPIClient
from configuration import FieldMapping


def get_sapi_column_definition(table_id: str, storage_url: str, storage_token: str):
    """
    Get column definitions from Storage API table metadata.

    Args:
        table_id: Storage table ID
        storage_url: Storage API URL
        storage_token: Storage API token

    Returns:
        List of column configuration dicts with Keboola data types
    """
    storage_client = SAPIClient(storage_url, storage_token)
    table_detail = storage_client.get_table_detail(table_id)
    columns = []

    for column in table_detail.get("columns", []):
        columns.append(
            FieldMapping(
                source_column=column,
                monday_column_id=""
            ).model_dump()
        )

    return columns