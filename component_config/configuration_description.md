Configure the Monday.com Writer to sync data from Keboola Storage tables to your Monday.com boards with automatic create/update logic.

### Authentication
- **API Token** (Required)  
  Your Monday.com API token. Generate this from your Monday.com account settings under "Developers" > "API".

### Sync Options
- **Workspace** (Required)  
  Select the Monday.com workspace containing your target board. Click "LOAD WORKSPACES" to populate available options.

- **Board** (Required)  
  Select the board where items will be created or updated. Click "LOAD BOARDS" after selecting a workspace.

- **Group** (Required)  
  Select the group within the board where new items will be created. Existing items will be updated regardless of their group location.

- **Batch Size** (Optional, default: 50)  
  Number of rows to process in each batch. Range: 1-500. Larger batches improve performance but may hit API rate limits.

### Unique Field
- **Unique Source Column** (Required)  
  The column from your source table that uniquely identifies each record (e.g., "AR", "ID", "Request_Number"). This value becomes the Monday.com item name and is used to determine whether to create a new item or update an existing one.

### Column Mappings
Map your source table columns to Monday.com board columns. For each mapping:
- **Source Column**: The column from your input CSV/table
- **Monday Column**: The target column in your Monday.com board

The component automatically handles different column types:
- **Status/Color columns**: Validates values against allowed labels
- **Dropdown columns**: Validates values against configured options
- **Email columns**: Validates email format
- **Date columns**: Normalizes various date formats to YYYY-MM-DD
- **Text/Number columns**: Direct value transfer
- **Note/Doc columns**: Automatically skipped to preserve existing content

All validation errors and sync failures are logged to an events table (`monday_writer_events`) for easy troubleshooting. The component performs strict validation before sending data to Monday.com, ensuring data quality and preventing API failures.