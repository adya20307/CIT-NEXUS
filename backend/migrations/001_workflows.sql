CREATE TABLE workflow_profiles (
    subject_id TEXT PRIMARY KEY,
    username TEXT,
    email TEXT,
    roles TEXT[] NOT NULL DEFAULT '{}',
    display_name TEXT NOT NULL DEFAULT '',
    department TEXT,
    faculty_id TEXT,
    student_group_id TEXT,
    phone TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX workflow_profiles_username_idx ON workflow_profiles (lower(username));
CREATE INDEX workflow_profiles_email_idx ON workflow_profiles (lower(email));
CREATE INDEX workflow_profiles_roles_idx ON workflow_profiles USING GIN (roles);

CREATE TABLE timetable_versions (
    id UUID PRIMARY KEY,
    version_number BIGINT NOT NULL UNIQUE,
    dataset_id TEXT NOT NULL,
    dataset_version_id TEXT,
    schedule JSONB NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_proposal_id UUID,
    decision TEXT NOT NULL CHECK (decision IN ('baseline', 'approved'))
);

CREATE TABLE timetable_proposals (
    id UUID PRIMARY KEY,
    proposal_number BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    base_version_id UUID NOT NULL REFERENCES timetable_versions(id),
    dataset_id TEXT NOT NULL,
    dataset_version_id TEXT,
    original_schedule JSONB NOT NULL,
    proposed_schedule JSONB NOT NULL,
    changes JSONB NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    rejection_reason TEXT,
    decision_by TEXT,
    decision_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX timetable_proposals_status_created_idx
    ON timetable_proposals (status, created_at DESC);
CREATE INDEX timetable_proposals_creator_idx
    ON timetable_proposals (created_by, created_at DESC);

CREATE TABLE timetable_current (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    version_id UUID NOT NULL REFERENCES timetable_versions(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE timetable_versions
    ADD CONSTRAINT timetable_versions_source_proposal_fk
    FOREIGN KEY (source_proposal_id) REFERENCES timetable_proposals(id);

CREATE TABLE faculty_schedule_requests (
    id UUID PRIMARY KEY,
    created_by TEXT NOT NULL,
    faculty_id TEXT NOT NULL,
    request_type TEXT NOT NULL CHECK (request_type IN ('leave', 'reschedule', 'availability')),
    session_id TEXT,
    preferred_days TEXT[] NOT NULL DEFAULT '{}',
    preferred_periods INTEGER[] NOT NULL DEFAULT '{}',
    message TEXT NOT NULL,
    interpretation JSONB NOT NULL DEFAULT '{}'::jsonb,
    alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
    base_version_id UUID REFERENCES timetable_versions(id),
    dataset_version_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    decision_by TEXT,
    decision_reason TEXT,
    decision_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (created_by, idempotency_key)
);

CREATE INDEX faculty_schedule_requests_status_idx
    ON faculty_schedule_requests (status, created_at DESC);
CREATE INDEX faculty_schedule_requests_owner_idx
    ON faculty_schedule_requests (created_by, created_at DESC);

CREATE TABLE workflow_notifications (
    id UUID PRIMARY KEY,
    recipient_subject TEXT NOT NULL,
    request_id UUID REFERENCES faculty_schedule_requests(id),
    proposal_id UUID REFERENCES timetable_proposals(id),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (request_id IS NOT NULL OR proposal_id IS NOT NULL)
);

CREATE INDEX workflow_notifications_recipient_idx
    ON workflow_notifications (recipient_subject, created_at DESC);

CREATE TABLE workflow_audit_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_subject TEXT NOT NULL,
    actor_roles TEXT[] NOT NULL DEFAULT '{}',
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    before_state JSONB,
    after_state JSONB,
    result TEXT NOT NULL CHECK (result IN ('success', 'failure')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX workflow_audit_time_idx ON workflow_audit_log (occurred_at DESC);
CREATE INDEX workflow_audit_actor_idx ON workflow_audit_log (actor_subject, occurred_at DESC);
CREATE INDEX workflow_audit_action_idx ON workflow_audit_log (action, occurred_at DESC);
CREATE INDEX workflow_audit_entity_idx ON workflow_audit_log (entity_type, entity_id);

CREATE FUNCTION prevent_workflow_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'workflow audit history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workflow_audit_no_update
    BEFORE UPDATE OR DELETE ON workflow_audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_workflow_audit_mutation();
