CREATE TABLE chat_session (
    id         VARCHAR(36)  PRIMARY KEY,
    user_id    VARCHAR(100) NOT NULL,
    title      VARCHAR(200) NOT NULL,
    created_at DATETIME(6)  NOT NULL,
    updated_at DATETIME(6)  NOT NULL
);
CREATE INDEX idx_chat_session_user ON chat_session (user_id, updated_at);

CREATE TABLE chat_message (
    id             VARCHAR(36) PRIMARY KEY,
    session_id     VARCHAR(36) NOT NULL,
    sender         VARCHAR(10) NOT NULL,
    text           TEXT,
    steps_json     TEXT,
    questions_json TEXT,
    artifact_id    VARCHAR(36),
    created_at     DATETIME(6) NOT NULL,
    CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_chat_message_session ON chat_message (session_id, created_at);

CREATE TABLE uploaded_file (
    id            VARCHAR(36)  PRIMARY KEY,
    session_id    VARCHAR(36)  NOT NULL,
    name          VARCHAR(500) NOT NULL,
    alias         VARCHAR(100) NOT NULL,
    storage_key   VARCHAR(500) NOT NULL,
    size_bytes    BIGINT       NOT NULL,
    type          VARCHAR(20)  NOT NULL,
    metadata_json TEXT,
    row_count     BIGINT,
    expired       TINYINT      DEFAULT 0 NOT NULL,
    created_at    DATETIME(6)  NOT NULL,
    CONSTRAINT fk_file_session FOREIGN KEY (session_id) REFERENCES chat_session (id),
    CONSTRAINT uq_uploaded_file_alias UNIQUE (session_id, alias)
);
CREATE INDEX idx_uploaded_file_session ON uploaded_file (session_id);

CREATE TABLE artifact (
    id                   VARCHAR(36)  PRIMARY KEY,
    session_id           VARCHAR(36)  NOT NULL,
    title                VARCHAR(300) NOT NULL,
    raw_html_storage_key VARCHAR(500),
    html_storage_key     VARCHAR(500),
    asset_profile        VARCHAR(40),
    created_at           DATETIME(6)  NOT NULL,
    CONSTRAINT fk_artifact_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_artifact_session ON artifact (session_id);
