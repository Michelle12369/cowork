MERGE INTO artifact a
USING (
  SELECT id, ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY created_at) AS rn
  FROM artifact
) s ON (a.id = s.id)
WHEN MATCHED THEN UPDATE SET a.title = 'Version ' || s.rn;
