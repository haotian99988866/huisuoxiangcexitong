ALTER TABLE delivery_people_state
    ADD COLUMN archive_build_mode BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE delivery_media
    ADD COLUMN archive_only BOOLEAN NOT NULL DEFAULT FALSE;
