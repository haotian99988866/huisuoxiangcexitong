ALTER TABLE delivery_media
    ADD COLUMN ocr_text TEXT NULL AFTER caption,
    ADD COLUMN age TINYINT UNSIGNED NULL AFTER ocr_text;
