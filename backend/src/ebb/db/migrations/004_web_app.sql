-- Milestone 4: web app. Subject names are unique per user (ignoring case), study sessions
-- remember which terminal or browser drives them, and LLM calls are counted per user per day.

-- Existing duplicates must go before the index can exist: among one user's subjects whose
-- lower(name) collide, the oldest keeps its name and the others get " (2)", " (3)"... in
-- created_at order, each taking the next suffix no subject of that user already uses.
do $$
declare
    dup record;
    n integer;
begin
    for dup in
        select id, user_id, name from (
            select id, user_id, name, created_at,
                   row_number() over (
                       partition by user_id, lower(name) order by created_at, id
                   ) as rank
            from subjects
        ) ranked
        where rank > 1
        order by created_at, id
    loop
        n := 2;
        while exists (
            select 1 from subjects
            where user_id = dup.user_id and lower(name) = lower(dup.name || ' (' || n || ')')
        ) loop
            n := n + 1;
        end loop;
        update subjects set name = dup.name || ' (' || n || ')' where id = dup.id;
    end loop;
end $$;

create unique index subjects_user_name_key on subjects (user_id, lower(name));

alter table study_sessions add column driver text;
alter table study_sessions add column driver_seen_at timestamptz;

create table llm_usage (
    user_id uuid not null references profiles (id) on delete cascade,
    day date not null,
    calls integer not null default 0,
    primary key (user_id, day)
);
