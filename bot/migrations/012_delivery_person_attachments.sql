CREATE TABLE IF NOT EXISTS delivery_person_notes (
    person_id BIGINT PRIMARY KEY,
    description TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delivery_person_attachments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    person_id BIGINT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    media_type VARCHAR(16) NOT NULL,
    content_type VARCHAR(32) NOT NULL,
    content LONGBLOB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_delivery_attachment (person_id, content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
