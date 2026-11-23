# Database ER Diagram

Entity-relationship diagrams for the platform database, grouped by domain.
26 tables. Keys: PK primary key, FK foreign key, UK unique. `||--o{` = one-to-many (required), `o|--o{` = optional parent.

A full guide with descriptions, JSON shapes and database commands is in [`docs/Database_ER_Guide.pdf`](./docs/Database_ER_Guide.pdf).

## Identity & access

```mermaid
erDiagram
    ROLES {
        int id PK
        varchar name UK
        varchar description
    }
    PERMISSIONS {
        int id PK
        varchar code UK
        varchar description
    }
    ROLE_PERMISSIONS {
        int role_id PK,FK
        int permission_id PK,FK
    }
    USERS {
        int id PK
        varchar name
        varchar email UK
        varchar password_hash
        int role_id FK
        varchar status
        datetime last_login "nullable"
        json context
        json prefs
    }
    GROUPS {
        int id PK
        varchar name UK
        varchar description
    }
    USER_GROUPS {
        int user_id PK,FK
        int group_id PK,FK
    }
    REVOKED_TOKENS {
        varchar jti PK
        int user_id FK
        datetime expires_at
        datetime revoked_at
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

    ROLES ||--o{ ROLE_PERMISSIONS : grants
    PERMISSIONS ||--o{ ROLE_PERMISSIONS : granted_by
    ROLES ||--o{ USERS : has_role
    USERS o|--o{ AUDIT_LOG : performs
    USERS ||--o{ NOTIFICATIONS : receives
    USERS ||--o{ REVOKED_TOKENS : signed_out
    USERS ||--o{ USER_GROUPS : member_of
    GROUPS ||--o{ USER_GROUPS : has_member
```

## Research runs & workspace

```mermaid
erDiagram
    USERS {
        int id PK
        varchar name
        varchar email UK
        varchar password_hash
        int role_id FK
        varchar status
        datetime last_login "nullable"
        json context
        json prefs
    }
    GROUPS {
        int id PK
        varchar name UK
        varchar description
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
        int thread_id "nullable"
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
        int group_id FK "nullable"
        varchar permission
    }
    ITEM_NOTES {
        int id PK
        int item_id FK
        int user_id FK
        text text
        datetime created_at
    }

    USERS ||--o{ RUNS : owns
    RUNS o|--o{ RUNS : follow_up_of
    USERS ||--o{ WORKSPACE_ITEMS : owns
    RUNS o|--o{ WORKSPACE_ITEMS : saved_from
    WORKSPACE_ITEMS ||--o{ ITEM_NOTES : has
    USERS ||--o{ ITEM_NOTES : writes
    WORKSPACE_ITEMS ||--o{ ITEM_SHARES : has
    USERS o|--o{ ITEM_SHARES : shared_with
    GROUPS o|--o{ ITEM_SHARES : shared_with
    WORKSPACE_ITEMS ||--o{ ITEM_VERSIONS : has
```

## Prioritization scoring

```mermaid
erDiagram
    USERS {
        int id PK
        varchar name
        varchar email UK
        varchar password_hash
        int role_id FK
        varchar status
        datetime last_login "nullable"
        json context
        json prefs
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
        varchar status
        datetime created_at
        datetime updated_at
    }
    ASSESSMENT_OVERRIDES {
        int id PK
        int assessment_id FK
        varchar indication
        varchar criterion_key
        int score "nullable"
        text justification
        int user_id FK
        datetime created_at
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

    USERS ||--o{ ASSESSMENTS : owns
    SCORING_MODELS ||--o{ ASSESSMENTS : template_of
    ASSESSMENTS ||--o{ ASSESSMENT_OVERRIDES : has
    USERS ||--o{ ASSESSMENT_OVERRIDES : overrides
    ASSESSMENTS ||--o{ SCENARIOS : has
```

## Pipeline catalogue

```mermaid
erDiagram
    SPONSORS {
        int id PK
        varchar name UK
    }
    MODALITIES {
        int id PK
        varchar name UK
    }
    TARGETS {
        int id PK
        varchar name UK
    }
    INDICATIONS {
        int id PK
        varchar code UK
        varchar name
    }
    DRUG_ASSETS {
        int id PK
        varchar name
        int sponsor_id FK
        int modality_id FK
        int target_id FK
    }
    PIPELINE_PROGRAMS {
        int id PK
        int asset_id FK
        int indication_id FK
        varchar stage
        varchar activity
        varchar geography
        varchar source
        datetime updated_at
    }

    SPONSORS ||--o{ DRUG_ASSETS : develops
    MODALITIES ||--o{ DRUG_ASSETS : modality_of
    TARGETS ||--o{ DRUG_ASSETS : target_of
    DRUG_ASSETS ||--o{ PIPELINE_PROGRAMS : developed_in
    INDICATIONS ||--o{ PIPELINE_PROGRAMS : studied_in
```

## Configuration (no foreign keys)

```mermaid
erDiagram
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

```

## Notes

- `users` and `groups` appear in several diagrams because many domains reference them.
- `runs.thread_id` groups the turns of a conversation (the first turn's id). It is a logical link without a foreign-key constraint.
- `role_permissions` and `user_groups` are many-to-many link tables with composite primary keys.
- An `item_shares` row targets either a user (`user_id`) or a group (`group_id`), never both.
- `assessment_overrides.indication` / `criterion_key` refer to values inside the assessment's own `indications` / `criteria` JSON (the assessment is a working copy of a scoring model).
- JSON columns hold snapshots and flexible payloads (run params/results, saved snapshots, scenario snapshots, criteria templates, user context and preferences). IDs inside JSON are not enforced by the database.
