CREATE TABLE IF NOT EXISTS ktv_room_videos (
    id INT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    channel_id VARCHAR(50) NOT NULL,
    message_id BIGINT NOT NULL,
    file_id VARCHAR(255) NOT NULL,
    room_number VARCHAR(32) NULL,
    caption TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ktv_video_message (channel_id, message_id),
    INDEX idx_ktv_video_room (venue_id, room_number)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='KTV room videos posted to the Telegram channel';

CREATE TABLE IF NOT EXISTS ktv_channel_texts (
    id INT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    channel_id VARCHAR(50) NOT NULL,
    message_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ktv_text_message (channel_id, message_id),
    INDEX idx_ktv_text_venue (venue_id)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='KTV channel text posts used for consumption mode and prices';
