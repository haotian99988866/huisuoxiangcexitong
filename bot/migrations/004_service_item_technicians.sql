CREATE TABLE IF NOT EXISTS service_item_technicians (
    id INT PRIMARY KEY AUTO_INCREMENT,
    service_item_id INT NOT NULL,
    technician_code VARCHAR(16) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_service_item_technician (service_item_id, technician_code),
    INDEX idx_service_technician_code (technician_code),
    CONSTRAINT fk_service_item_technicians_item
        FOREIGN KEY (service_item_id)
        REFERENCES service_items (id)
        ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Technicians qualified for each service item';
