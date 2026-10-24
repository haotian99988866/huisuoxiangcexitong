CREATE TABLE IF NOT EXISTS delivery_media (
    id INT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    channel_id VARCHAR(50) NOT NULL,
    message_id BIGINT NOT NULL,
    media_group_id VARCHAR(64) NULL,
    media_type VARCHAR(10) NOT NULL,
    file_id VARCHAR(255) NOT NULL,
    caption TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_delivery_media_message (channel_id, message_id),
    INDEX idx_delivery_media_venue (venue_id, message_id),
    INDEX idx_delivery_media_group (media_group_id)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Photos and videos posted to the delivery venue Telegram channels';

INSERT IGNORE INTO delivery_media
    (venue_id, channel_id, message_id, media_type, file_id)
SELECT venue_id, channel_id, msg_id, 'photo', file_id
FROM technician_photos
WHERE venue_id IN (7, 8, 9, 10);
