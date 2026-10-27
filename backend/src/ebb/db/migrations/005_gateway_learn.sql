-- Milestone 5: the LLM gateway records every attempt it makes (including failed and backup
-- ones), and the learn phase keeps one lesson per concept plus the exchanges around it.

create table llm_calls (
    id uuid primary key default gen_random_uuid(),
    call_id uuid not null,
    request_id uuid,
    user_id uuid not null references profiles (id) on delete cascade,
    at timestamptz not null,
    role text not null check (role in ('teach', 'quiz', 'eval_writer')),
    endpoint text not null,
    provider text not null,
    model text not null,
    attempt integer not null,
    outcome text not null check (outcome in ('ok', 'invalid', 'error', 'refused')),
    input_tokens integer,
    output_tokens integer,
    reasoning_tokens integer,
    ttft_ms integer,
    total_ms integer not null,
    cost_usd numeric(12, 8),
    error_kind text,
    backup boolean not null default false
);

create index llm_calls_user_at on llm_calls (user_id, at);
create index llm_calls_user_endpoint_at on llm_calls (user_id, endpoint, at);

create table concept_lessons (
    user_id uuid not null references profiles (id) on delete cascade,
    concept_id uuid primary key references concepts (id) on delete cascade,
    markdown text not null,
    citations jsonb not null default '[]',
    web_pages jsonb not null default '[]',
    suggestions jsonb not null default '[]',
    source_summary jsonb not null default '{}',
    model text not null,
    written_at timestamptz not null,
    learned_at timestamptz
);

create table lesson_exchanges (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references profiles (id) on delete cascade,
    concept_id uuid not null references concepts (id) on delete cascade,
    position integer not null,
    kind text not null check (kind in ('check', 'suggested', 'typed')),
    question text not null,
    answer text not null,
    citations jsonb not null default '[]',
    suggestions jsonb not null default '[]',
    asked_at timestamptz not null,
    unique (concept_id, position)
);
