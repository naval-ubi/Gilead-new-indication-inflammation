CREATE TABLE users (
	id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	email VARCHAR(200) NOT NULL, 
	password_hash VARCHAR(300) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	groups JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_login DATETIME, 
	context JSON NOT NULL, 
	prefs JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE TABLE pipeline_assets (
	id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	sponsor VARCHAR(120) NOT NULL, 
	modality VARCHAR(60) NOT NULL, 
	target VARCHAR(120) NOT NULL, 
	indication VARCHAR(60) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	activity VARCHAR(30) NOT NULL, 
	geography VARCHAR(30) NOT NULL, 
	source VARCHAR(60) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_pipeline_assets_stage ON pipeline_assets (stage);
CREATE INDEX ix_pipeline_assets_modality ON pipeline_assets (modality);
CREATE INDEX ix_pipeline_assets_name ON pipeline_assets (name);
CREATE INDEX ix_pipeline_assets_target ON pipeline_assets (target);
CREATE INDEX ix_pipeline_assets_sponsor ON pipeline_assets (sponsor);
CREATE INDEX ix_pipeline_assets_indication ON pipeline_assets (indication);
CREATE TABLE scoring_models (
	id INTEGER NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	version VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	criteria JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE connectors (
	id INTEGER NOT NULL, 
	category INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	health VARCHAR(20) NOT NULL, 
	license VARCHAR(20) NOT NULL, 
	last_sync DATETIME, 
	config JSON NOT NULL, 
	secret_ref VARCHAR(200), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE admin_records (
	id INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	version VARCHAR(20) NOT NULL, 
	data JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_admin_records_kind ON admin_records (kind);
CREATE TABLE runs (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	module VARCHAR(20) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	question TEXT NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	params JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	steps JSON NOT NULL, 
	step_index INTEGER NOT NULL, 
	step_label VARCHAR(300) NOT NULL, 
	result JSON, 
	error TEXT, 
	source_count INTEGER NOT NULL, 
	project VARCHAR(120) NOT NULL, 
	saved BOOLEAN NOT NULL, 
	approved BOOLEAN NOT NULL, 
	parent_run_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	deleted BOOLEAN NOT NULL, thread_id INTEGER, notify BOOLEAN DEFAULT 0, workflow VARCHAR(40) DEFAULT '', 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(parent_run_id) REFERENCES runs (id)
);
CREATE INDEX ix_runs_module ON runs (module);
CREATE INDEX ix_runs_status ON runs (status);
CREATE INDEX ix_runs_created_at ON runs (created_at);
CREATE INDEX ix_runs_user_id ON runs (user_id);
CREATE TABLE assessments (
	id INTEGER NOT NULL, 
	owner_id INTEGER NOT NULL, 
	model_id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	target VARCHAR(80) NOT NULL, 
	indications JSON NOT NULL, 
	criteria JSON NOT NULL, 
	weights JSON NOT NULL, 
	ai_scores JSON NOT NULL, 
	overrides JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(model_id) REFERENCES scoring_models (id)
);
CREATE INDEX ix_assessments_owner_id ON assessments (owner_id);
CREATE TABLE notifications (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	body VARCHAR(500) NOT NULL, 
	route VARCHAR(40) NOT NULL, 
	unread BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_notifications_created_at ON notifications (created_at);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(300) NOT NULL, 
	entity VARCHAR(40) NOT NULL, 
	entity_id VARCHAR(40) NOT NULL, 
	"before" JSON, 
	"after" JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_audit_log_created_at ON audit_log (created_at);
CREATE TABLE workspace_items (
	id INTEGER NOT NULL, 
	owner_id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	item_type VARCHAR(30) NOT NULL, 
	module VARCHAR(20) NOT NULL, 
	folder VARCHAR(120) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	question TEXT NOT NULL, 
	summary TEXT NOT NULL, 
	params JSON NOT NULL, 
	snapshot JSON, 
	source_count INTEGER NOT NULL, 
	archived BOOLEAN NOT NULL, 
	run_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
);
CREATE INDEX ix_workspace_items_updated_at ON workspace_items (updated_at);
CREATE INDEX ix_workspace_items_owner_id ON workspace_items (owner_id);
CREATE TABLE scenarios (
	id INTEGER NOT NULL, 
	assessment_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	weights JSON NOT NULL, 
	overrides JSON NOT NULL, 
	ranking JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(assessment_id) REFERENCES assessments (id)
);
CREATE INDEX ix_scenarios_assessment_id ON scenarios (assessment_id);
CREATE TABLE item_versions (
	id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	label VARCHAR(20) NOT NULL, 
	note VARCHAR(300) NOT NULL, 
	snapshot JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES workspace_items (id)
);
CREATE INDEX ix_item_versions_item_id ON item_versions (item_id);
CREATE TABLE item_shares (
	id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	user_id INTEGER, 
	"group" VARCHAR(80), 
	permission VARCHAR(10) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES workspace_items (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_item_shares_item_id ON item_shares (item_id);
CREATE TABLE item_notes (
	id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES workspace_items (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_item_notes_item_id ON item_notes (item_id);
CREATE TABLE revoked_tokens (
	jti VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	expires_at DATETIME NOT NULL, 
	revoked_at DATETIME NOT NULL, 
	PRIMARY KEY (jti), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);
CREATE INDEX ix_revoked_tokens_user_id ON revoked_tokens (user_id);
CREATE INDEX ix_runs_thread_id ON runs (thread_id);
