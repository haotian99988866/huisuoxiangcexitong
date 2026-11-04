CREATE TABLE IF NOT EXISTS delivery_people (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    identity_key CHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL DEFAULT '',
    consent_status VARCHAR(16) NOT NULL DEFAULT 'granted',
    merged_into_id BIGINT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_delivery_people_identity (venue_id, identity_key),
    INDEX idx_delivery_people_venue (venue_id, merged_into_id)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Delivery personnel profiles and local face-match consent';

ALTER TABLE delivery_media
    ADD COLUMN person_id BIGINT NULL,
    ADD INDEX idx_delivery_media_person (person_id);

CREATE TABLE IF NOT EXISTS delivery_person_audit (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    person_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL,
    action VARCHAR(32) NOT NULL,
    details TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_delivery_person_audit_person (person_id, created_at)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Audit log for delivery-person consent and profile changes';

CREATE TABLE IF NOT EXISTS delivery_people_state (
    id TINYINT PRIMARY KEY,
    photo_profile_version INT NOT NULL DEFAULT 0
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Delivery personnel profile migration state';

INSERT IGNORE INTO delivery_people_state (id, photo_profile_version)
VALUES (1, 0);
