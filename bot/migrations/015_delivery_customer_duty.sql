CREATE TABLE IF NOT EXISTS delivery_customer_subscriptions (
    person_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    enabled BOOLEAN NOT NULL,
    subscribed_at DATETIME NOT NULL,
    revision INT NOT NULL DEFAULT 1,
    PRIMARY KEY(person_id,user_id),
    FOREIGN KEY(person_id) REFERENCES delivery_people(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delivery_customer_requests (
    request_id CHAR(32) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    request_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delivery_customer_notifications (
    person_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    duty_date DATE NOT NULL,
    shift VARCHAR(16) NOT NULL,
    published_at DATETIME NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL,
    sent_at DATETIME NULL,
    PRIMARY KEY(person_id,user_id,duty_date,shift)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
