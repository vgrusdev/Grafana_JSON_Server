import pandas as pd
from typing import List, Optional, Union, Any

def deduplicate_rows(
    rows: List[dict],
    dedup_columns: Optional[List[str]] = None,
    exclude_columns: Optional[List[str]] = None,
    timestamp_column: Optional[str] = "TIMESTAMP",
    keep_timestamp: str = "latest",  # "latest", "earliest", or "first"/"last"
    keep_order: str = "first"  # "first", "last" (used when no timestamp)
) -> List[dict]:
    """
    it is mostly for query_json calls...
    Prometheus does not like duplicate labels in one serie of data, it just ignore whole serie.
    So, we need to dedup rows from database, grouping by labels, except probably TIMESTAMP and Value.
    Dedup column names are needed to be provided for particular call.

    Deduplicate rows based on specified columns with flexible options.
    
    Args:
        rows: List of dictionaries to deduplicate
        dedup_columns: Explicit list of columns to deduplicate by. If None, 
                      uses all columns except excluded ones
        exclude_columns: List of columns to exclude from deduplication logic
        timestamp_column: Name of timestamp column (if exists). None to ignore timestamp
        keep_timestamp: How to handle duplicates when timestamp exists:
                       "latest" - keep row with latest timestamp
                       "earliest" - keep row with earliest timestamp
                       "first" - keep first occurrence (ignore timestamp)
                       "last" - keep last occurrence (ignore timestamp)
        keep_order: How to handle duplicates when no timestamp or when ignore timestamp:
                   "first" - keep first occurrence
                   "last" - keep last occurrence
    
    Returns:
        List of dictionaries with duplicates removed
    """
    if not rows:
        return []
    
    # Convert to DataFrame
    df = pd.DataFrame(rows)
    
    # Determine which columns to use for deduplication
    if dedup_columns is not None:
        # Use explicitly specified columns
        groupby_cols = dedup_columns
    else:
        # Start with all columns
        groupby_cols = list(df.columns)
        
        # Remove excluded columns
        if exclude_columns:
            groupby_cols = [col for col in groupby_cols if col not in exclude_columns]
        
        # Remove timestamp column if specified and not in exclude_columns
        if timestamp_column and timestamp_column in groupby_cols:
            if exclude_columns and timestamp_column in exclude_columns:
                # Keep it if it's in exclude_columns
                pass
            else:
                groupby_cols.remove(timestamp_column)
    
    # Validate columns exist
    missing_cols = [col for col in groupby_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Columns not found in data: {missing_cols}")
    
    # Handle deduplication based on timestamp presence
    if timestamp_column and timestamp_column in df.columns and keep_timestamp in ["latest", "earliest"]:
        # Use timestamp to determine which duplicate to keep
        if keep_timestamp == "latest":
            idx = df.groupby(groupby_cols)[timestamp_column].idxmax()
        else:  # earliest
            idx = df.groupby(groupby_cols)[timestamp_column].idxmin()
        
        deduplicated_df = df.loc[idx]
    else:
        # No timestamp or ignore timestamp - use simple deduplication
        if keep_timestamp in ["first", "last"]:
            keep_method = keep_timestamp
        else:
            keep_method = keep_order
        
        deduplicated_df = df.drop_duplicates(subset=groupby_cols, keep=keep_method)
    
    # Reset index and convert back to list of dicts
    return deduplicated_df.reset_index(drop=True).to_dict('records')


# Helper functions for common use cases
def deduplicate_with_timestamp(
    rows: List[dict],
    timestamp_column: str = "TIMESTAMP",
    exclude_columns: Optional[List[str]] = None,
    keep_latest: bool = True
) -> List[dict]:
    """Helper for deduplication with timestamp column."""
    keep_timestamp = "latest" if keep_latest else "earliest"
    return deduplicate_rows(
        rows=rows,
        timestamp_column=timestamp_column,
        exclude_columns=exclude_columns,
        keep_timestamp=keep_timestamp
    )


def deduplicate_without_timestamp(
    rows: List[dict],
    exclude_columns: Optional[List[str]] = None,
    keep_first: bool = True
) -> List[dict]:
    """Helper for deduplication without timestamp column."""
    keep_order = "first" if keep_first else "last"
    return deduplicate_rows(
        rows=rows,
        timestamp_column=None,
        exclude_columns=exclude_columns,
        keep_order=keep_order
    )


def deduplicate_by_labels(
    rows: List[dict],
    label_columns: List[str],
    timestamp_column: Optional[str] = None,
    keep_latest: bool = True
) -> List[dict]:
    """Helper for deduplication by explicitly specified label columns."""
    if timestamp_column:
        keep_timestamp = "latest" if keep_latest else "earliest"
    else:
        keep_timestamp = "first"
    
    return deduplicate_rows(
        rows=rows,
        dedup_columns=label_columns,
        timestamp_column=timestamp_column,
        keep_timestamp=keep_timestamp
    )