# Monday.com Writer

The Monday.com Writer syncs data from Keboola Storage tables to Monday.com boards with intelligent create/update logic, strict validation, and comprehensive error logging.

## Features

- **Automatic Upsert Logic**
  - Creates new items when they don't exist in Monday.com.
  - Updates existing items based on a unique key field.
  - Builds an index of existing items to avoid duplicates.

- **Comprehensive Column Type Support**
  - Handles status, color, dropdown, date, email, text, and number columns.
  - Automatically formats values correctly for each Monday.com column type.
  - Validates dropdown/status values against allowed labels before syncing.

- **Strict Pre-API Validation**
  - Validates email formats using regex patterns.
  - Checks dropdown and status values against board metadata.
  - Normalizes dates to ISO format (YYYY-MM-DD).
  - Rejects invalid rows before hitting the Monday.com API.

- **Protected Column Detection**
  - Automatically skips `long_text` and `doc` columns to preserve existing notes.
  - Protects column-level conversation threads and updates.

- **Detailed Error Logging**
  - Logs all validation failures and API errors to `monday_writer_events.csv`.
  - Includes full row context for easy debugging and correction.
  - Events table is incremental with unique event IDs.

- **Transparent GraphQL Integration**
  - Uses GraphQL mutations for direct API control.
  - Provides clear, actionable error messages from Monday.com.
  - No SDK black box - you see exactly what succeeds and what fails.

- **Batch Processing**
  - Configurable batch size (1-500 rows per batch).
  - Processes large datasets efficiently.

## Configuration

The Monday.com Writer is configured through the Keboola UI with the following steps:

### 1. Input Table Selection

Select the Keboola Storage table containing the data you want to sync to Monday.com. The component requires exactly one input table.

### 2. Authentication

Enter your Monday.com API token:
- Generate from: Monday.com → Profile → Developers → API (v2 Token)
- The token is securely stored as an encrypted parameter

### 3. Sync Options

Configure where the data will be written in Monday.com:

- **Workspace**: Select from your available Monday.com workspaces (auto-populated dropdown)
- **Board**: Select the target board within the workspace (auto-populated after workspace selection)
- **Group**: Select the group where new items will be created (auto-populated after board selection)
- **Batch Size** (optional): Number of rows per batch (default: 50, range: 1-500)

### 4. Unique Field

Select the source column that uniquely identifies each record:
- This value becomes the Monday.com **item name**
- Used to determine whether to create a new item or update an existing one
- Must contain unique values across all records

Common examples: `ID`, `Item_Code`, `Request_Number`, `Ticket_ID`

### 5. Column Mappings

Map your source table columns to Monday.com board columns using the UI table:

| Source Column | Monday Column   |
|---------------|-----------------|
| Category      | Category (color_mkqa7d0y) |
| Status        | Status (color_mknfwnja) |
| Owner         | Owner (dropdown_mknfs0cz) |
| Created_Date  | Created Date (date_mknfdkdr) |
| Contact_Email | Contact Email (email_mknfwe0n) |

Both dropdowns are auto-populated:
- **Source Column**: Lists all columns from your input table
- **Monday Column**: Lists all columns from the selected board

The component automatically detects column types and formats values appropriately:

- **Status/Color columns**: Validates against allowed labels
- **Dropdown columns**: Validates against configured options
- **Date columns**: Normalizes to YYYY-MM-DD format
- **Email columns**: Validates email format
- **Text/Number columns**: Direct value transfer
- **Note/Doc columns**: Automatically skipped to preserve existing content

### Important Notes

- **Label validation**: Status, color, and dropdown values must exactly match (case-sensitive) the labels configured in Monday.com
- **Email validation**: Invalid email addresses will reject the entire row
- **Protected columns**: `long_text` and `doc` type columns are automatically skipped to preserve notes and documentation
- **Column updates**: Existing column-level conversation threads and notes are always preserved

## Output

After a successful run, the component produces:

### Success Summary

```text
[Monday.com/Writer] ═══ SYNC SUMMARY ═══
[Monday.com/Writer] Total rows processed: 100
[Monday.com/Writer] ✓ Successfully synced: 95
[Monday.com/Writer] ✗ Errors encountered: 2
[Monday.com/Writer] ↯ Rejected (invalid data): 3
[Monday.com/Writer] ⊘ Skipped (no mapped values): 0
```

### Events Table

All issues are logged to `data/out/tables/monday_writer_events.csv`:

```csv
event_id,event_time,event_type,unique_id,error_message,row_data
abc123...,2025-11-18T23:40:50Z,rejected_row,ITEM001,"Value 'Invalid' is not an allowed label for column 'color_status'. Allowed: ['Active', 'Pending']","{...full row JSON...}"
def456...,2025-11-18T23:40:51Z,error_upsert,ITEM002,"The dropdown column parameters are an array of ids or labels","{...full row JSON...}"
```

**Event Types:**
- `rejected_row`: Invalid email, invalid dropdown/status label, or missing unique key
- `error_upsert`: Monday.com API error during create/update
- `skip_column`: Unparseable date (column skipped, row still processed)
- `skip_no_mapped_values`: Row has no mapped values to write

