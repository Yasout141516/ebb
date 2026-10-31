-- The lesson's unanswered check-yourself questions, kept with it for "Read the lesson again".
alter table concept_lessons add column checks jsonb not null default '[]';
