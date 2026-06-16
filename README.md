# Payload CMS → Fivetran Connector

A [Fivetran Connector SDK](https://fivetran.com/docs/connectors/connector-sdk) connector that syncs collections from a [Payload CMS](https://payloadcms.com) instance into your data warehouse (Snowflake, BigQuery, etc.).

## How it works

- Fetches your Payload schema from `/api/schema` to understand field types
- Paginates through each configured collection using an `updatedAt` cursor for incremental syncs
- Flattens Payload `group` fields into top-level columns (e.g. `technicalInfo_material`)
- Resolves `relationship` fields to IDs and `hasMany` relationships to arrays of IDs
- Stores `json` and `array` fields as VARIANT (native JSON in your warehouse)
- Checkpoints after each page so a failure mid-sync resumes rather than restarts

## Prerequisites

- Python 3.9+
- A Payload CMS instance with API key auth enabled
- A Fivetran account with a destination configured
- A `/api/schema` endpoint on your Payload instance that returns field type metadata

## Local setup

```bash
python -m venv myenv
source myenv/bin/activate
pip install -r requirements.txt
```

Copy the example config and fill in your values:

```bash
cp configuration.example.json configuration.local.json
```

Run locally to test:

```bash
python connector.py
```

This uses the Fivetran SDK's debug runner, which writes output to `files/warehouse.db` and state to `files/state.json`. Delete those files to reset and do a full re-sync.

## Configuration

| Field | Required | Description |
|---|---|---|
| `payload_url` | Yes | Base URL of your Payload instance, e.g. `https://cms.example.com` |
| `api_key` | Yes | API key from Payload (Users → API Key tab) |
| `collections` | Yes | Comma-separated list of collection slugs to sync, e.g. `products,variants,categories` |
| `users_collection` | No | The Payload users collection slug used in the `Authorization` header. Defaults to `users` |

```json
{
  "payload_url": "https://your-payload-instance.com",
  "api_key": "YOUR_API_KEY_HERE",
  "collections": "categories,products,variants,attributes",
  "users_collection": "users"
}
```

### Generating an API key in Payload

1. Open the Payload admin panel
2. Navigate to **Users** and open your service account user
3. Enable **API Key** and copy the generated key
4. Use that key as `api_key` in your configuration

## Deploying to Fivetran

### 1. Install the Fivetran CLI

```bash
pip install fivetran-connector-sdk
```

### 2. Package the connector

From the project root:

```bash
fivetran deploy --api-key <FIVETRAN_API_KEY> --destination <DESTINATION_NAME> --connection <CONNECTION_NAME>
```

Or to create a zip package for manual upload:

```bash
fivetran pack
```

This produces a `connector.zip` you can upload via the Fivetran UI.

### 3. Configure in the Fivetran UI

1. In the Fivetran dashboard, go to **Connectors → Add connector**
2. Select **Connector SDK** (or find your uploaded connector)
3. Enter the configuration values from the table above
4. Set your sync frequency and click **Save & Test**

### 4. Trigger an initial sync

Fivetran will run an initial full sync on first connection. Subsequent syncs are incremental — only records updated since the last sync are fetched.

## Custom `/api/schema` endpoint

The connector fetches `GET /api/schema` from your Payload instance to determine which fields are typed as `group` — this is what enables the `fieldName_subField` flattening behaviour. **This endpoint is not built into Payload** and must be implemented in your Payload project.

If the endpoint is missing or returns an error, the connector logs a warning and continues — but group fields will **not** be flattened and will instead land in your warehouse as raw JSON objects. The sync itself will not fail.

### What the endpoint must return

```json
{
  "collections": {
    "products": {
      "fields": {
        "technicalInfo": { "type": "group" },
        "price":         { "type": "number" },
        "title":         { "type": "text" }
      }
    },
    "categories": {
      "fields": {
        "meta": { "type": "group" }
      }
    }
  }
}
```

Only `group`-typed fields affect connector behaviour — you can omit other field types or include them, either works.

### Example Payload endpoint (TypeScript)

```ts
// src/endpoints/schema.ts  (add to your Payload config under `endpoints`)
import type { Endpoint } from 'payload'

export const schemaEndpoint: Endpoint = {
  path: '/schema',
  method: 'get',
  handler: async (req) => {
    const { payload } = req

    const collections: Record<string, { fields: Record<string, { type: string }> }> = {}

    for (const collection of payload.config.collections) {
      const fields: Record<string, { type: string }> = {}

      for (const field of collection.fields) {
        // named fields only — unnamed UI fields (rows, collapsibles) have no DB column
        if ('name' in field && field.name) {
          fields[field.name] = { type: field.type }
        }
      }

      collections[collection.slug] = { fields }
    }

    return Response.json({ collections })
  },
}
```

Register it in your Payload config:

```ts
// payload.config.ts
import { schemaEndpoint } from './endpoints/schema'

export default buildConfig({
  // ...
  endpoints: [schemaEndpoint],
})
```

The endpoint does not need authentication — it only exposes field type metadata, not document data. If you prefer to lock it down, the connector sends the same `Authorization` header used for collection requests, so you can verify it using Payload's standard API key middleware.

## Schema handling

Payload field types map to warehouse types as follows:

| Payload type | Warehouse behaviour |
|---|---|
| `text`, `textarea`, `email`, `select`, `radio` | String column |
| `number` | Numeric column |
| `date` | Timestamp column |
| `checkbox` | Boolean column |
| `relationship` (single) | String column (the related document's ID) |
| `relationship` (hasMany) | VARIANT — array of IDs |
| `join` | VARIANT — array of IDs |
| `upload` | String column (the related media document's ID) |
| `group` | Flattened into `fieldName_subField` columns |
| `json` | VARIANT |
| `array` | VARIANT — array of objects |

## License

MIT — see [LICENSE](LICENSE).
