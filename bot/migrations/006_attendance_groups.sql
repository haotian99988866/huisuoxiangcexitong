CREATE TABLE IF NOT EXISTS attendance_groups (
    id INT PRIMARY KEY AUTO_INCREMENT,
    venue_id INT NOT NULL,
    duty_date DATE NOT NULL,
    group_type ENUM('roster', 'service') NOT NULL,
    group_name VARCHAR(64) NOT NULL,
    technician_codes TEXT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_attendance_group (
        venue_id, duty_date, group_type, group_name
    ),
    INDEX idx_attendance_groups_lookup (venue_id, duty_date, group_type)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Daily technician roster and service groups';
