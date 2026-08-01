ALTER TABLE uploaded_file ADD CONSTRAINT uq_uploaded_file_alias UNIQUE (session_id, alias);
