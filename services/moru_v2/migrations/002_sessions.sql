
CREATE TABLE moru_v2_sessions (
	id_hash VARCHAR(64) NOT NULL, 
	issuer VARCHAR(2048) NOT NULL, 
	subject VARCHAR(512) NOT NULL, 
	expires_at BIGINT NOT NULL, 
	PRIMARY KEY (id_hash)
)

;
