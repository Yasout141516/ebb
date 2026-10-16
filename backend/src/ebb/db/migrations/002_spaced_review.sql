-- Milestone 2: session activity columns and database-enforced ownership.
-- Composite foreign keys make a child row's user_id match its parent's.

alter table study_sessions
    add column last_active_at timestamptz not null default now(),
    add column pruned_at timestamptz;

update study_sessions s
set last_active_at = coalesce(
    (select max(a.graded_at)
     from attempts a
     join questions q on q.id = a.question_id
     where q.session_id = s.id),
    s.started_at
);

alter table subjects add constraint subjects_id_user_id_key unique (id, user_id);
alter table concepts add constraint concepts_id_user_id_key unique (id, user_id);
alter table study_sessions add constraint study_sessions_id_user_id_key unique (id, user_id);
alter table questions add constraint questions_id_user_id_key unique (id, user_id);

alter table concepts drop constraint concepts_subject_id_fkey;
alter table study_sessions drop constraint study_sessions_subject_id_fkey;
alter table questions drop constraint questions_session_id_fkey;
alter table questions drop constraint questions_concept_id_fkey;
alter table attempts drop constraint attempts_question_id_fkey;
alter table attempts drop constraint attempts_concept_id_fkey;

alter table concepts add constraint concepts_subject_owner_fkey
    foreign key (subject_id, user_id) references subjects (id, user_id) on delete cascade;
alter table study_sessions add constraint study_sessions_subject_owner_fkey
    foreign key (subject_id, user_id) references subjects (id, user_id) on delete cascade;
alter table questions add constraint questions_session_owner_fkey
    foreign key (session_id, user_id) references study_sessions (id, user_id) on delete cascade;
alter table questions add constraint questions_concept_owner_fkey
    foreign key (concept_id, user_id) references concepts (id, user_id) on delete cascade;
alter table attempts add constraint attempts_question_owner_fkey
    foreign key (question_id, user_id) references questions (id, user_id) on delete cascade;
alter table attempts add constraint attempts_concept_owner_fkey
    foreign key (concept_id, user_id) references concepts (id, user_id) on delete cascade;
