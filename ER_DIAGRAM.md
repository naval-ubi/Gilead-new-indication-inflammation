# Database ER Diagram

This diagram reflects the SQLAlchemy tables and declared foreign keys in
[`app/db/models.py`](./app/db/models.py).

```mermaid
erDiagram
    USERS {
        int id PK
        varchar name
        varchar email UK
        varchar password_hash
        varchar role
        json groups
        varchar status
        datetime last_login "nullable"
        json context
        json prefs
    }

    RUNS {
        int id PK
        int user_id FK
        varchar module
        varchar kind
        varchar title
        text question
        varchar mode
        json params
        varchar status
        json steps
        int step_index
        varchar step_label
        json result "nullable"
        text error "nullable"
        int source_count
        varchar project
        boolean saved
        boolean approved
        int parent_run_id FK "nullable"
        int thread_id "nullable, conversation root run id"
        boolean notify
        varchar workflow
        datetime created_at
        datetime updated_at
        boolean deleted
    }

    WORKSPACE_ITEMS {
        int id PK
        int owner_id FK
        varchar title
        varchar item_type
        varchar module
        varchar folder
        varchar status
        text question
        text summary
        json params
        json snapshot "nullable"
        int source_count
        boolean archived
        int run_id FK "nullable"
        datetime created_at
        datetime updated_at
    }

    ITEM_VERSIONS {
        int id PK
        int item_id FK
        varchar label
        varchar note
        json snapshot "nullable"
        datetime created_at
    }

    ITEM_SHARES {
        int id PK
        int item_id FK
        int user_id FK "nullable"
        varchar group "nullable"
        varchar permission
    }

    ITEM_NOTES {
        int id PK
        int item_id FK
        int user_id FK
        text text
        datetime created_at
    }

    PIPELINE_ASSETS {
        int id PK
        varchar name
        varchar sponsor
        varchar modality
        varchar target
        varchar indication
        varchar stage
        varchar activity
        varchar geography
        varchar source
        datetime updated_at
    }

    SCORING_MODELS {
        int id PK
        varchar name
        varchar version
        varchar status
        json criteria
    }

    ASSESSMENTS {
        int id PK
        int owner_id FK
        int model_id FK
        varchar title
        varchar target
        json indications
        json criteria
        json weights
        json ai_scores
        json overrides
        varchar status
        datetime created_at
        datetime updated_at
    }

    SCENARIOS {
        int id PK
        int assessment_id FK
        varchar name
        json weights
        json overrides
        json ranking
        datetime created_at
    }

    CONNECTORS {
        int id PK
        int category
        varchar name UK
        text description
        boolean enabled
        varchar health
        varchar license
        datetime last_sync "nullable"
        json config
        varchar secret_ref "nullable"
    }

    ADMIN_RECORDS {
        int id PK
        varchar kind
        varchar name
        varchar status
        varchar version
        json data
        datetime updated_at
    }

    NOTIFICATIONS {
        int id PK
        int user_id FK
        varchar title
        varchar body
        varchar route
        boolean unread
        datetime created_at
    }

    REVOKED_TOKENS {
        varchar jti PK
        int user_id FK
        datetime expires_at
        datetime revoked_at
    }

    AUDIT_LOG {
        int id PK
        int user_id FK "nullable"
        varchar action
        varchar entity
        varchar entity_id
        json before "nullable"
        json after "nullable"
        datetime created_at
    }

    USERS ||--o{ RUNS : owns
    RUNS o|--o{ RUNS : parent_of
    USERS ||--o{ WORKSPACE_ITEMS : owns
    RUNS o|--o{ WORKSPACE_ITEMS : linked_to
    WORKSPACE_ITEMS ||--o{ ITEM_VERSIONS : has
    WORKSPACE_ITEMS ||--o{ ITEM_SHARES : has
    USERS o|--o{ ITEM_SHARES : shared_with
    WORKSPACE_ITEMS ||--o{ ITEM_NOTES : has
    USERS ||--o{ ITEM_NOTES : writes
    USERS ||--o{ ASSESSMENTS : owns
    SCORING_MODELS ||--o{ ASSESSMENTS : uses
    ASSESSMENTS ||--o{ SCENARIOS : has
    USERS ||--o{ NOTIFICATIONS : receives
    USERS o|--o{ AUDIT_LOG : performs
    USERS ||--o{ REVOKED_TOKENS : signed_out
```

`pipeline_assets`, `connectors`, and `admin_records` have no declared foreign
keys. IDs embedded in JSON values (such as `assessments.overrides`) are not
foreign-key relationships in the schema.
