import requests
import json
from fivetran_connector_sdk import Connector, Operations as op, Logging as log

# Collections to sync - driven by configuration.json
# Add or remove collections there, no code changes needed

PAYLOAD_URL_KEY = "payload_url"
API_KEY_KEY = "api_key"
COLLECTIONS_KEY = "collections"
USERS_COLLECTION_KEY = "users_collection"
DEFAULT_USERS_COLLECTION = "users"


def extract_value(value):
    if isinstance(value, dict):
        # Paginated hasMany relationship (depth: 0) e.g. {"docs": [512], "hasNextPage": false}
        if "docs" in value and "hasNextPage" in value:
            docs = value["docs"]
            return [item["id"] if isinstance(item, dict) and "id" in item else item for item in docs]
        # Single relationship
        if "id" in value:
            return value["id"]
        # Group/JSON field - returned as-is so flatten_row can expand it
        return value

    # Lists (array fields, plain arrays) — pass through as VARIANT.
    # At depth: 0, hasMany relationships come back as {"docs": [...]} not raw arrays,
    # so there's no case where a list here should be collapsed to IDs.
    return value


def fetch_payload_schema(base_url, headers):
    resp = requests.get(f"{base_url}/api/schema", headers=headers)
    if not resp.ok:
        log.warning(f"Could not fetch schema: {resp.status_code} {resp.text}")
        return {}
    return resp.json()


def get_group_fields(payload_schema, collection):
    """Return field names typed 'group' for a collection — these get flattened."""
    fields = payload_schema.get("collections", {}).get(collection, {}).get("fields", {})
    return {name for name, cfg in fields.items() if isinstance(cfg, dict) and cfg.get("type") == "group"}


def flatten_row(row, group_fields):
    """Expand group-typed fields into top-level columns (field_subkey).
    Sub-field values are passed through extract_value so relationships inside groups resolve correctly.
    JSON fields and all other types pass through unchanged."""
    flat = {}
    for key, value in row.items():
        if key in group_fields and isinstance(value, dict):
            for subkey, subval in value.items():
                flat[f"{key}_{subkey}"] = extract_value(subval)
        else:
            flat[key] = value
    return flat


def sync_collection(collection, base_url, headers, state, group_fields):
    """
    Paginate through a Payload collection and yield upserts + checkpoints.
    Uses updatedAt cursor for incremental syncs.
    """
    last_updated = state.get(f"{collection}_last_updated", "1970-01-01T00:00:00.000Z")
    log.info(f"Syncing collection: {collection}, cursor: {last_updated}")

    page = 1
    has_more = True
    latest_updated_at = last_updated

    while has_more:
        log.info(f"  Fetching {collection} page {page}")
        log.info(f"   - {base_url}/api/{collection}")
        log.info(f"   - {headers}")

        resp = requests.get(
            f"{base_url}/api/{collection}",
            headers=headers,
            params={
                "page": page,
                "limit": 100,
                "where[updatedAt][greater_than]": last_updated,
                "sort": "updatedAt",
                "depth": 0,  # depth 0 = relationships return as IDs only (faster)
            }
        )

        if not resp.ok:
            log.warning(f"  Non-200 response for {collection} page {page}: {resp.status_code} {resp.text}")
            break

        data = resp.json()
        docs = data.get("docs", [])
        log.info(f"  Got {len(docs)} docs from {collection} page {page}")

        for doc in docs:
            row = flatten_row({field: extract_value(val) for field, val in doc.items()}, group_fields)
            yield op.upsert(collection, row)

            # Track the latest updatedAt we've seen
            if doc.get("updatedAt"):
                latest_updated_at = doc["updatedAt"]

        # Checkpoint after each page so we don't re-sync on failure
        if docs:
            new_state = {**state, f"{collection}_last_updated": latest_updated_at}
            yield op.checkpoint(new_state)

        page += 1
        has_more = data.get("hasNextPage", False)

    log.info(f"Finished syncing {collection}, last updatedAt: {latest_updated_at}")


def schema(configuration: dict):
    collections = configuration.get(COLLECTIONS_KEY, "").split(",")
    return [
        {
            "table": collection.strip(),
            "primary_key": ["id"]
        }
        for collection in collections
    ]


def update(configuration: dict, state: dict):
    base_url = configuration[PAYLOAD_URL_KEY].rstrip("/")
    api_key = configuration[API_KEY_KEY]
    collections = configuration.get(COLLECTIONS_KEY, "").split(",")
    users_collection = configuration.get(USERS_COLLECTION_KEY, DEFAULT_USERS_COLLECTION)

    headers = {
        "Authorization": f"{users_collection} API-Key {api_key}",
        "Content-Type": "application/json",
    }

    payload_schema = fetch_payload_schema(base_url, headers)
    log.info(f"Starting sync for collections: {collections}")

    for collection in collections:
        group_fields = get_group_fields(payload_schema, collection)
        log.info(f"Group fields for {collection}: {group_fields}")
        yield from sync_collection(collection, base_url, headers, state, group_fields)

    log.info("Sync complete")


connector = Connector(update=update, schema=schema)

if __name__ == "__main__":
    connector.debug()
