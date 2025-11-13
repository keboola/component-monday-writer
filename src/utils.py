from client_storage import SAPIClient


def get_sapi_column_definition(table_id: str, storage_url: str, storage_token: str):
    """Fetch simple column list from Keboola Storage API."""
    storage_client = SAPIClient(storage_url, storage_token)
    table_detail = storage_client.get_table_detail(table_id)

    if table_detail.get("isTyped") and table_detail.get("definition"):
        return [col["name"] for col in table_detail["definition"]["columns"]]
    return table_detail.get("columns", [])