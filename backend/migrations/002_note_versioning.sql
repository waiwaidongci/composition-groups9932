-- 补样建议版本化 / 撤回 / 乐观锁
-- 存量 resample_notes 每行即一条建议的 v1，回填后既有档案保持可读。
-- MySQL 8 手动执行：mysql -u<user> -p composition < backend/migrations/002_note_versioning.sql
ALTER TABLE resample_notes
  ADD COLUMN note_id INT NOT NULL DEFAULT 0 AFTER id,
  ADD COLUMN version INT NOT NULL DEFAULT 1 AFTER note_id,
  ADD COLUMN reviewed_at VARCHAR(64) NOT NULL DEFAULT '' AFTER review_note,
  ADD COLUMN revised_by VARCHAR(100) NOT NULL DEFAULT '' AFTER reviewed_at,
  ADD COLUMN revised_at VARCHAR(64) NOT NULL DEFAULT '' AFTER revised_by,
  ADD COLUMN lock_version INT NOT NULL DEFAULT 0 AFTER revised_at;

-- 存量行以自身主键作为建议链编号，版本均为 1
UPDATE resample_notes SET note_id = id WHERE note_id = 0;

ALTER TABLE resample_notes
  ADD UNIQUE INDEX uq_note_version (note_id, version),
  ADD INDEX ix_resample_notes_note_id (note_id),
  ADD INDEX ix_resample_notes_status (status);
