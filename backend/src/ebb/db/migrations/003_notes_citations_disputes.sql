-- Milestone 3: uploaded notes, citations and grade disputes.
-- Every new table carries user_id with composite foreign keys to its parent.

alter table attempts add constraint attempts_id_user_id_key unique (id, user_id);

create table documents (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    subject_id uuid not null,
    filename text not null,
    kind text not null check (kind in ('pdf', 'txt', 'md')),
    size_bytes integer not null,
    page_count integer,
    sha256 text not null,
    created_at timestamptz not null default now(),
    unique (id, user_id),
    unique (subject_id, sha256),
    foreign key (subject_id, user_id) references subjects (id, user_id) on delete cascade
);

create table document_chunks (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    document_id uuid not null,
    subject_id uuid not null,
    ordinal integer not null,
    page_start integer,
    page_end integer,
    page_offsets jsonb not null default '[]',
    heading text,
    content text not null,
    search_vector tsvector generated always as (
        to_tsvector('english', coalesce(heading, '') || ' ' || content)
    ) stored,
    unique (id, user_id),
    foreign key (document_id, user_id) references documents (id, user_id) on delete cascade,
    foreign key (subject_id, user_id) references subjects (id, user_id) on delete cascade
);

create index document_chunks_search_idx on document_chunks using gin (search_vector);
create index document_chunks_document_idx on document_chunks (document_id, ordinal);

alter table questions
    add column source text not null default 'general'
        check (source in ('notes', 'web', 'general')),
    add column citations jsonb not null default '[]';

alter table attempts
    add column citations jsonb not null default '[]',
    add column concept_before jsonb,
    add column voided_at timestamptz;

create table grade_disputes (
    id uuid primary key,
    user_id uuid not null references profiles (id) on delete cascade,
    attempt_id uuid not null unique,
    reason text not null check (reason in ('correct', 'ambiguous', 'notes_differ', 'other')),
    explanation text not null,
    original_grade text not null check (original_grade in ('correct', 'partial', 'incorrect')),
    new_grade text check (new_grade in ('correct', 'partial', 'incorrect')),
    outcome text not null check (outcome in ('upheld', 'changed', 'voided')),
    regrade_explanation text not null,
    citations jsonb not null default '[]',
    model_used text not null,
    created_at timestamptz not null default now(),
    foreign key (attempt_id, user_id) references attempts (id, user_id) on delete cascade
);

create index grade_disputes_user_idx on grade_disputes (user_id, created_at);
