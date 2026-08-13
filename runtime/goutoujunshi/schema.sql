CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS relationship_profiles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner_key VARCHAR(255) NOT NULL,
    slug VARCHAR(160) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    status ENUM('active', 'archived') NOT NULL DEFAULT 'active',
    current_channel VARCHAR(32) NOT NULL DEFAULT '微信',
    latest_state LONGTEXT NOT NULL,
    known_facts LONGTEXT NOT NULL,
    conservative_judgments LONGTEXT NOT NULL,
    unknowns LONGTEXT NOT NULL,
    response_preferences LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_profile_owner_slug (owner_key, slug),
    KEY ix_profile_owner_name (owner_key, display_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS source_channels (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    kind VARCHAR(32) NOT NULL,
    label VARCHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_relationship_channel (relationship_id, kind),
    CONSTRAINT fk_channel_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS chat_bindings (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(32) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    owner_key VARCHAR(255) NOT NULL,
    relationship_id BIGINT UNSIGNED NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    archived_at DATETIME(6) NULL,
    UNIQUE KEY uq_platform_chat (platform, chat_id),
    KEY ix_binding_relationship (relationship_id, active),
    CONSTRAINT fk_binding_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS relationship_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    source_channel_id BIGINT UNSIGNED NULL,
    event_type ENUM('received', 'sent', 'draft', 'background', 'analysis', 'correction') NOT NULL,
    author_role ENUM('user', 'other', 'assistant', 'system', 'unknown') NOT NULL,
    content LONGTEXT NOT NULL,
    evidence_kind VARCHAR(64) NOT NULL,
    external_message_id VARCHAR(255) NULL,
    dedupe_key CHAR(64) NOT NULL,
    supersedes_event_id BIGINT UNSIGNED NULL,
    metadata JSON NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_event_dedupe (relationship_id, dedupe_key),
    KEY ix_event_recent (relationship_id, occurred_at, id),
    KEY ix_event_type_channel (relationship_id, event_type, source_channel_id, id),
    CONSTRAINT fk_event_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT,
    CONSTRAINT fk_event_channel FOREIGN KEY (source_channel_id)
        REFERENCES source_channels(id) ON DELETE RESTRICT,
    CONSTRAINT fk_event_supersedes FOREIGN KEY (supersedes_event_id)
        REFERENCES relationship_events(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS relationship_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    version INT UNSIGNED NOT NULL,
    snapshot_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_snapshot_version (relationship_id, version),
    CONSTRAINT fk_snapshot_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS import_manifests (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    source_path VARCHAR(1024) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    source_bytes BIGINT UNSIGNED NOT NULL,
    parser_version VARCHAR(32) NOT NULL,
    raw_content LONGTEXT NOT NULL,
    imported_event_count INT UNSIGNED NOT NULL,
    imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_import_sha (source_sha256),
    CONSTRAINT fk_import_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS export_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    status ENUM('pending', 'running', 'done', 'failed') NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(1000) NULL,
    requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    KEY ix_export_pending (status, requested_at),
    CONSTRAINT fk_export_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS relationship_event_search_documents (
    event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    relationship_id BIGINT UNSIGNED NOT NULL,
    source_text LONGTEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    enrichment_json JSON NOT NULL,
    enrichment_text LONGTEXT NOT NULL,
    enrichment_source ENUM('none', 'model') NOT NULL DEFAULT 'none',
    enrichment_version VARCHAR(64) NOT NULL DEFAULT '',
    status ENUM('raw_only', 'enriched') NOT NULL DEFAULT 'raw_only',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    KEY ix_search_document_relationship (relationship_id, status, event_id),
    FULLTEXT KEY ft_search_document_source (source_text) WITH PARSER ngram,
    FULLTEXT KEY ft_search_document_enrichment (enrichment_text) WITH PARSER ngram,
    CONSTRAINT fk_search_document_event FOREIGN KEY (event_id)
        REFERENCES relationship_events(id) ON DELETE RESTRICT,
    CONSTRAINT fk_search_document_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS relationship_event_enrichment_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    event_id BIGINT UNSIGNED NOT NULL,
    relationship_id BIGINT UNSIGNED NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    prompt_version VARCHAR(64) NOT NULL,
    status ENUM('pending', 'running', 'done', 'failed') NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64) NULL,
    last_error VARCHAR(1000) NULL,
    requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_enrichment_job_event (event_id),
    KEY ix_enrichment_job_claim (status, attempts, prompt_version, id),
    KEY ix_enrichment_job_relationship (relationship_id, status, event_id),
    CONSTRAINT fk_enrichment_job_event FOREIGN KEY (event_id)
        REFERENCES relationship_events(id) ON DELETE RESTRICT,
    CONSTRAINT fk_enrichment_job_relationship FOREIGN KEY (relationship_id)
        REFERENCES relationship_profiles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO relationship_event_search_documents(
    event_id,relationship_id,source_text,source_sha256,enrichment_json,
    enrichment_text,enrichment_source,enrichment_version,status
)
SELECT id,relationship_id,content,SHA2(content,256),JSON_OBJECT(),'',
    'none','','raw_only'
FROM relationship_events
WHERE event_type <> 'draft';

INSERT IGNORE INTO relationship_event_enrichment_jobs(
    event_id,relationship_id,source_sha256,prompt_version,status,completed_at
)
SELECT e.id,e.relationship_id,SHA2(e.content,256),
    IF(d.status='enriched' AND d.enrichment_version <> '',d.enrichment_version,'mysql-enrichment-v1'),
    IF(d.status='enriched','done','pending'),
    IF(d.status='enriched',CURRENT_TIMESTAMP(6),NULL)
FROM relationship_events e
JOIN relationship_event_search_documents d ON d.event_id=e.id
WHERE e.event_type <> 'draft';

DROP TABLE IF EXISTS relationship_event_index_jobs;

DROP TABLE IF EXISTS relationship_search_indexes;

CREATE TABLE IF NOT EXISTS control_requests (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    request_kind ENUM('reconcile_routes', 'restart_gateway') NOT NULL,
    payload JSON NOT NULL,
    status ENUM('pending', 'done', 'failed') NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    processed_at DATETIME(6) NULL,
    KEY ix_control_pending (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_memory_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner_key VARCHAR(255) NOT NULL,
    operation ENUM('remember', 'correct', 'forget') NOT NULL,
    category ENUM('identity', 'work_school', 'lifestyle', 'preference', 'goal', 'current_context') NOT NULL,
    content TEXT NOT NULL,
    lifespan ENUM('persistent', 'today', 'week') NOT NULL DEFAULT 'persistent',
    expires_at DATETIME(6) NULL,
    target_event_id BIGINT UNSIGNED NULL,
    evidence_kind VARCHAR(64) NOT NULL,
    source_ref VARCHAR(255) NOT NULL DEFAULT '',
    dedupe_key CHAR(64) NOT NULL,
    metadata JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_user_memory_dedupe (dedupe_key),
    KEY ix_user_memory_current (owner_key, expires_at, id),
    KEY ix_user_memory_target (target_event_id),
    CONSTRAINT fk_user_memory_target FOREIGN KEY (target_event_id)
        REFERENCES user_memory_events(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_migrations(version, description)
VALUES (1, 'initial relationship store');

ALTER TABLE relationship_profiles
    MODIFY current_channel VARCHAR(32) NOT NULL DEFAULT '微信';

UPDATE relationship_profiles
SET current_channel=CONVERT(0xE5BEAEE4BFA1 USING utf8mb4)
WHERE HEX(current_channel)='C3A5C2BEC2AEC3A4C2BFC2A1';

INSERT IGNORE INTO schema_migrations(version, description)
VALUES (2, 'enforce utf8mb4 channel default');

INSERT IGNORE INTO schema_migrations(version, description)
VALUES (3, 'add append-only cross-group user memory');

INSERT IGNORE INTO schema_migrations(version, description)
VALUES (4, 'reserved legacy relationship search migration');

INSERT IGNORE INTO schema_migrations(version, description)
VALUES (5, 'replace vector search with enriched MySQL fulltext documents');
