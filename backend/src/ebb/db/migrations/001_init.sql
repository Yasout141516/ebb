-- Milestone 1 schema. Every table carries user_id (review decision E9).
-- On Supabase (milestone 6) a later migration adds profiles.id -> auth.users(id).

create table profiles (
    id uuid primary key,
    display_name text,
    timezone text not null default 'UTC',
    created_at timestamptz not null default now()
);

create table subjects (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    name text not null,
    created_at timestamptz not null default now()
);

create table concepts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    subject_id uuid not null references subjects (id) on delete cascade,
    name text not null,
    description text not null,
    position smallint not null default 0,
    stage smallint not null default 0 check (stage between 0 and 5),
    next_review_at timestamptz,
    last_reviewed_at timestamptz,
    review_count integer not null default 0
);

create table study_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    subject_id uuid not null references subjects (id) on delete cascade,
    mode text not null default 'quiz',
    thread_id text not null unique,
    started_at timestamptz not null default now(),
    ended_at timestamptz
);

create table questions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    session_id uuid not null references study_sessions (id) on delete cascade,
    concept_id uuid not null references concepts (id) on delete cascade,
    type text not null check (type in ('mcq', 'short')),
    difficulty smallint not null check (difficulty between 1 and 3),
    prompt text not null,
    options jsonb,
    reference_answer text not null,
    created_at timestamptz not null default now()
);

create table attempts (
    id uuid primary key,
    user_id uuid not null references profiles (id) on delete cascade,
    question_id uuid not null references questions (id) on delete cascade,
    concept_id uuid not null references concepts (id) on delete cascade,
    answer_text text not null,
    grade text not null check (grade in ('correct', 'partial', 'incorrect')),
    feedback text not null,
    stage_before smallint not null,
    stage_after smallint not null,
    model_used text not null,
    graded_at timestamptz not null
);

create index concepts_due_idx on concepts (user_id, next_review_at);
create index attempts_user_time_idx on attempts (user_id, graded_at);
create index attempts_concept_time_idx on attempts (concept_id, graded_at);
