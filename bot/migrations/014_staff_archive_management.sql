-- Startup also checks and adds these columns; do not repeat ALTER after automatic upgrade.
ALTER TABLE staff_members
    ADD COLUMN description TEXT NULL,
    ADD COLUMN description_revision INT NOT NULL DEFAULT 0,
    ADD COLUMN deleted_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS staff_archive_audit (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    staff_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL,
    action VARCHAR(32) NOT NULL,
    description_length INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX staff_archive_actions(staff_id,id)
) ENGINE=InnoDB;
