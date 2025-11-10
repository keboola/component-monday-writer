from typing import List, Optional, Dict, Tuple, Union
from pydantic import BaseModel, Field, ValidationError, model_validator, field_validator
from keboola.component.exceptions import UserException


class Authorization(BaseModel):
    """Monday.com authorization."""
    api_key: str = Field(..., alias="#api_key", title="API Token")
    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate(self) -> "Authorization":
        self.api_key = self.api_key.strip()
        if not self.api_key:
            raise ValueError("API token must be provided.")
        return self


class FieldMapping(BaseModel):
    """One mapping row: CSV/Source column -> Monday column_id."""
    source_column: str = Field(..., title="Source Column")
    monday_column_id: Optional[str] = Field("", title="Monday Column ID")

    @model_validator(mode="after")
    def _validate(self) -> "FieldMapping":
        self.source_column = self.source_column.strip()
        self.monday_column_id = (self.monday_column_id or "").strip()
        if not self.source_column:
            raise ValueError("source_column cannot be empty.")
        return self


class UniqueKey(BaseModel):
    """Defines upsert identity: which source column maps to which Monday column_id."""
    source_column: str = Field(..., title="Unique Source Column")
    monday_column_id: str = Field(..., title="Unique Monday Column ID")

    @model_validator(mode="after")
    def _validate(self) -> "UniqueKey":
        self.source_column = self.source_column.strip()
        self.monday_column_id = self.monday_column_id.strip()
        if not self.source_column:
            raise ValueError("unique_key.source_column cannot be empty.")
        if not self.monday_column_id:
            raise ValueError("unique_key.monday_column_id cannot be empty.")
        return self


class SyncOptions(BaseModel):
    """Runtime options."""
    board_id: Optional[Union[str, int]] = Field(None, title="Board ID")
    group_id: Optional[str] = Field("topics", title="Group ID")
    batch_size: int = Field(50, ge=1, le=500, title="Batch Size")

    @field_validator("board_id", mode="before")
    @classmethod
    def _coerce_board_id(cls, v):
        if v is None:
            return None
        return str(v).strip()

    @field_validator("group_id", mode="before")
    @classmethod
    def _normalize_group_id(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    @model_validator(mode="after")
    def _validate(self) -> "SyncOptions":
        # Skip strict validation when config incomplete (UI phase)
        if not self.board_id:
            return self
        if not str(self.board_id).strip():
            raise ValueError("board_id must be provided.")
        return self


class Parameters(BaseModel):
    """Root parameters for Monday.com writer."""
    authorization: Optional[Authorization] = None
    sync_options: Optional[SyncOptions] = None
    unique_key: Optional[UniqueKey] = None
    field_mappings: Optional[List[FieldMapping]] = None

    @model_validator(mode="after")
    def _validate(self) -> "Parameters":
        # Skip if running in partial config (UI autoload or sync actions)
        if not all([self.authorization, self.sync_options, self.unique_key, self.field_mappings]):
            return self

        # Full validation only for run mode
        if not self.field_mappings:
            raise ValueError("field_mappings must contain at least one mapping.")

        srcs = [m.source_column for m in self.field_mappings]
        cols = [m.monday_column_id for m in self.field_mappings if m.monday_column_id]
        if len(set(srcs)) != len(srcs):
            raise ValueError("field_mappings contains duplicate source_column values.")
        if len(set(cols)) != len(cols):
            raise ValueError("field_mappings contains duplicate monday_column_id values.")

        if self.unique_key.source_column not in srcs:
            raise ValueError("unique_key.source_column is not present in field_mappings.")
        if self.unique_key.monday_column_id not in cols:
            raise ValueError("unique_key.monday_column_id is not present in field_mappings.")
        return self

    @property
    def mapping_dict(self) -> Dict[str, str]:
        """{ source_column -> monday_column_id }"""
        return {m.source_column: m.monday_column_id for m in (self.field_mappings or [])}

    @property
    def monday_columns(self) -> List[str]:
        """Ordered list of monday_column_id to be written."""
        return [m.monday_column_id for m in (self.field_mappings or []) if m.monday_column_id]

    @property
    def identity_pair(self) -> Optional[Tuple[str, str]]:
        """(unique_source_column, unique_monday_column_id)"""
        if not self.unique_key:
            return None
        return self.unique_key.source_column, self.unique_key.monday_column_id


class Configuration(BaseModel):
    """Keboola config wrapper."""
    parameters: Optional[Parameters] = None
    action: Optional[str] = Field(default="run")

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            msgs = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Configuration validation error: {', '.join(msgs)}")

    @property
    def auth(self) -> Optional[Authorization]:
        return self.parameters.authorization if self.parameters else None

    @property
    def sync(self) -> Optional[SyncOptions]:
        return self.parameters.sync_options if self.parameters else None

    @property
    def mappings(self) -> Dict[str, str]:
        return self.parameters.mapping_dict if self.parameters else {}

    @property
    def unique(self) -> Optional[Tuple[str, str]]:
        return self.parameters.identity_pair if self.parameters else None
