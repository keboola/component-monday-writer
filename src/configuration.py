"""
Configuration Models for Monday.com Writer Component

Pydantic-based configuration models providing validation and structure
for component parameters, field mappings, and sync options.

Models:
- Authorization: Monday.com API authentication
- FieldMapping: Source column to Monday.com column mapping
- UniqueKey: Unique identifier configuration for upsert logic
- SyncOptions: Board, workspace, and sync behavior settings
- Parameters: Root parameter container with validation
- Configuration: Top-level configuration wrapper
"""

from typing import List, Optional, Dict, Tuple, Union

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_validator,
    field_validator,
)

from keboola.component.exceptions import UserException


class Authorization(BaseModel):
    """
    Monday.com API authentication configuration.

    Attributes:
        api_key: Monday.com API token (v2) for authentication
    """
    api_key: str = Field(..., alias="#api_key", title="API Token")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate(self) -> "Authorization":
        """
        Validate and normalize API token.

        Ensures token is not empty after whitespace stripping.

        Raises:
            ValueError: If API token is empty or not provided
        """
        self.api_key = (self.api_key or "").strip()
        if not self.api_key:
            raise ValueError("API token must be provided.")
        return self


class FieldMapping(BaseModel):
    """
    Single field mapping between source table column and Monday.com column.

    Attributes:
        source_column: Column name from input CSV/table
        monday_column_id: Target Monday.com column ID (e.g., "color_mkqa7d0y")
    """
    source_column: Optional[str] = Field(None, title="Source Column")
    monday_column_id: Optional[str] = Field(None, title="Monday Column ID")

    @model_validator(mode="after")
    def _validate(self) -> "FieldMapping":
        """
        Normalize field mapping values.

        Allows empty rows during UI editing but normalizes non-empty values
        by stripping whitespace.
        """
        if not self.source_column and not self.monday_column_id:
            return self

        if self.source_column:
            self.source_column = self.source_column.strip()
        if self.monday_column_id:
            self.monday_column_id = self.monday_column_id.strip()

        return self


class UniqueKey(BaseModel):
    """
    Unique identifier configuration for upsert operations.

    Defines which source column uniquely identifies records and how it maps
    to Monday.com. Defaults to using the source column value as the Monday.com
    item name.

    Attributes:
        source_column: Source table column containing unique identifiers
        monday_column_id: Target Monday.com column (default: "__item_name__")
    """
    source_column: Optional[str] = Field(None, title="Unique Source Column")
    monday_column_id: Optional[str] = Field(
        "__item_name__", title="Unique Monday Column ID"
    )

    @model_validator(mode="after")
    def _validate(self) -> "UniqueKey":
        """
        Normalize unique key configuration.

        Allows empty configuration during UI/sync actions but normalizes
        values when provided. Defaults monday_column_id to "__item_name__".
        """
        if not self.source_column:
            return self

        self.source_column = (self.source_column or "").strip()
        self.monday_column_id = (self.monday_column_id or "__item_name__").strip()
        return self


