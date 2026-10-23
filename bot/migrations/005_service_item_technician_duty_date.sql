ALTER TABLE service_item_technicians
    ADD COLUMN duty_date DATE NULL AFTER service_item_id,
    DROP INDEX uq_service_item_technician,
    ADD UNIQUE KEY uq_service_item_duty_technician (
        service_item_id,
        duty_date,
        technician_code
    ),
    ADD INDEX idx_service_item_duty_date (service_item_id, duty_date);
