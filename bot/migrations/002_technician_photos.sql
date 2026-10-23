CREATE TABLE IF NOT EXISTS technician_photos (
    id INT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    duty_date DATE NULL,
    technician_code VARCHAR(16) NULL,
    file_id VARCHAR(255) NOT NULL,
    channel_id VARCHAR(50) NOT NULL,
    msg_id BIGINT NOT NULL,
    ocr_text TEXT,
    candidate_codes TEXT NOT NULL,
    ocr_confidence DECIMAL(6,5) NULL,
    status ENUM('matched', 'pending_review') NOT NULL DEFAULT 'pending_review',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP NULL,
    UNIQUE KEY uq_technician_photo_message (channel_id, msg_id),
    INDEX idx_technician_photo_lookup (venue_id, duty_date, technician_code, status),
    INDEX idx_technician_photo_review (status, created_at)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Technician photos with local OCR matching';