class SyncOptions(BaseModel):
    """
    Runtime synchronization configuration.

    Attributes:
        workspace_id: Monday.com workspace identifier
        board_id: Monday.com board identifier
        group_id: Target group within board for new items
        batch_size: Number of rows to process per batch (1-500)
    """
    workspace_id: Optional[Union[str, int]] = Field(None, title="Workspace ID")
    board_id: Optional[Union[str, int]] = Field(None, title="Board ID")
    group_id: Optional[str] = Field(None, title="Group ID")
    batch_size: int = Field(50, ge=1, le=500, title="Batch Size")

    @field_validator("workspace_id", "board_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value):
        """
        Normalize workspace and board IDs to strings.

        Handles null/empty values and converts numeric IDs to strings.
        """
        if value in (None, "", "null"):
            return None
        return str(value).strip()

    @field_validator("group_id", mode="before")
    @classmethod
    def _normalize_group_id(cls, value):
        """
        Normalize group ID to string.

        Handles null/empty values and strips whitespace.
        """
        if value in (None, "", "null"):
            return None
        return str(value).strip()

    @model_validator(mode="after")
    def _validate(self) -> "SyncOptions":
        """
        Final validation and normalization of sync options.

        Ensures board_id is properly formatted as string.
        """
        if self.board_id:
            self.board_id = str(self.board_id).strip()
        return self


class Parameters(BaseModel):
    """
    Root parameters container for Monday.com writer component.

    Aggregates all configuration sections and provides validation logic
    that differs between sync actions (UI dropdowns) and actual run execution.

    Attributes:
        authorization: API authentication configuration
        sync_options: Board and sync behavior settings
        unique_key: Unique identifier configuration
        field_mappings: List of column mappings
        action: Current action type ("run" or sync action name)
    """
    authorization: Optional[Authorization] = None
    sync_options: Optional[SyncOptions] = None
    unique_key: Optional[UniqueKey] = None
    field_mappings: Optional[List[FieldMapping]] = None
    action: Optional[str] = Field(default="run")

    @model_validator(mode="after")
    def _validate(self) -> "Parameters":
        """
        Validate parameters based on action context.

        Validation rules:
        - Sync actions: Minimal validation (UI is still being configured)
        - Run action: Full validation including:
          - At least one field mapping required
          - No duplicate source columns or Monday columns
          - Unique key source column must be provided
          - If unique key uses real Monday column (not item_name), it must be in mappings

        Note: Unique key source column is NOT required to be in field_mappings
        when using item_name mapping, as it serves as the item identifier only.

        Raises:
            ValueError: If validation fails for run action
        """
        if self.action and self.action not in ("run", None):
            return self

        if not self.authorization or not self.sync_options:
            return self

        if not self.field_mappings:
            raise ValueError("field_mappings must contain at least one mapping.")

        source_columns = [
            mapping.source_column for mapping in self.field_mappings
            if mapping.source_column
        ]
        monday_columns = [
            mapping.monday_column_id for mapping in self.field_mappings
            if mapping.monday_column_id
        ]

        if len(set(source_columns)) != len(source_columns):
            raise ValueError("field_mappings contains duplicate source_column values.")
        if len(set(monday_columns)) != len(monday_columns):
            raise ValueError("field_mappings contains duplicate monday_column_id values.")

        if self.unique_key:
            if not self.unique_key.source_column:
                raise ValueError("unique_key.source_column must be provided.")

            monday_key_id = self.unique_key.monday_column_id or "__item_name__"

            if monday_key_id != "__item_name__" and monday_key_id not in monday_columns:
                raise ValueError(
                    "unique_key.monday_column_id is not present in field_mappings."
                )

        return self

    @property
    def mapping_dict(self) -> Dict[str, str]:
        """
        Get field mappings as dictionary.

        Returns:
            Dictionary mapping source_column -> monday_column_id
        """
        return {
            mapping.source_column: mapping.monday_column_id
            for mapping in (self.field_mappings or [])
            if mapping.source_column and mapping.monday_column_id
        }

    @property
    def monday_columns(self) -> List[str]:
        """
        Get ordered list of Monday.com column IDs to be written.

        Returns:
            List of Monday.com column IDs from field mappings
        """
        return [
            mapping.monday_column_id
            for mapping in (self.field_mappings or [])
            if mapping.monday_column_id
        ]

    @property
    def identity_pair(self) -> Optional[Tuple[str, str]]:
        """
        Get unique key configuration as tuple.

        Returns:
            Tuple of (source_column, monday_column_id) or None if not configured

        Example:
            ("ID", "__item_name__") - ID column maps to item name
            ("reference", "text_ref") - reference column maps to text_ref column
        """
        if not self.unique_key or not self.unique_key.source_column:
            return None
        return (
            self.unique_key.source_column,
            (self.unique_key.monday_column_id or "__item_name__"),
        )


class Configuration(BaseModel):
    """
    Top-level configuration wrapper for Keboola component.

    Provides convenient property accessors for nested configuration sections
    and converts Pydantic validation errors into user-friendly Keboola exceptions.

    Attributes:
        parameters: Component parameters container
        action: Current action being executed
    """
    parameters: Optional[Parameters] = None
    action: Optional[str] = Field(default="run")

    def __init__(self, **data):
        """
        Initialize configuration with validation error handling.

        Args:
            **data: Configuration dictionary from Keboola

        Raises:
            UserException: If configuration validation fails, with user-friendly message
        """
        try:
            super().__init__(**data)
        except ValidationError as validation_error:
            error_messages = [
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in validation_error.errors()
            ]
            raise UserException(
                f"Configuration validation error: {', '.join(error_messages)}"
            )

    @property
    def auth(self) -> Optional[Authorization]:
        """Get authorization configuration."""
        return self.parameters.authorization if self.parameters else None

    @property
    def sync(self) -> Optional[SyncOptions]:
        """Get sync options configuration."""
        return self.parameters.sync_options if self.parameters else None

    @property
    def mappings(self) -> Dict[str, str]:
        """Get field mappings as dictionary."""
        return self.parameters.mapping_dict if self.parameters else {}

    @property
    def unique(self) -> Optional[Tuple[str, str]]:
        """Get unique key configuration as tuple."""
        return self.parameters.identity_pair if self.parameters else None
