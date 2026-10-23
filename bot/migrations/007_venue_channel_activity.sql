CREATE TABLE IF NOT EXISTS venue_channel_activity (
    venue_id INT PRIMARY KEY,
    channel_id VARCHAR(50) NOT NULL,
    message_id BIGINT NOT NULL,
    message_date DATETIME NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_venue_channel_activity_date (message_date)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Latest Telegram channel post timestamp per venue';