The events table is **incremental** with `event_id` as the primary key, allowing you to track issues across multiple runs.

## How It Works

1. **Index Building**: Fetches all existing items from the Monday.com board and builds an index by unique key
2. **Validation**: For each row:
   - Validates email format
   - Checks status/dropdown labels against allowed values
   - Normalizes dates to ISO format
   - Skips protected note/doc columns
3. **Upsert Logic**:
   - If unique key exists in index → **UPDATE** via GraphQL `change_multiple_column_values`
   - If unique key not found → **CREATE** via GraphQL `create_item`
4. **Error Logging**: All failures logged to events table with full context

## Column Type Reference

### Status & Color Columns

Monday.com status and color columns accept predefined labels:

**Input CSV:**
```csv
Status,Priority
Active,High
```

**Formatted for API:**
```json
{
  "color_status": {"label": "Active"},
  "color_priority": {"label": "High"}
}
```

⚠️ **Labels are case-sensitive** and must match exactly with board configuration.

### Dropdown Columns

Dropdown columns require an array format:

**Input CSV:**
```csv
Owner
Team A
```

**Formatted for API:**
```json
{
  "dropdown_owner": {"labels": ["Team A"]}
}
```

### Date Columns

The component normalizes various date formats:

**Supported formats:**
- ISO: `2025-08-15`
- US: `08/15/2025`
- UK: `15/08/2025`
- Written: `Aug 15, 2025` or `August 15, 2025`

**Formatted for API:**
```json
{
  "date_request": {"date": "2025-08-15"}
}
```

### Email Columns

Emails are validated and formatted:

**Input CSV:**
```csv
Contact_Email
john@example.com
```

**Formatted for API:**
```json
{
  "email_contact": {
    "email": "john@example.com",
    "text": "john@example.com"
  }
}
```

⚠️ Invalid emails reject the entire row.

## Running Locally

For local development and testing, you can run the component outside of Keboola.

### Setup Configuration

Create a `data/config.json` file with the raw configuration parameters (this mimics what Keboola generates from the UI):

```json
{
  "parameters": {
    "authorization": {
      "#api_key": "your_monday_api_token"
    },
    "sync_options": {
      "workspace_id": "12782477",
      "board_id": "18146232311",
      "group_id": "topics",
      "batch_size": 50
    },
    "unique_key": {
      "source_column": "ID"
    },
    "field_mappings": [
      {
        "source_column": "Category",
        "monday_column_id": "color_mkqa7d0y"
      },
      {
        "source_column": "Status",
        "monday_column_id": "color_mknfwnja"
      }
    ]
  }
}
```

### Prepare Input Data

Create your input CSV in `data/in/tables/`:

```bash
mkdir -p data/in/tables
echo "ID,Category,Status" > data/in/tables/test.csv
echo "ITEM001,Electronics,Active" >> data/in/tables/test.csv
echo "ITEM002,Furniture,Pending" >> data/in/tables/test.csv
```

### Run the Component

```bash
python3 src/component.py
```

### Check Output

After running, check the events table for any issues:

```bash
cat data/out/tables/monday_writer_events.csv
```

### Development & Testing

Install all dependencies into your virtual environment using `uv`:

```bash
uv pip sync
```

This installs everything listed in your `uv.lock` file, ensuring a fully reproducible environment matching your `pyproject.toml` specifications.

To update your lockfile after changing dependencies in `pyproject.toml`, run:

```bash
uv pip compile pyproject.toml --output-file uv.lock
```

Run tests with:

```bash
pytest
```

## Troubleshooting

### Common Issues

**Issue**: All rows failing with "The dropdown column parameters are an array of ids or labels"

**Solution**: Check your column type mapping. Dropdown columns need `{"labels": [...]}` format, but the component handles this automatically. If you see this error, ensure you're using the latest version of the component.

---

**Issue**: Rows rejected with "Value 'X' is not an allowed label"

**Solution**: The value in your source data doesn't match the allowed labels in Monday.com. Check your board settings or update your source data. The error message lists all allowed values.

---

**Issue**: "Invalid email" rejections

**Solution**: Verify email format in source data. Common issues:
- Missing `@` symbol
- Invalid domain (e.g., `user@domain` without `.com`)
- Whitespace around email address

---

**Issue**: Dates not syncing

**Solution**: The component logs unparseable dates as `skip_column` events. Check the events table for details. Supported formats are listed in the Date Columns section above.

---

**Issue**: "Create failed - received None item_id"

**Solution**: This indicates a GraphQL API error. Check the events table for the specific error message from Monday.com. Common causes:
- Invalid column ID
- Missing required fields
- Board permissions issues

## API Rate Limits

Monday.com enforces API rate limits:
- **Complexity**: Each request has a complexity score
- **Rate limit**: ~10,000 complexity per minute

The component processes items sequentially and batches operations. For large datasets (1000+ items), monitor your run time and consider:
- Reducing `batch_size` to spread load
- Running during off-peak hours
- Splitting data across multiple runs

## License

MIT License