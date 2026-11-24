
CREATE TABLE moru_v2_connections (
	owner VARCHAR(64) NOT NULL, 
	platform VARCHAR(16) NOT NULL, 
	generation VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	state_hash VARCHAR(64), 
	binding_hash VARCHAR(64), 
	state_expires BIGINT, 
	account_id VARCHAR(128), 
	ciphertext BYTEA, 
	key_id VARCHAR(64), 
	expires_at BIGINT, 
	scopes VARCHAR(2048), 
	PRIMARY KEY (owner, platform), 
	CHECK (platform IN ('threads', 'instagram')), 
	CHECK (status IN ('pending', 'exchanging', 'connected', 'disconnected')), 
	CHECK (status != 'connected' OR (ciphertext IS NOT NULL AND key_id IS NOT NULL AND account_id IS NOT NULL AND expires_at IS NOT NULL))
)

;
