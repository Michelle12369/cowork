CREATE TABLE chat_session (
    id         VARCHAR2(36)  PRIMARY KEY,
    user_id    VARCHAR2(100) NOT NULL,
    title      VARCHAR2(200) NOT NULL,
    created_at TIMESTAMP     NOT NULL,
    updated_at TIMESTAMP     NOT NULL
);
CREATE INDEX idx_chat_session_user ON chat_session (user_id, updated_at);

CREATE TABLE chat_message (
    id          VARCHAR2(36) PRIMARY KEY,
    session_id  VARCHAR2(36) NOT NULL,
    sender      VARCHAR2(10) NOT NULL,
    text        CLOB,
    steps_json  CLOB,
    artifact_id VARCHAR2(36),
    created_at  TIMESTAMP    NOT NULL,
    CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_chat_message_session ON chat_message (session_id, created_at);

CREATE TABLE uploaded_file (
    id            VARCHAR2(36)  PRIMARY KEY,
    session_id    VARCHAR2(36)  NOT NULL,
    name          VARCHAR2(500) NOT NULL,
    alias         VARCHAR2(100) NOT NULL,
    storage_key   VARCHAR2(500) NOT NULL,
    size_bytes    NUMBER(19)    NOT NULL,
    type          VARCHAR2(20)  NOT NULL,
    metadata_json CLOB,
    created_at    TIMESTAMP     NOT NULL,
    CONSTRAINT fk_file_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_uploaded_file_session ON uploaded_file (session_id);

CREATE TABLE artifact (
    id         VARCHAR2(36)  PRIMARY KEY,
    session_id VARCHAR2(36)  NOT NULL,
    title      VARCHAR2(300) NOT NULL,
    html       CLOB,
    created_at TIMESTAMP     NOT NULL,
    CONSTRAINT fk_artifact_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_artifact_session ON artifact (session_id);
