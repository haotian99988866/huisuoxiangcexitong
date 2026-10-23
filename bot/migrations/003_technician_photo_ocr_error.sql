ALTER TABLE technician_photos
    ADD COLUMN ocr_error VARCHAR(500) NULL AFTER ocr_confidence;
