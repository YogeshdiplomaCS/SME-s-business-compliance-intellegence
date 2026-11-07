-- SME Compliance & Document Intelligence Platform - MySQL 5.7+ / 8.x schema.
-- database.py runs this file automatically on first start, so you normally do not need to import it by hand.
-- Manual import:  mysql -u root -p < database.sql

CREATE DATABASE IF NOT EXISTS sme_compliance_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE sme_compliance_db;

-- Users
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL,
    mobile VARCHAR(10) NOT NULL,
    password VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    language_preference VARCHAR(10) DEFAULT 'en',
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_extension VARCHAR(10) NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    category VARCHAR(50),
    extracted_text LONGTEXT,
    metadata JSON,
    status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
    KEY idx_documents_user_id (user_id),
    KEY idx_documents_status (status),
    CONSTRAINT fk_documents_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Extracted fields
CREATE TABLE IF NOT EXISTS extracted_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    field_value TEXT NOT NULL,
    confidence_score DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_extracted_info_document_id (document_id),
    CONSTRAINT fk_extracted_document FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Important dates and alert flags
CREATE TABLE IF NOT EXISTS important_dates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    date_type VARCHAR(50) NOT NULL,
    date_value DATE NOT NULL,
    description VARCHAR(255),
    alert_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_important_dates_document_id (document_id),
    KEY idx_important_dates_date_value (date_value),
    CONSTRAINT fk_dates_document FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Search history
CREATE TABLE IF NOT EXISTS search_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    query TEXT NOT NULL,
    search_type VARCHAR(20) DEFAULT 'text',
    results_count INT DEFAULT 0,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_search_history_user_id (user_id),
    CONSTRAINT fk_search_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- E-mail tokens (reserved for link-based verification)
CREATE TABLE IF NOT EXISTS email_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_email_tokens_token (token),
    CONSTRAINT fk_tokens_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
